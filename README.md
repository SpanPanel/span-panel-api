# span-panel-api - SPAN Panel API Client

A modern, type-safe Python client library for the SPAN Panel REST API.

[![PyPI version](https://badge.fury.io/py/span-panel-api.svg)](https://badge.fury.io/py/span-panel-api)
[![GitHub license](https://img.shields.io/github/license/SpanPanel/span-panel-api.svg)](https://github.com/SpanPanel/span-panel-api/blob/main/LICENSE)
[![Python Versions](https://img.shields.io/pypi/pyversions/span-panel-api.svg)](https://pypi.org/project/span-panel-api/)

## About

The `span-panel-api` is a Python client library for interacting with SPAN smart electrical panels. It's maintained by the [SpanPanel](https://github.com/SpanPanel) organization.

[![PyPI Downloads](https://img.shields.io/pypi/dm/span-panel-api.svg)](https://pypi.org/project/span-panel-api/)
[![GitHub Stars](https://img.shields.io/github/stars/SpanPanel/span-panel-api.svg)](https://github.com/SpanPanel/span-panel-api/stargazers)
[![GitHub Issues](https://img.shields.io/github/issues/SpanPanel/span-panel-api.svg)](https://github.com/SpanPanel/span-panel-api/issues)

## Features

- **Type Safety**: Full Pydantic models generated from OpenAPI specification
- **Async Support**: Built on httpx for high-performance async operations
- **Validation**: Automatic request/response validation

## Installation

### From PyPI (Recommended)

```bash
# Install from PyPI
pip install span-panel-api
```

### From GitHub (Latest Development Version)

```bash
# Install the latest development version
pip install git+https://github.com/SpanPanel/span-panel-api.git
```

### For Development

```bash
# Clone the repository
git clone https://github.com/SpanPanel/span-panel-api.git
cd span-panel-api

# Install with Poetry
poetry install
```

## Quick Start

```python
import asyncio
from span_panel_api import SpanPanelClient

async def main():
    async with SpanPanelClient("192.168.1.100") as client:
        # Authenticate
        token = await client.authenticate("my-client", "Description")

        # Get panel status
        status = await client.get_status()
        print(f"Panel: {status.system.manufacturer} {status.system.model}")

        # Get all circuits
        circuits = await client.get_circuits()
        for circuit_id, circuit in circuits.circuits.items():
            print(f"Circuit {circuit_id}: {circuit.name} - {circuit.instant_power_w}W")

asyncio.run(main())
```

## API Coverage

This client provides access to a range of SPAN Panel REST API endpoints (some API's may be restricted based on proper authentication):

### Authentication

- Register new API clients
- Manage existing clients

### Panel Operations

- System status and hardware info
- Real-time power and energy data
- Main relay control
- Emergency reconnect

### Circuit Management

- Get all circuits and individual circuit data
- Control circuit relays and priorities
- Power consumption and production monitoring

### Storage & Battery

- Battery state of energy (SOE)
- Storage thresholds configuration

### Network Features

- WiFi scanning and configuration
- Network connectivity status
- Grid islanding state detection

## Development

### Setting Up Local Development Environment

```bash
# Clone the repository
git clone https://github.com/SpanPanel/span-panel-api.git
cd span-panel-api

# Install development dependencies
poetry install

# Install pre-commit hooks
poetry run pre-commit install
```

### Development Workflow

The client models are generated from the OpenAPI specification. After making changes to the OpenAPI spec:

```bash
# Regenerate models from OpenAPI specification
poetry run openapi-generator-cli generate -i openapi.json -g python -o generated_client

# Run the test suite
poetry run pytest

# Check code quality
poetry run mypy src/
poetry run ruff check src/
```

## Requirements

- Python 3.11+
- httpx 0.28.1+
- pydantic 2.11.5+
- typing-extensions 4.0.0+

## License

MIT License - see [LICENSE](https://github.com/SpanPanel/span-panel-api/blob/main/LICENSE) file for details.

## Issues

If you encounter any issues or have feature requests, please [open an issue](https://github.com/SpanPanel/span-panel-api/issues) on our GitHub repository.

## Disclaimer

This is an independent client library not officially affiliated with or endorsed by SPAN.IO Inc. The span-panel-api library is based on the SPAN Panel OpenAPI.

### No Warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
