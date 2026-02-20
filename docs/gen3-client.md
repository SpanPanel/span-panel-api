# Gen3 gRPC Client

The Gen3 client (`SpanGrpcClient`) communicates with next-generation SPAN panels (MAIN40, MLO48) using gRPC on port 50065. No authentication is required.

> **Note**: For integrations that should work with both generations, prefer `create_span_client` from the factory module — it auto-detects the panel generation. Use the Gen3 client directly only when targeting Gen3 hardware exclusively.

## Prerequisites

Gen3 support requires the optional `grpcio` dependency:

```bash
pip install span-panel-api[grpc]
```

## Gen3 Panel Behaviour

| Characteristic           | Detail                                     |
| ------------------------ | ------------------------------------------ |
| Hardware                 | MAIN40, MLO48                              |
| Transport                | gRPC (port 50065)                          |
| Authentication           | None                                       |
| Circuit discovery        | `GetInstances` / `GetRevision` trait calls |
| Power metrics            | Push-streamed via `Subscribe`              |
| Relay / priority control | Not supported                              |
| Energy history           | Not supported                              |
| Battery / storage SOE    | Not supported                              |

## Connection and Usage

### Using the Factory (Recommended)

```python
import asyncio
from span_panel_api import create_span_client, PanelGeneration

async def main():
    # Auto-detect generation
    client = await create_span_client("192.168.1.100")

    # Or force Gen3 explicitly
    client = await create_span_client("192.168.1.100", panel_generation=PanelGeneration.GEN3)

    await client.connect()

    snapshot = await client.get_snapshot()
    print(f"Panel serial: {snapshot.serial_number}")
    for cid, circuit in snapshot.circuits.items():
        print(f"  [{cid}] {circuit.name}: {circuit.power_w:.0f} W")

    await client.close()

asyncio.run(main())
```

### Direct Client Usage

```python
import asyncio
from span_panel_api.grpc.client import SpanGrpcClient

async def main():
    client = SpanGrpcClient(host="192.168.1.100", port=50065)

    connected = await client.connect()
    if not connected:
        print("Failed to connect")
        return

    print(f"Connected — {len(client.data.circuits)} circuits discovered")

    # One-shot snapshot
    snapshot = await client.get_snapshot()
    for cid, circuit in snapshot.circuits.items():
        print(f"  [{cid}] {circuit.name}: {circuit.power_w:.0f} W")

    await client.close()

asyncio.run(main())
```

## Real-Time Streaming

Gen3 panels deliver power metrics via a push stream. Start the streaming background task to receive continuous updates, and register callbacks to react to each update.

```python
async def main():
    client = SpanGrpcClient("192.168.1.100")
    await client.connect()

    # Register a callback — invoked on every streamed update
    def on_update() -> None:
        data = client.data
        main_power = data.main_feed.power_w
        print(f"Grid: {main_power:.0f} W")

    unregister = client.register_callback(on_update)

    # Start the streaming loop
    await client.start_streaming()

    # Let updates arrive for a while
    await asyncio.sleep(60)

    # Clean up
    unregister()
    await client.stop_streaming()
    await client.close()
```

The `register_callback` method returns an unregister function. Call it to remove the callback without affecting others.

## Capability Runtime Check

Always use `PanelCapability` flags rather than hard-coding the generation:

```python
from span_panel_api import PanelCapability

caps = client.capabilities

if PanelCapability.PUSH_STREAMING in caps:
    await client.start_streaming()

# Gen3 does not support these — guard with capability flags
if PanelCapability.RELAY_CONTROL in caps:
    await client.set_circuit_relay("1", "OPEN")  # Only reached on Gen2
```

## Snapshot Data Model

`get_snapshot()` returns a `SpanPanelSnapshot` populated from the latest streamed metrics. Fields that are Gen2-only are `None` for Gen3.

```python
snapshot: SpanPanelSnapshot = await client.get_snapshot()

snapshot.panel_generation      # PanelGeneration.GEN3
snapshot.serial_number         # panel resource ID (proxy for serial)
snapshot.firmware_version      # empty string until exposed by a trait
snapshot.main_power_w          # total grid power in watts
snapshot.main_voltage_v        # main feed voltage
snapshot.main_current_a        # main feed current

# None on Gen3:
snapshot.grid_power_w          # None
snapshot.battery_soe           # None
snapshot.dsm_state             # None

# Per-circuit snapshot
for cid, c in snapshot.circuits.items():
    c.circuit_id               # str key (positional slot, "1" = slot 1)
    c.name                     # user-assigned name from panel
    c.power_w                  # real power in watts
    c.voltage_v                # circuit voltage
    c.current_a                # circuit current
    c.is_on                    # True if voltage above off-threshold
    c.is_dual_phase            # True for 240 V double-pole circuits
    c.apparent_power_va        # apparent power (VA) — Gen3 only
    c.reactive_power_var       # reactive power (VAR) — Gen3 only
    c.power_factor             # power factor — Gen3 only
    # None on Gen3:
    c.relay_state              # None
    c.priority                 # None
    c.energy_consumed_wh       # None
```

## Low-Level Data Access

For direct access to the raw gRPC layer data (circuit topology and latest metrics):

```python
data = client.data   # span_panel_api.grpc.models.PanelData

data.serial          # panel serial / resource ID
data.firmware        # firmware version string
data.circuits        # dict[int, CircuitInfo]  — static circuit topology
data.metrics         # dict[int, CircuitMetrics] — latest streamed values
data.main_feed       # CircuitMetrics for the main service entrance
```

`CircuitInfo` fields: `circuit_id`, `name`, `metric_iid`, `is_dual_phase`, `breaker_position`

`CircuitMetrics` fields: `power_w`, `voltage_v`, `current_a`, `apparent_power_va`, `reactive_power_var`, `frequency_hz`, `power_factor`, `is_on`, `voltage_a_v`, `voltage_b_v`, `current_a_a`, `current_b_a`

## Error Handling

```python
from span_panel_api import SpanPanelGrpcError, SpanPanelGrpcConnectionError

try:
    await client.connect()
except SpanPanelGrpcConnectionError as e:
    print(f"Could not connect to Gen3 panel: {e}")
except SpanPanelGrpcError as e:
    print(f"gRPC error: {e}")
```

See [error-handling.md](error-handling.md) for the full exception hierarchy.
