# SPAN Panel API Client

[![GitHub Release](https://img.shields.io/github/v/release/SpanPanel/span-panel-api?style=flat-square)](https://github.com/SpanPanel/span-panel-api/releases)
[![PyPI Version](https://img.shields.io/pypi/v/span-panel-api?style=flat-square)](https://pypi.org/project/span-panel-api/)
[![Python Versions](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue?style=flat-square)](https://pypi.org/project/span-panel-api/)
[![License](https://img.shields.io/github/license/SpanPanel/span-panel-api?style=flat-square)](https://github.com/SpanPanel/span-panel-api/blob/main/LICENSE)

[![CI Status](https://img.shields.io/github/actions/workflow/status/SpanPanel/span-panel-api/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/SpanPanel/span-panel-api/actions/workflows/ci.yml)

[![Code Quality](https://img.shields.io/codefactor/grade/github/SpanPanel/span-panel-api?style=flat-square)](https://www.codefactor.io/repository/github/spanpanel/span-panel-api)
[![Security](https://img.shields.io/snyk/vulnerabilities/github/SpanPanel/span-panel-api?style=flat-square)](https://snyk.io/test/github/SpanPanel/span-panel-api)

[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&style=flat-square)](https://github.com/pre-commit/pre-commit)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg?style=flat-square)](https://github.com/psf/black)
[![Linting: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![Type Checking: MyPy](https://img.shields.io/badge/type%20checking-mypy-blue?style=flat-square)](https://mypy-lang.org/)

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support%20development-FFDD00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/cayossarian)

A Python client library for SPAN Panel smart electrical panels. Supports both **Gen2** panels (REST/OpenAPI) and **Gen3** panels (gRPC — MAIN40/MLO48).

## Installation

```bash
# Core library — Gen2 panels (REST/OpenAPI)
pip install span-panel-api

# With Gen3 gRPC support
pip install span-panel-api[grpc]
```

## Quick Start

Use `create_span_client` to connect to a panel without knowing its generation in advance. The factory auto-detects Gen2 vs Gen3 and returns the appropriate client.

```python
import asyncio
from span_panel_api import create_span_client

async def main():
    client = await create_span_client("192.168.1.100")
    await client.connect()

    snapshot = await client.get_snapshot()
    print(f"Panel: {snapshot.serial_number}  ({snapshot.panel_generation})")
    print(f"Grid power: {snapshot.main_power_w:.0f} W")

    for circuit_id, circuit in snapshot.circuits.items():
        print(f"  [{circuit_id}] {circuit.name}: {circuit.power_w:.0f} W")

    await client.close()

asyncio.run(main())
```

To target a specific generation, pass `panel_generation` explicitly:

```python
from span_panel_api import create_span_client, PanelGeneration

# Force Gen2 (REST/OpenAPI)
client = await create_span_client("192.168.1.100", panel_generation=PanelGeneration.GEN2)

# Force Gen3 (gRPC) — requires span-panel-api[grpc]
client = await create_span_client("192.168.1.100", panel_generation=PanelGeneration.GEN3)
```

## Gen2 vs Gen3 Capabilities

| Feature                  | Gen2 (REST/OpenAPI) | Gen3 (gRPC)   |
| ------------------------ | ------------------- | ------------- |
| Authentication           | Required (JWT)      | None          |
| Circuit relay control    | Yes                 | No            |
| Circuit priority control | Yes                 | No            |
| Energy history (Wh)      | Yes                 | No            |
| Battery / storage SOE    | Yes                 | No            |
| Solar / DSM state        | Yes                 | No            |
| Real-time power metrics  | Polled              | Push-streamed |
| Simulation mode          | Yes                 | No            |

Use `client.capabilities` (a `PanelCapability` flag set) at runtime to guard optional features:

```python
from span_panel_api import PanelCapability

if PanelCapability.RELAY_CONTROL in client.capabilities:
    await client.set_circuit_relay("1", "OPEN")

if PanelCapability.PUSH_STREAMING in client.capabilities:
    await client.start_streaming()
```

## Documentation

| Topic                                                          | Link                                             |
| -------------------------------------------------------------- | ------------------------------------------------ |
| Gen2 REST/OpenAPI client — usage, auth, API reference, caching | [docs/gen2-client.md](docs/gen2-client.md)       |
| Gen3 gRPC client — usage, streaming, data models               | [docs/gen3-client.md](docs/gen3-client.md)       |
| Error handling and retry strategies                            | [docs/error-handling.md](docs/error-handling.md) |
| Simulation mode                                                | [docs/simulation.md](docs/simulation.md)         |
| Development setup and contributing                             | [docs/development.md](docs/development.md)       |

## License

MIT License - see [LICENSE](LICENSE) for details.
