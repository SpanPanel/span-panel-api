# gRPC Transport Interface Design

## Context

Gen3 SPAN panels (MLO48 / MAIN40) communicate via gRPC on port 50065 rather than the OpenAPI/HTTP interface used by Gen2. This document describes the transport-abstraction layer added to `span-panel-api` to support both generations behind a common
interface.

---

## Key Differences Between Generations

| Feature                      | Gen2 (OpenAPI/HTTP) | Gen3 (gRPC)   |
| ---------------------------- | ------------------- | ------------- |
| Circuit relay control        | ✅                  | ❌            |
| Circuit priority control     | ✅                  | ❌            |
| Energy history (Wh)          | ✅                  | ❌            |
| Battery / storage SOE        | ✅                  | ❌            |
| JWT authentication           | ✅                  | ❌ (no auth)  |
| Solar / feedthrough data     | ✅                  | ❌            |
| DSM state                    | ✅                  | ❌            |
| Hardware status (door, etc.) | ✅                  | ❌            |
| Real-time power metrics      | ✅ (polled)         | ✅ (streamed) |
| Push streaming               | ❌                  | ✅            |

---

## Architecture: Protocol + Capability Advertisement

Two complementary mechanisms work together:

### 1. `PanelCapability` Flags

Runtime advertisement of what a client supports. The HA integration reads these at setup time to enable/disable entity platforms before any entities are created.

```python
caps = client.capabilities
if PanelCapability.RELAY_CONTROL in caps:
    platforms.append("switch")          # circuit switches
if PanelCapability.BATTERY in caps:
    platforms.append("battery_sensor")
if PanelCapability.PRIORITY_CONTROL in caps:
    platforms.append("select")          # priority selects
if PanelCapability.PUSH_STREAMING in caps:
    # Use push coordinator instead of polling coordinator
    ...
```

**Gen2 default capabilities**: `GEN2_FULL` — all flags except `PUSH_STREAMING`.

**Gen3 initial capabilities**: `GEN3_INITIAL` — `PUSH_STREAMING` only. Additional capabilities will be added as the Gen3 API matures.

### 2. Protocol Hierarchy

Static typing via `typing.Protocol` for type-safe dispatch:

```text
SpanPanelClientProtocol          # Core: capabilities + connect + close + ping + get_snapshot
    ├── AuthCapableProtocol      # Gen2: authenticate(), set_access_token()
    ├── CircuitControlProtocol   # Gen2: set_circuit_relay(), set_circuit_priority()
    ├── EnergyCapableProtocol    # Gen2: get_storage_soe()
    └── StreamingCapableProtocol # Gen3: register_callback(), start/stop_streaming()
```

All protocols use `@runtime_checkable`, enabling `isinstance()` narrowing:

```python
if isinstance(client, CircuitControlProtocol):
    await client.set_circuit_relay(circuit_id, "OPEN")
```

**Design intent**: `capabilities` is for _runtime_ entity platform gating at setup time. The Protocol mixins are for _static type narrowing_ within methods that need to call optional features.

---

## Module Structure

```text
src/span_panel_api/
├── __init__.py        — public exports (updated)
├── client.py          — SpanPanelClient (Gen2 OpenAPI/HTTP) + protocol conformance
├── exceptions.py      — + SpanPanelGrpcError, SpanPanelGrpcConnectionError
├── factory.py         — create_span_client() factory + auto-detection
├── models.py          — PanelCapability, PanelGeneration, SpanPanelSnapshot, SpanCircuitSnapshot
├── protocol.py        — SpanPanelClientProtocol + capability Protocol mixins
├── grpc/              — Gen3 gRPC subpackage (requires grpcio)
│   ├── __init__.py
│   ├── client.py      — SpanGrpcClient
│   ├── models.py      — CircuitInfo, CircuitMetrics, PanelData
│   └── const.py       — port 50065, trait IDs, vendor/product IDs
├── phase_validation.py — (unchanged)
├── simulation.py       — (unchanged)
└── generated_client/   — (unchanged)
```

---

## `create_span_client()` Auto-Detection

```python
from span_panel_api import create_span_client, PanelGeneration

# Force Gen2
client = await create_span_client(host, panel_generation=PanelGeneration.GEN2)

# Force Gen3 (requires pip install span-panel-api[grpc])
client = await create_span_client(host, panel_generation=PanelGeneration.GEN3)

# Auto-detect (tries Gen2 HTTP then Gen3 gRPC)
client = await create_span_client(host)
```

Auto-detection order:

1. Probe Gen2 via `SpanPanelClient.ping()` (HTTP status endpoint)
2. Probe Gen3 via `SpanGrpcClient.test_connection()` (gRPC GetInstances)
3. Raise `SpanPanelConnectionError` if neither responds

---

## Installation

```bash
# Gen2 only (default)
pip install span-panel-api

# Gen2 + Gen3 gRPC support
pip install span-panel-api[grpc]
```

---

## Unified Snapshot

`get_snapshot()` is available on all transport clients and returns a `SpanPanelSnapshot` containing the current state. Fields not supported by a transport are `None`.

```python
snapshot = await client.get_snapshot()

# Available for both Gen2 and Gen3
print(snapshot.panel_generation)   # PanelGeneration.GEN2 or .GEN3
print(snapshot.serial_number)
print(snapshot.main_power_w)
for cid, circuit in snapshot.circuits.items():
    print(f"{circuit.name}: {circuit.power_w} W")

# Gen2-only (None for Gen3)
print(snapshot.battery_soe)
print(snapshot.dsm_state)
print(snapshot.grid_power_w)

# Gen3-only (None for Gen2)
print(snapshot.main_voltage_v)
print(snapshot.main_frequency_hz)
```

---

## Gen3 gRPC Implementation Notes

- **No authentication**: Gen3 panels accept connections on port 50065 without any token or credential.
- **Manual protobuf**: The client uses hand-written varint/field parsing to avoid requiring generated stubs — only `grpcio` is needed.
- **Push streaming**: After `start_streaming()`, the client calls registered callbacks on every `Subscribe` notification. Use `get_snapshot()` inside a callback to read the latest data.
- **Circuit discovery**: On `connect()`, `GetInstances` is called to discover all circuit IIDs (trait 26, offset 27), then `GetRevision` on trait 16 fetches the human-readable name for each circuit.

---

## How the HA Integration Uses This

### Phase 1 — Implemented (span v1.3.2, span-panel-api v1.1.15)

1. **`span_panel_api.py`**: Added `capabilities` property that delegates to `self._client.capabilities` when the client exists, falling back to `GEN2_FULL`. The underlying `_client` is still `SpanPanelClient`; full migration to `SpanPanelClientProtocol` is
   Phase 2.

2. **`__init__.py` / platform setup**: `_BASE_PLATFORMS` (`BINARY_SENSOR`, `SENSOR`) always loaded; `SWITCH` added when `RELAY_CONTROL` present, `SELECT` added when `PRIORITY_CONTROL` present. Active platform list stored in `hass.data` per entry so unload
   is exact.

3. **`config_flow.py`**: Panel generation dropdown (auto / gen2 / gen3) added to the user form. `CONF_PANEL_GENERATION` stored in config entry `data`. Gen3 path (`async_step_gen3_setup`) probes `SpanGrpcClient` directly, skips JWT auth, and jumps to entity
   naming. Gen2/auto path unchanged.

4. **`sensors/factory.py`**: Capability-gated sensor groups — DSM status sensors require `DSM_STATE`; panel and circuit energy sensors require `ENERGY_HISTORY`; hardware status sensors require `HARDWARE_STATUS`; battery sensor requires `BATTERY`; solar
   sensors require `SOLAR`. Panel and circuit power sensors are always created.

5. **`const.py`**: Added `CONF_PANEL_GENERATION = "panel_generation"`.

### Phase 2 — Deferred (requires Gen3 hardware)

- **`coordinator.py`**: `SpanPanelPushCoordinator` — calls `client.register_callback()` and `start_streaming()`, drives entity updates without polling.
- **`span_panel_api.py`**: `_create_client()` Gen3 branch — instantiates `SpanGrpcClient` when `CONF_PANEL_GENERATION == "gen3"`; widens `_client` to `SpanPanelClientProtocol | None`.
- **`sensors/factory.py`**: Gen3 power-metric sensor entities (voltage, current, apparent power, reactive power, frequency, power factor per circuit).
