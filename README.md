# SPAN Panel API

[![GitHub Release](https://img.shields.io/github/v/release/SpanPanel/span-panel-api?style=flat-square)](https://github.com/SpanPanel/span-panel-api/releases)
[![PyPI Version](https://img.shields.io/pypi/v/span-panel-api?style=flat-square)](https://pypi.org/project/span-panel-api/) [![Python Versions](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://pypi.org/project/span-panel-api/)
[![License](https://img.shields.io/github/license/SpanPanel/span-panel-api?style=flat-square)](https://github.com/SpanPanel/span-panel-api/blob/main/LICENSE)

[![CI Status](https://img.shields.io/github/actions/workflow/status/SpanPanel/span-panel-api/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/SpanPanel/span-panel-api/actions/workflows/ci.yml)

[![Code Quality](https://img.shields.io/codefactor/grade/github/SpanPanel/span-panel-api?style=flat-square)](https://www.codefactor.io/repository/github/spanpanel/span-panel-api)
[![Security](https://img.shields.io/snyk/vulnerabilities/github/SpanPanel/span-panel-api?style=flat-square)](https://snyk.io/test/github/SpanPanel/span-panel-api)

[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&style=flat-square)](https://github.com/pre-commit/pre-commit)
[![Linting: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![Type Checking: MyPy](https://img.shields.io/badge/type%20checking-mypy-blue?style=flat-square)](https://mypy-lang.org/)

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support%20development-FFDD00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/cayossarian)

A Python client library for the SPAN Panel v2 API, using MQTT/Homie for real-time push-based panel state.

## v1.x Sunset Notice

**Package versions prior to 2.0.0 are deprecated.** These versions depend on the SPAN v1 REST API, which will be retired when SPAN sunsets v1 firmware at the end of 2026. Users should upgrade to v2.0.0 or later, which requires v2 firmware
(`spanos2/r202603/05` or later) and a panel passphrase.

## Installation

```bash
pip install span-panel-api
```

### Dependencies

- `httpx` — v2 authentication and detection endpoints
- `paho-mqtt` — MQTT/Homie transport (real-time push)
- `pyyaml` — simulation configuration

## Architecture

v2.0.0 is a ground-up rewrite. The package connects to the SPAN Panel's on-device MQTT broker using the [Homie v5](https://homieiot.github.io/) convention. All panel and circuit state is delivered via retained MQTT messages — no polling required.

### Transport

The `SpanMqttClient` connects to the panel's MQTT broker (MQTTS or WebSocket) and subscribes to the Homie device tree. A `HomieDeviceConsumer` state machine parses incoming topic updates into typed `SpanPanelSnapshot` dataclasses. Changes are pushed to
consumers via callbacks.

### Event-Loop-Driven I/O (Home Assistant Compatible)

The MQTT transport is designed around the Home Assistant core async pattern — all paho-mqtt I/O runs on the asyncio event loop with no background threads:

- **NullLock replacement** — paho-mqtt's seven internal threading locks are replaced with no-op `NullLock` instances at setup time, eliminating lock contention since all access is single-threaded on the event loop.
- **`add_reader` / `add_writer`** — `AsyncMqttBridge` registers the MQTT socket with the event loop via `loop.add_reader()` and `loop.add_writer()`, calling paho's `loop_read()` / `loop_write()` directly from I/O callbacks rather than from a `loop_start()`
  background thread.
- **Periodic misc** — A `loop.call_at()` timer fires every second to call `loop_misc()` for keepalive and timeout housekeeping.
- **Executor bridge for connect** — The initial TLS handshake and TCP connect are blocking operations, so they run in `loop.run_in_executor()`. Once the executor returns, socket callbacks are immediately switched from sync bridges (`call_soon_threadsafe`)
  back to the async-only versions.

This means the library can be dropped into any asyncio application — including Home Assistant — without spawning threads or requiring thread-safe wrappers.

### Circuit Name Synchronization

Circuit names arrive as MQTT retained messages that may land after the Homie device transitions to `$state=ready`. The client handles this with a bounded wait during `connect()`:

1. After the device reaches ready state, the client polls `HomieDeviceConsumer.circuit_nodes_missing_names()` every 250ms.
2. As retained name properties arrive, the consumer stores them. Once all circuit-type nodes have a name, the wait returns immediately.
3. If names have not all arrived within 10 seconds, the timeout expires (non-fatal) and the client proceeds — circuits without names will use fallback identifiers.

This ensures that the first `get_snapshot()` after connect returns human-readable circuit names in the common case, while never blocking indefinitely on a missing retained message.

### Protocols

The library defines three structural subtyping protocols (PEP 544) that both the MQTT transport and the simulation engine implement:

| Protocol                   | Purpose                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------- |
| `SpanPanelClientProtocol`  | Core lifecycle: `connect`, `close`, `ping`, `get_snapshot`                            |
| `CircuitControlProtocol`   | Relay and shed-priority control: `set_circuit_relay`, `set_circuit_priority`          |
| `StreamingCapableProtocol` | Push-based updates: `register_snapshot_callback`, `start_streaming`, `stop_streaming` |

Integration code programs against these protocols, not transport-specific classes.

### Snapshots

All panel state is represented as immutable, frozen dataclasses:

| Dataclass             | Content                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `SpanPanelSnapshot`   | Complete panel state: power, energy, grid/DSM state, hardware status, per-leg voltages, power flows, lugs current, circuits, battery, PV |
| `SpanCircuitSnapshot` | Per-circuit: power, energy, relay state, priority, tabs, device type, breaker rating, current                                            |
| `SpanBatterySnapshot` | BESS: SoC percentage, SoE kWh, vendor/product metadata, nameplate capacity                                                               |
| `SpanPVSnapshot`      | PV inverter: vendor/product metadata, nameplate capacity                                                                                 |

## Usage

### Factory Pattern (Recommended)

The `create_span_client()` factory handles v2 registration and returns a configured `SpanMqttClient`:

```python
import asyncio
from span_panel_api import create_span_client

async def main():
    client = await create_span_client(
        host="192.168.1.100",
        passphrase="your-panel-passphrase",
    )

    try:
        await client.connect()

        # Get a point-in-time snapshot
        snapshot = await client.get_snapshot()
        print(f"Grid power: {snapshot.instant_grid_power_w}W")
        print(f"Firmware: {snapshot.firmware_version}")
        print(f"Circuits: {len(snapshot.circuits)}")

        for cid, circuit in snapshot.circuits.items():
            print(f"  {circuit.name}: {circuit.instant_power_w}W ({circuit.relay_state})")

    finally:
        await client.close()

asyncio.run(main())
```

### Streaming Pattern

For real-time push updates without polling:

```python
import asyncio
from span_panel_api import create_span_client, SpanPanelSnapshot

async def on_snapshot(snapshot: SpanPanelSnapshot) -> None:
    print(f"Grid: {snapshot.instant_grid_power_w}W, Circuits: {len(snapshot.circuits)}")

async def main():
    client = await create_span_client(
        host="192.168.1.100",
        passphrase="your-panel-passphrase",
    )

    try:
        await client.connect()

        # Register callback and start streaming
        unsubscribe = client.register_snapshot_callback(on_snapshot)
        await client.start_streaming()

        # Run until interrupted
        await asyncio.Event().wait()

    finally:
        await client.stop_streaming()
        await client.close()

asyncio.run(main())
```

### Pre-Built Config Pattern

If you already have MQTT broker credentials (e.g., stored from a previous registration):

```python
from span_panel_api import create_span_client, MqttClientConfig

config = MqttClientConfig(
    broker_host="192.168.1.100",
    username="stored-username",
    password="stored-password",
    mqtts_port=8883,
    ws_port=9001,
    wss_port=443,
)

client = await create_span_client(
    host="192.168.1.100",
    mqtt_config=config,
    serial_number="nj-2316-XXXX",
)
```

### Circuit Control

```python
# Set circuit relay (OPEN/CLOSED)
await client.set_circuit_relay("circuit-uuid", "OPEN")
await client.set_circuit_relay("circuit-uuid", "CLOSED")

# Set circuit shed priority (NEVER / SOC_THRESHOLD / OFF_GRID)
await client.set_circuit_priority("circuit-uuid", "NEVER")
```

### API Version Detection

Detect whether a panel supports v2 (unauthenticated probe):

```python
from span_panel_api import detect_api_version

result = await detect_api_version("192.168.1.100")
print(f"API version: {result.api_version}")  # "v1" or "v2"
if result.status_info:
    print(f"Serial: {result.status_info.serial_number}")
    print(f"Firmware: {result.status_info.firmware_version}")
```

### v2 Authentication Functions

Standalone async functions for v2-specific HTTP operations:

```python
from span_panel_api import register_v2, download_ca_cert, get_homie_schema, regenerate_passphrase

# Register and obtain MQTT broker credentials
auth = await register_v2("192.168.1.100", "my-app", passphrase="panel-passphrase")
print(f"Broker: {auth.ebus_broker_host}:{auth.ebus_broker_mqtts_port}")
print(f"Serial: {auth.serial_number}")

# Download the panel's CA certificate (for TLS verification)
pem = await download_ca_cert("192.168.1.100")

# Fetch the Homie property schema (unauthenticated)
schema = await get_homie_schema("192.168.1.100")
print(f"Schema hash: {schema.types_schema_hash}")

# Rotate MQTT broker password (invalidates previous password)
new_password = await regenerate_passphrase("192.168.1.100", token=auth.access_token)
```

## Error Handling

All exceptions inherit from `SpanPanelError`:

| Exception                      | Cause                                                     |
| ------------------------------ | --------------------------------------------------------- |
| `SpanPanelAuthError`           | Invalid passphrase, expired token, or missing credentials |
| `SpanPanelConnectionError`     | Cannot reach the panel (network/DNS)                      |
| `SpanPanelTimeoutError`        | Request or connection timed out                           |
| `SpanPanelValidationError`     | Data validation failure                                   |
| `SpanPanelAPIError`            | Unexpected HTTP response from v2 endpoints                |
| `SpanPanelServerError`         | Panel returned HTTP 500                                   |
| `SimulationConfigurationError` | Invalid simulation YAML configuration                     |

```python
from span_panel_api import SpanPanelAuthError, SpanPanelConnectionError

try:
    client = await create_span_client(host="192.168.1.100", passphrase="wrong")
except SpanPanelAuthError:
    print("Invalid passphrase")
except SpanPanelConnectionError:
    print("Cannot reach panel")
```

## Simulation Mode

The library includes a simulation engine for development and testing without a physical SPAN panel. The `DynamicSimulationEngine` implements the same protocols as `SpanMqttClient` and produces `SpanPanelSnapshot` dataclasses from YAML-configured fixture
data with dynamic power and energy variations.

For detailed information, see [tests/docs/simulation.md](tests/docs/simulation.md).

## Capabilities

The `PanelCapability` flag enum advertises transport features at runtime:

| Flag              | Meaning                               |
| ----------------- | ------------------------------------- |
| `EBUS_MQTT`       | Connected via MQTT/Homie transport    |
| `PUSH_STREAMING`  | Supports real-time push callbacks     |
| `CIRCUIT_CONTROL` | Can set relay state and shed priority |
| `BATTERY_SOE`     | Battery state-of-energy available     |

## Project Structure

```text
src/span_panel_api/
├── __init__.py          # Public API exports
├── auth.py              # v2 HTTP provisioning (register, cert, schema, passphrase)
├── const.py             # Panel state constants (DSM, relay)
├── detection.py         # detect_api_version() → DetectionResult
├── exceptions.py        # Exception hierarchy
├── factory.py           # create_span_client() → SpanMqttClient
├── models.py            # Snapshot dataclasses (panel, circuit, battery, PV)
├── phase_validation.py  # Electrical phase utilities
├── protocol.py          # PEP 544 protocols + PanelCapability flags
├── simulation.py        # Simulation engine (YAML-driven, snapshot-producing)
└── mqtt/
    ├── __init__.py
    ├── async_client.py  # NullLock + AsyncMQTTClient (HA core pattern)
    ├── client.py        # SpanMqttClient (all three protocols)
    ├── connection.py    # AsyncMqttBridge (event-loop-driven, no threads)
    ├── const.py         # MQTT/Homie constants + UUID helpers
    ├── homie.py         # HomieDeviceConsumer (Homie v5 state machine)
    └── models.py        # MqttClientConfig, MqttTransport
```

## Development Setup

### Prerequisites

- Python 3.10+ (CI tests 3.12 and 3.13)
- [Poetry](https://python-poetry.org/) for dependency management

### Development Installation

```bash
git clone https://github.com/SpanPanel/span-panel-api.git
cd span-panel-api
eval "$(poetry env activate)"
poetry install

# Run tests
poetry run pytest

# Check coverage
python scripts/coverage.py
```

### Testing and Coverage

```bash
# Full test suite
poetry run pytest

# With coverage report
poetry run pytest --cov=span_panel_api tests/

# Check coverage meets threshold (85%)
python scripts/coverage.py --check --threshold 85
```

## Contributing

1. Fork and clone the repository
2. Install dev dependencies: `poetry install`
3. Make changes and add tests
4. Ensure all checks pass: `poetry run pytest && poetry run mypy src/ && poetry run ruff check src/`
5. Submit a pull request

## License

MIT License - see LICENSE file for details.
