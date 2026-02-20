# Gen2 REST/OpenAPI Client

The Gen2 client (`SpanPanelClient`) communicates with SPAN panels via the local REST API (HTTP on port 80). Gen2 covers the original SPAN Panel hardware (pre-MAIN40/MLO48).

> **Note**: For integrations that should work with both generations, prefer [`create_span_client`](../README.md) from the factory module. Use `SpanPanelClient` directly only when targeting Gen2 exclusively.

## Connection Patterns

### Context Manager (Recommended for Scripts)

Best for scripts, one-off operations, and short-lived processes.

```python
import asyncio
from span_panel_api import SpanPanelClient

async def main():
    async with SpanPanelClient("192.168.1.100") as client:
        await client.authenticate("my-script", "SPAN Control Script")

        status = await client.get_status()
        print(f"Panel: {status.system.manufacturer}")

        circuits = await client.get_circuits()
        for circuit_id, circuit in circuits.circuits.additional_properties.items():
            print(f"{circuit.name}: {circuit.instant_power_w} W")

asyncio.run(main())
```

The context manager handles `close()` automatically on exit, including exception paths.

### Long-Lived Pattern (Services and Integrations)

Best for long-running processes such as Home Assistant integrations and daemons.

```python
import asyncio
from span_panel_api import SpanPanelClient

class SpanPanelService:
    def __init__(self, host: str) -> None:
        self.client = SpanPanelClient(host)
        self._authenticated = False

    async def setup(self) -> None:
        try:
            await self.client.authenticate("my-service", "Panel Service")
            self._authenticated = True
        except Exception:
            await self.client.close()
            raise

    async def update(self) -> dict:
        if not self._authenticated:
            await self.client.authenticate("my-service", "Panel Service")
            self._authenticated = True
        try:
            return {
                "status": await self.client.get_status(),
                "panel": await self.client.get_panel_state(),
                "circuits": await self.client.get_circuits(),
                "storage": await self.client.get_storage_soe(),
            }
        except Exception:
            self._authenticated = False
            raise

    async def teardown(self) -> None:
        await self.client.close()
```

### Manual Pattern (Advanced)

Full control over lifecycle — useful for debugging or custom requirements.

```python
client = SpanPanelClient("192.168.1.100")
try:
    await client.authenticate("manual-app", "Manual Application")
    circuits = await client.get_circuits()
    print(f"Found {len(circuits.circuits.additional_properties)} circuits")
finally:
    await client.close()   # Always close to free resources
```

## Client Initialization

```python
client = SpanPanelClient(
    host="192.168.1.100",           # Required: panel IP or hostname
    port=80,                        # Optional: default 80
    timeout=30.0,                   # Optional: request timeout in seconds
    use_ssl=False,                  # Optional: use HTTPS (uncommon for local)
    cache_window=1.0,               # Optional: response cache window in seconds
    retries=0,                      # Optional: retry attempts on transient errors
    retry_timeout=0.5,              # Optional: initial delay between retries
    retry_backoff_multiplier=2.0,   # Optional: exponential backoff multiplier
)
```

## Authentication

SPAN Gen2 panels require JWT authentication. The panel's physical proximity sensor must be triggered (within 15 minutes) on first registration.

```python
# Register a new API client — one-time setup per client name
auth = await client.authenticate(
    name="my-integration",       # Identifies the client; shown in panel UI
    description="My Application" # Optional display description
)
# The token is stored internally; all subsequent requests use it automatically.

# If you already have a token (e.g., stored from a previous run):
client.set_access_token("your-jwt-token")
```

## API Reference

### Panel Status and State

```python
# System info — no authentication required
status = await client.get_status()
print(f"Manufacturer: {status.system.manufacturer}")
print(f"Network: {status.network}")

# Detailed panel state — authentication required
panel = await client.get_panel_state()
print(f"Grid power: {panel.instant_grid_power_w} W")
print(f"Main relay: {panel.main_relay_state}")

# Battery / storage state of energy — authentication required
storage = await client.get_storage_soe()
print(f"Battery SOE: {storage.soe * 100:.1f}%")
print(f"Max capacity: {storage.max_energy_kwh} kWh")
```

### Circuit Data

```python
circuits = await client.get_circuits()
for circuit_id, circuit in circuits.circuits.additional_properties.items():
    print(f"[{circuit_id}] {circuit.name}")
    print(f"  Power: {circuit.instant_power_w} W")
    print(f"  Relay: {circuit.relay_state}")
    print(f"  Priority: {circuit.priority}")
```

`get_circuits()` enriches the API response with **virtual circuits** for unmapped panel tabs, ensuring complete panel visibility. Virtual circuits have IDs such as `unmapped_tab_1`.

```python
# Configured circuit
circuits.circuits.additional_properties["1"].name          # "Main Kitchen"
circuits.circuits.additional_properties["1"].instant_power_w  # 150.0

# Virtual circuit for an unmapped tab (e.g., solar feedthrough)
circuits.circuits.additional_properties["unmapped_tab_5"].instant_power_w  # -2500.0
```

### Circuit Control

Authentication is required for all write operations.

```python
# Relay control
await client.set_circuit_relay("1", "OPEN")    # Turn off
await client.set_circuit_relay("1", "CLOSED")  # Turn on

# Load priority (affects behavior during demand-response events)
await client.set_circuit_priority("1", "MUST_HAVE")
await client.set_circuit_priority("1", "NICE_TO_HAVE")
await client.set_circuit_priority("1", "NON_ESSENTIAL")
```

### Unified Snapshot (Protocol-Compatible)

For code that must work with both Gen2 and Gen3, use `get_snapshot()`:

```python
snapshot = await client.get_snapshot()
print(f"Serial: {snapshot.serial_number}")
print(f"Main power: {snapshot.main_power_w} W")
for cid, circuit in snapshot.circuits.items():
    print(f"  [{cid}] {circuit.name}: {circuit.power_w} W  relay={circuit.relay_state}")
```

## Timeout and Retry Configuration

| Parameter                  | Default | Description                                           |
| -------------------------- | ------- | ----------------------------------------------------- |
| `timeout`                  | `30.0`  | Per-request timeout in seconds                        |
| `retries`                  | `0`     | Retry attempts on transient failures (0 = no retries) |
| `retry_timeout`            | `0.5`   | Initial delay between retries in seconds              |
| `retry_backoff_multiplier` | `2.0`   | Multiplier for exponential backoff                    |

```python
# Production configuration with retries
client = SpanPanelClient(
    "192.168.1.100",
    timeout=10.0,
    retries=3,
    retry_timeout=0.5,
    retry_backoff_multiplier=2.0,
)

# Settings are also mutable at runtime
client.retries = 2
client.retry_timeout = 1.0
```

| `retries` | Total attempts |
| --------- | -------------- |
| 0         | 1 (no retries) |
| 1         | 2              |
| 2         | 3              |

## Response Caching

The client caches responses per endpoint for a configurable window to prevent redundant network calls. Each endpoint (status, panel state, circuits, storage) has an independent cache that starts when data is successfully received.

```python
panel = await client.get_panel_state()    # Network call
circuits = await client.get_circuits()   # Uses cached panel state internally
panel2 = await client.get_panel_state()  # Returns cached result (within window)
```

- `cache_window=0` disables caching entirely
- Failed requests do not start or extend the cache window

## Home Assistant Integration

Home Assistant's event loop can be sensitive to `asyncio.sleep()` calls inside retry logic. Use `set_async_delay_func` to replace the default with an HA-compatible implementation:

```python
from span_panel_api import SpanPanelClient, set_async_delay_func
import asyncio

async def ha_delay(seconds: float) -> None:
    await asyncio.sleep(seconds)

set_async_delay_func(ha_delay)

# Use the client normally — retry delays now use your custom function
async with SpanPanelClient("192.168.1.100") as client:
    await client.authenticate("ha-integration", "Home Assistant")
    panel = await client.get_panel_state()

# Restore default behavior
set_async_delay_func(None)
```

This only affects retry delay behaviour; normal API calls are unaffected.

## SSL

Local SPAN panels do not typically support HTTPS, but the option is available:

```python
client = SpanPanelClient(host="span-panel.local", use_ssl=True, port=443)
```

## Simulation Mode

The Gen2 client supports a simulation mode for testing without real hardware. See [simulation.md](simulation.md) for full details.

```python
client = SpanPanelClient(
    "192.168.1.100",
    simulation_mode=True,
    simulation_config_path="examples/simulation_config_40_circuit_with_battery.yaml",
)
```
