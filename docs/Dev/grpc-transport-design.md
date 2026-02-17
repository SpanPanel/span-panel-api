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

`get_snapshot()` is the **primary interface** between the library and the HA integration. It is available on all transport clients and returns a `SpanPanelSnapshot` containing the current state. Fields not supported by a transport are `None`.

The integration should call `get_snapshot()` exclusively and never use generation-specific client methods (OpenAPI calls, gRPC trait calls) directly. This keeps the integration insulated from both transport implementations.

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

### What `get_snapshot()` does per transport

| Transport                | Implementation                                                                                                                                                                                                       |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SpanPanelClient` (Gen2) | Fires `get_status()`, `get_panel_state()`, `get_circuits()`, `get_storage_soe()` concurrently; maps OpenAPI types to `SpanPanelSnapshot`. Individual methods are internal — callers should not invoke them directly. |
| `SpanGrpcClient` (Gen3)  | Reads the in-memory `PanelData` cache the streaming loop maintains. No I/O — safe and cheap to call from a push-update callback.                                                                                     |

---

## Gen3 gRPC Implementation Notes

- **No authentication**: Gen3 panels accept connections on port 50065 without any token or credential.
- **Manual protobuf**: The client uses hand-written varint/field parsing to avoid requiring generated stubs — only `grpcio` is needed.
- **Push streaming**: After `start_streaming()`, the client calls registered callbacks on every `Subscribe` notification. Use `get_snapshot()` inside a callback to read the latest data.
- **Circuit discovery**: On `connect()`, `GetInstances` is called to collect trait 16 IIDs (circuit names) and trait 26 IIDs (power metrics) independently. Both lists are sorted and deduplicated, then paired by position to build the circuit map.
  `GetRevision` on trait 16 is then called for each circuit using its discovered trait 16 IID. See _Circuit IID Mapping Bug_ below.

---

## Circuit IID Mapping Bug — Fixed

**Reported**: PR #169 comment, MLO48 user (`cecilkootz`). Circuit names were paired with the wrong power readings.

**Root cause — offset assumption**: The original `_parse_instances()` computed circuit position as `circuit_id = instance_id - METRIC_IID_OFFSET` where `METRIC_IID_OFFSET = 27`. This was reverse-engineered from one MAIN40 where trait 26 IIDs happened to be
28–52 (offset exactly 27). On the MLO48, trait 26 IIDs were `[2, 35, 36, 37, …]` — the offset varies, so most computed `circuit_id` values were negative or > 50 and were silently discarded. Result: no circuits discovered on the MLO48.

**Root cause — name IID assumption**: `_get_circuit_name(circuit_id)` passed the positional `circuit_id` as the GetRevision `instance_id`. On the MAIN40 this accidentally worked because trait 16 IIDs happened to equal circuit positions (1, 2, 3, …). The
MLO48 has non-contiguous trait 16 IIDs (skipping positions 20, 22, 33), so names were fetched from wrong or nonexistent instances.

**Fix** (`grpc/client.py`, `grpc/models.py`, `grpc/const.py`):

- `_parse_instances()` now collects trait 16 IIDs and trait 26 IIDs into two separate lists during a single `GetInstances` pass. Both lists are sorted and deduplicated, then **paired by position**: `circuit_id = idx + 1` regardless of actual IID values.
- `CircuitInfo` gains a `name_iid` field (the trait 16 instance ID). `_fetch_circuit_names()` uses `info.name_iid` for each GetRevision call instead of the positional circuit_id.
- `_metric_iid_to_circuit: dict[int, int]` is built at connect time as a reverse map from trait 26 IID → circuit_id. `_decode_and_store_metric()` uses O(1) dict lookup instead of the broken `iid - METRIC_IID_OFFSET` arithmetic.
- `METRIC_IID_OFFSET` removed from `grpc/const.py` — the constant embodied the wrong assumption.

This fix is panel-model-agnostic: MAIN40, MLO48, and any future Gen3 variant are handled correctly regardless of how their firmware assigns IID values.

---

## Hardware Validation Required

The following items are implemented but **untested against real Gen3 hardware** (MLO48 / MAIN40). They were derived from PR #169 (`Griswoldlabs:gen3-grpc-support`) which demonstrated connectivity but whose transport code was not merged.

| Item                            | File             | What to validate                                                                                                         |
| ------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `connect()` + circuit discovery | `grpc/client.py` | `GetInstances` response parses correctly; circuits populated via positional pairing; `name_iid` and `metric_iid` correct |
| Streaming loop                  | `grpc/client.py` | `Subscribe` stream delivers notifications; `_metric_iid_to_circuit` lookup resolves correctly; callbacks fire on updates |
| Protobuf field IDs              | `grpc/const.py`  | Trait IDs 15/16/17/26/27/31, `VENDOR_SPAN`, `PRODUCT_GEN3_PANEL`, `MAIN_FEED_IID` are correct for production firmware    |
| `_decode_main_feed()`           | `grpc/client.py` | Field 14 in `Subscribe` notification contains main feed metrics; power/voltage/current parse correctly                   |
| `_decode_circuit_metrics()`     | `grpc/client.py` | Per-circuit metrics (power, voltage A/B, dual-phase detection) decode correctly                                          |
| `get_snapshot()` conversion     | `grpc/client.py` | `SpanCircuitSnapshot` fields populated with correct values from live data                                                |
| Auto-detection                  | `factory.py`     | Gen2 HTTP probe completes before Gen3 gRPC probe when both fail; Gen3 detected on port 50065 when panel is present       |

If any field IDs or message structure differs from production firmware, `grpc/const.py` and the decode functions in `grpc/client.py` are the only files that need updating — no protocol or model changes required.

---

## Developer Setup for Hardware Testing

The gRPC protobuf decoders must be validated against a live Gen3 panel. Publishing the library between every decode fix is impractical — use an **editable install** so changes to `grpc/client.py` or `grpc/const.py` are picked up on the next integration
reload without reinstalling anything.

### Prerequisites

- Gen3 panel (MLO48 or MAIN40) reachable on port 50065
- Python 3.12+, `git`
- Both repos cloned side-by-side: `span-panel-api/` (this library) and `span/` (HA integration)

### Option A — Local HA Core (fastest iteration)

```bash
# 1. Create a dedicated HA environment (once)
python -m venv ha-venv
source ha-venv/bin/activate
pip install homeassistant

# 2. Install the library in editable mode (once; survives HA restarts)
pip install -e /path/to/span-panel-api[grpc]

# 3. Confirm editable install — Location must be a file path, not site-packages
pip show span-panel-api

# 4. Link the integration into HA config
mkdir -p ~/ha-config/custom_components
ln -s /path/to/span/custom_components/span_panel ~/ha-config/custom_components/span_panel

# 5. Run HA
hass -c ~/ha-config
```

After the editable install, any edit to `src/span_panel_api/grpc/client.py` or `grpc/const.py` is live on the next integration reload — no `pip install` needed.

### Option B — HA in Docker (Home Assistant Container)

```bash
# 1. Start HA with both repos volume-mounted
docker run -d \
  --name homeassistant \
  -v /path/to/span-panel-api:/span-panel-api \
  -v /path/to/span/custom_components/span_panel:/config/custom_components/span_panel \
  -v ~/ha-config:/config \
  --network host \
  ghcr.io/home-assistant/home-assistant:stable

# 2. Install the library in editable mode inside the container
docker exec homeassistant pip install -e /span-panel-api[grpc]

# 3. Confirm
docker exec homeassistant pip show span-panel-api

# 4. Restart to pick up the new library
docker restart homeassistant
```

The editable install persists across container restarts. If the container is **removed and recreated** (`docker rm`), re-run step 2.

### Enable Debug Logging

Add to `~/ha-config/configuration.yaml` before starting HA:

```yaml
logger:
  default: warning
  logs:
    custom_components.span_panel: debug
```

Key log messages to watch for:

| Log message                                             | Meaning                                       |
| ------------------------------------------------------- | --------------------------------------------- |
| `Span Panel coordinator: Gen3 push-streaming mode`      | Capability detection succeeded                |
| `Registered Gen3 push-streaming coordinator callback`   | Streaming wired up correctly                  |
| `Gen3 push update failed: …`                            | Push callback raised — check the error detail |
| `SPAN Panel update cycle completed` in rapid succession | Push-driven updates are flowing               |

### Iteration Workflow

1. **Edit** `src/span_panel_api/grpc/client.py` or `grpc/const.py`
2. **Reload** the integration: HA UI → Settings → Devices & Services → SPAN Panel → ⋮ → Reload
3. **Check logs** — no HA restart required for most decode changes
4. Commit only after the log output confirms correct circuit count and live power readings

### Diagnostic Symptom Table

| Symptom                           | Where to look                                                      |
| --------------------------------- | ------------------------------------------------------------------ |
| No circuits discovered            | `_parse_instances()` — check `GetInstances` trait filtering        |
| Circuits found but power stays 0  | `_decode_and_store_metric()` — check field indices                 |
| Circuit names wrong or swapped    | `_get_circuit_name_by_iid()`, `CircuitInfo.name_iid`               |
| No push updates (entities frozen) | `_streaming_loop()` — check `Subscribe` stream delivery            |
| Connection refused on port 50065  | `grpc/const.py` — verify `VENDOR_SPAN`, `PRODUCT_GEN3_PANEL`, port |
| Wrong circuit count               | `_parse_instances()` — count of trait 26 IIDs vs physical circuits |

### What a Working Integration Looks Like

- Circuit count matches panel model (MAIN40 → 40, MLO48 → 48)
- Power readings update within seconds of real load changes
- `main_power_w` approximately equals the sum of active circuit powers
- Log shows `SPAN Panel update cycle completed` on each push notification (not on a fixed polling cadence)

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

### Phase 2a — Snapshot migration — **Complete**

The integration's domain objects are now populated exclusively from `get_snapshot()`, removing all OpenAPI type dependencies above the library boundary:

- **`span_panel_api.py`**: `update()` calls `client.get_snapshot()` and maps the returned `SpanPanelSnapshot` into the integration's domain objects.
- **`span_panel.py`**: Populated from `SpanPanelSnapshot` fields rather than OpenAPI response objects.
- **`span_panel_circuit.py`**: Wraps `SpanCircuitSnapshot` instead of the OpenAPI `Circuit` type. Entity classes required no changes.

Entities read from `SpanCircuitSnapshot`-backed properties, so overlapping Gen3 metrics (power) required no additional entity work.

### Phase 2b — Gen3 runtime wiring — **Complete**

Push-streaming was folded into the existing `SpanPanelCoordinator` rather than a separate subclass. Key changes:

- **`span_panel_api.py`**:

  - `_create_client()` Gen3 branch instantiates `SpanGrpcClient` when `CONF_PANEL_GENERATION == "gen3"`; `_client` is typed as `SpanPanelClientProtocol | None`.
  - Added `register_push_callback(cb)` — delegates to `client.register_callback()` when the client satisfies `StreamingCapableProtocol`; returns `None` otherwise. This keeps callers from accessing `_client` directly.

- **`coordinator.py`** (`SpanPanelCoordinator`):

  - Detects `PanelCapability.PUSH_STREAMING in span_panel.api.capabilities` at `__init__` time.
  - Gen3: passes `update_interval=None` to `DataUpdateCoordinator` (disables the polling timer), then calls `_register_push_callback()`.
  - Gen2: passes `update_interval=timedelta(seconds=scan_interval_seconds)` as before.
  - `_on_push_data()` — sync callback invoked by the gRPC stream; guards against stacking concurrent async tasks with a `_push_update_pending` flag.
  - `_async_push_update()` — async task that calls `span_panel.update()` then `async_set_updated_data(span_panel)`, driving entity refreshes without a polling cycle.
  - `async_shutdown()` — calls the push unregister callable before delegating to `super().async_shutdown()`.

- **`__init__.py`**: A single `SpanPanelCoordinator` is created for both Gen2 and Gen3; no coordinator selection logic needed because the constructor self-configures based on capabilities.

- **`span_panel_api.py`**: `SpanPanelApi.__init__` normalises `_panel_generation` to `"gen2"` whenever `simulation_mode=True`. `SpanGrpcClient` has no simulation infrastructure — simulation is Gen2 `SpanPanelClient`-only. This means the generation dropdown
  in the config flow has no effect when simulation is checked; the correct transport is selected automatically.

- **Config entry migration (v1 → v2)**: The v1→v2 migration now stamps `CONF_PANEL_GENERATION: "gen2"` onto existing entries that lack the field. All v1 entries pre-date Gen3 support and are definitively Gen2.

- **`sensors/factory.py`**: Gen3-only sensor entities — voltage, current, apparent power, reactive power, frequency, power factor per circuit — are created only when the corresponding `SpanCircuitSnapshot` field is non-`None` in the first snapshot.
