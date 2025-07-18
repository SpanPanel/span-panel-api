# SPAN Panel OpenAPI Client

[![GitHub Release](https://img.shields.io/github/v/release/SpanPanel/span-panel-api?style=flat-square)](https://github.com/SpanPanel/span-panel-api/releases)
[![PyPI Version](https://img.shields.io/pypi/v/span-panel-api?style=flat-square)](https://pypi.org/project/span-panel-api/)
[![Python Versions](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue?style=flat-square)](https://pypi.org/project/span-panel-api/)
[![License](https://img.shields.io/github/license/SpanPanel/span-panel-api?style=flat-square)](https://github.com/SpanPanel/span-panel-api/blob/main/LICENSE)

[![CI Status](https://img.shields.io/github/actions/workflow/status/SpanPanel/span-panel-api/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/SpanPanel/span-panel-api/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/codecov/c/github/SpanPanel/span-panel-api?style=flat-square)](https://codecov.io/gh/SpanPanel/span-panel-api)
[![Code Quality](https://img.shields.io/codefactor/grade/github/SpanPanel/span-panel-api?style=flat-square)](https://www.codefactor.io/repository/github/spanpanel/span-panel-api)
[![Security](https://img.shields.io/snyk/vulnerabilities/github/SpanPanel/span-panel-api?style=flat-square)](https://snyk.io/test/github/SpanPanel/span-panel-api)

[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&style=flat-square)](https://github.com/pre-commit/pre-commit)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg?style=flat-square)](https://github.com/psf/black)
[![Linting: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![Type Checking: MyPy](https://img.shields.io/badge/type%20checking-mypy-blue?style=flat-square)](https://mypy-lang.org/)

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support%20development-FFDD00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/cayossarian)

A Python client library for accessing the SPAN Panel OpenAPI endpoint.

## Simulation Mode

The SPAN Panel API client includes a simulation mode for development and testing without requiring a physical SPAN panel. When enabled, the client uses pre-recorded fixture data and applies dynamic variations provided by the API to simulate various load variations.
Simulation mode supports time-based energy accumulation, power fluctuation patterns for different appliance types, and per-circuit or per-branch variation controls.

For detailed information and usage examples, see [tests/docs/simulation.md](tests/docs/simulation.md).

## Installation

```bash
pip install span-panel-api
```

## Usage Patterns

The client supports two usage patterns depending on your use case:

### Context Manager Pattern (Recommended for Scripts)

**Best for**: Scripts, one-off operations, short-lived applications

```python
import asyncio
from span_panel_api import SpanPanelClient

async def main():
    # Context manager automatically handles connection lifecycle
    async with SpanPanelClient("192.168.1.100") as client:
        # Authenticate
        auth = await client.authenticate("my-script", "SPAN Control Script")

        # Get panel status (no auth required)
        status = await client.get_status()
        print(f"Panel: {status.system.manufacturer}")

        # Get circuits (requires auth)
        circuits = await client.get_circuits()
        for circuit_id, circuit in circuits.circuits.additional_properties.items():
            print(f"{circuit.name}: {circuit.instant_power_w}W")

        # Control a circuit
        await client.set_circuit_relay("circuit-1", "OPEN")
        await client.set_circuit_priority("circuit-1", "MUST_HAVE")

    # Client is automatically closed when exiting context

asyncio.run(main())
```

### Long-Lived Pattern (Services or Integrations)

**Best for**: Long-running services, persistent connections, integration platforms

> **Note for Home Assistant integrations**: See [Home Assistant Integration](#home-assistant-integration) section for HA-specific compatibility configuration.

```python
import asyncio
from span_panel_api import SpanPanelClient

class SpanPanelIntegration:
    """Example long-running service integration pattern."""

    def __init__(self, host: str):
        # Create client but don't use context manager
        self.client = SpanPanelClient(host)
        self._authenticated = False

    async def setup(self) -> None:
        """Initialize the integration (called once)."""
        try:
            # Authenticate once during setup
            await self.client.authenticate("my-service", "Panel Integration Service")
            self._authenticated = True
        except Exception as e:
            await self.client.close()  # Clean up on setup failure
            raise

    async def update_data(self) -> dict:
        """Update all data (called periodically by coordinator)."""
        if not self._authenticated:
            await self.client.authenticate("my-service", "Panel Integration Service")
            self._authenticated = True

        try:
            # Get all data in one update cycle
            status = await self.client.get_status()
            panel_state = await self.client.get_panel_state()
            circuits = await self.client.get_circuits()
            storage = await self.client.get_storage_soe()

            return {
                "status": status,
                "panel": panel_state,
                "circuits": circuits,
                "storage": storage
            }
        except Exception:
            self._authenticated = False  # Reset auth on error
            raise

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> None:
        """Set circuit priority (called by service)."""
        if not self._authenticated:
            await self.client.authenticate("my-service", "Panel Integration Service")
            self._authenticated = True

        await self.client.set_circuit_priority(circuit_id, priority)

    async def cleanup(self) -> None:
        """Cleanup when integration is unloaded."""
        await self.client.close()

# Usage in long-running service
async def main():
    integration = SpanPanelIntegration("192.168.1.100")

    try:
        await integration.setup()

        # Simulate coordinator updates
        for i in range(10):
            data = await integration.update_data()
            print(f"Update {i}: {len(data['circuits'].circuits.additional_properties)} circuits")
            await asyncio.sleep(30)  # Service typically updates every 30 seconds

    finally:
        await integration.cleanup()

asyncio.run(main())
```

### Manual Pattern (Advanced Usage)

**Best for**: Custom connection management, special requirements

```python
import asyncio
from span_panel_api import SpanPanelClient

async def manual_example():
    """Manual client lifecycle management."""
    client = SpanPanelClient("192.168.1.100")

    try:
        # Manually authenticate
        await client.authenticate("manual-app", "Manual Application")

        # Do work
        status = await client.get_status()
        circuits = await client.get_circuits()

        print(f"Found {len(circuits.circuits.additional_properties)} circuits")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        # IMPORTANT: Always close the client to free resources
        await client.close()

asyncio.run(manual_example())
```

## When to Use Each Pattern

| Pattern             | Use Case                                 | Pros                                                                         | Cons                                                                     |
| ------------------- | ---------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **Context Manager** | Scripts, one-off tasks, testing          | Automatic cleanup • Exception safe • Simple code                             | Creates/destroys connection each time |
| **Long-Lived**      | Services, daemons, integration platforms | Efficient connection reuse Authentication persistence | Manual lifecycle management • Must handle cleanup                        |
| **Manual**          | Custom requirements, debugging           | Full control handling                                         | Must remember to call close() • More error-prone                         |

## Error Handling

The client provides error categorization for different retry strategies:

### Exception Types

All exceptions inherit from `SpanPanelError`.

- `SpanPanelAuthError`: Raised for authentication failures (invalid token, login required, etc.)
- `SpanPanelConnectionError`: Raised for network errors, server errors, or API errors
- `SpanPanelTimeoutError`: Raised when a request times out
- `SpanPanelValidationError`: Raised for data validation errors (invalid input, schema mismatch)
- `SpanPanelAPIError`: General API error (fallback for unexpected API issues)
- `SpanPanelRetriableError`: Raised for retriable server errors (502, 503, 504)
- `SpanPanelServerError`: Raised for non-retriable server errors (500)
- `SimulationConfigurationError`: Raised for invalid or missing simulation configuration (simulation mode only)

```python
from span_panel_api import (
    SpanPanelError,              # Base exception
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
    SpanPanelAPIError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SimulationConfigurationError,
)
```

### HTTP Error Code Mapping

| Status Code                     | Exception                        | Retry?               | Description                      | Action                         |
| ------------------------------- | -------------------------------- | -------------------- | -------------------------------- | ------------------------------ |
| **Authentication Errors**       | -                                | -                    | -                                | -                              |
| 401, 403                        | `SpanPanelAuthError`             | Once (after re-auth) | Authentication required/failed   | Re-authenticate and retry once |
| **Server/Network Errors**       | -                                | -                    | -                                | -                              |
| 500                             | `SpanPanelServerError`           | No                   | Server error (non-retriable)     | Check server, report issue     |
| 502, 503, 504                   | `SpanPanelRetriableError`        | Yes                  | Retriable server/network errors  | Retry with exponential backoff |
| **Other HTTP Errors**           | -                                | -                    | -                                | -                              |
| 404, 400, etc                   | `SpanPanelAPIError`              | Case by case         | Client/request errors            | Check request parameters       |
| **Timeouts**                    | `SpanPanelTimeoutError`          | Yes                  | Request timed out                | Retry with backoff             |
| **Validation Errors**           | `SpanPanelValidationError`       | No                   | Data validation failed           | Fix input data                 |
| **Simulation Config Errors**    | `SimulationConfigurationError`   | No                   | Invalid/missing simulation config| Fix simulation config          |

### Retry Strategy

```python
async def example_request_with_retry():
    """Example showing appropriate error handling."""
    try:
        return await client.get_circuits()
    except SpanPanelAuthError:
        # Re-authenticate and retry once
        await client.authenticate("my-app", "My Application")
        return await client.get_circuits()
    except SpanPanelRetriableError as e:
        # Temporary server or network issues - should retry with backoff
        logger.warning(f"Retriable error, will retry: {e}")
        raise  # Let retry logic handle the retry
    except SpanPanelTimeoutError as e:
        # Network timeout - should retry
        logger.warning(f"Timeout, will retry: {e}")
        raise
    except SpanPanelValidationError as e:
        # Data validation error - fix input
        logger.error(f"Validation error: {e}")
        raise
    except SimulationConfigurationError as e:
        # Simulation config error - fix config
        logger.error(f"Simulation config error: {e}")
        raise
    except SpanPanelAPIError as e:
        # Other API errors
        logger.error(f"API error: {e}")
        raise
```

### Exception Handling

The client configures the underlying OpenAPI client with `raise_on_unexpected_status=True`,
ensuring that HTTP errors (especially 500 responses) are converted to appropriate exceptions
rather than being silently ignored.

## API Reference

### Client Initialization

```python
client = SpanPanelClient(
    host="192.168.1.100",    # Required: SPAN Panel IP
    port=80,                 # Optional: default 80
    timeout=30.0,            # Optional: request timeout
    use_ssl=False,           # Optional: HTTPS (usually False for local)
    cache_window=1.0         # Optional: cache window in seconds (0 to disable)
)
```

### Authentication

```python
# Register a new API client (one-time setup)
auth = await client.authenticate(
    name="my-integration",           # Required: client name
    description="My Application"  # Optional: description
)
# Token is stored and used for subsequent requests
```

### Panel Information

```python
# System status (no authentication required)
status = await client.get_status()
print(f"System: {status.system}")
print(f"Network: {status.network}")

# Detailed panel state (requires authentication)
panel = await client.get_panel_state()
print(f"Grid power: {panel.instant_grid_power_w}W")
print(f"Main relay: {panel.main_relay_state}")

# Battery storage information
storage = await client.get_storage_soe()
print(f"Battery SOE: {storage.soe * 100:.1f}%")
print(f"Max capacity: {storage.max_energy_kwh}kWh")
```

### Circuit Control

```python
# Get all circuits
circuits = await client.get_circuits()
for circuit_id, circuit in circuits.circuits.additional_properties.items():
    print(f"Circuit {circuit_id}: {circuit.name}")
    print(f"  Power: {circuit.instant_power_w}W")
    print(f"  Relay: {circuit.relay_state}")
    print(f"  Priority: {circuit.priority}")

# Control circuit relay (OPEN/CLOSED)
await client.set_circuit_relay("circuit-1", "OPEN")   # Turn off
await client.set_circuit_relay("circuit-1", "CLOSED") # Turn on

# Set circuit priority
await client.set_circuit_priority("circuit-1", "MUST_HAVE")
await client.set_circuit_priority("circuit-1", "NICE_TO_HAVE")
```

### Complete Circuit Data

The `get_circuits()` method includes virtual circuits for unmapped panel tabs,
providing complete panel visibility including non-user controlled tabs.

- Virtual circuits have IDs like `unmapped_tab_1`, `unmapped_tab_2`
- All energy values are correctly mapped from panel branches

**Example Output:**

```python
circuits = await client.get_circuits()

# Standard configured circuits
print(circuits.circuits.additional_properties["1"].name)  # "Main Kitchen"
print(circuits.circuits.additional_properties["1"].instant_power_w)  # 150

# Virtual circuits for unmapped tabs (e.g., solar)
print(circuits.circuits.additional_properties["unmapped_tab_5"].name)  # "Unmapped Tab 5"
print(circuits.circuits.additional_properties["unmapped_tab_5"].instant_power_w)  # -2500 (solar production)
```

## Timeout and Retry Control

The SPAN Panel API client provides timeout and retry configuration:

- `timeout` (float, default: 30.0): The maximum time (in seconds) to wait for a response from the panel for each attempt.
- `retries` (int, default: 0): The number of times to retry a failed request due to network or retriable server errors.
`retries=0` means no retries (1 total attempt), `retries=1` means 1 retry (2 total attempts), etc.
- `retry_timeout` (float, default: 0.5): The base wait time (in seconds) between retries, with exponential backoff.
- `retry_backoff_multiplier` (float, default: 2.0): The multiplier for exponential backoff between retries.

### Example Usage

```python
# No retries (default, fast feedback)
client = SpanPanelClient("192.168.1.100", timeout=10.0)

# Add retries for production
client = SpanPanelClient("192.168.1.100", timeout=10.0, retries=2, retry_timeout=1.0)

# Full retry configuration
client = SpanPanelClient(
    "192.168.1.100",
    timeout=10.0,
    retries=3,
    retry_timeout=0.5,
    retry_backoff_multiplier=2.0
)

# Change retry settings at runtime
client.retries = 3
client.retry_timeout = 2.0
client.retry_backoff_multiplier = 1.5
```

### What does 'retries' mean?

| retries | Total Attempts | Description          |
| ------- | -------------- | -------------------- |
| 0       | 1              | No retries (default) |
| 1       | 2              | 1 retry              |
| 2       | 3              | 2 retries            |

Retry and timeout settings can be queried and changed at runtime.

## Performance Features

### Caching

The client includes a time-based cache that prevents redundant API calls within a
configurable window.  This feature is particularly useful when multiple operations need the same data.
The package itself makes multiple calls to create virtual circuits for tabs not represented in circuits data so the cache avoids unecessary calls when the user also makes requests the same data.

**Cache Behavior:**

- Each API endpoint (status, panel_state, circuits, storage) has independent cache
- Cache window starts when successful data is obtained
- Subsequent calls within the window return cached data
- After expiration, next call makes fresh network request
- Failed requests don't affect cache timing

**Example Benefits:**

```python
# These calls demonstrate cache efficiency:
panel_state = await client.get_panel_state()    # Network call
circuits = await client.get_circuits()          # Uses cached panel_state data internally
panel_state2 = await client.get_panel_state()   # Returns cached data (within window)
```

## Development Setup

### Prerequisites

- Python 3.12 or 3.13 (SPAN Panel requires Python 3.12+)
- [Poetry](https://python-poetry.org/) for dependency management

### Development Installation

```bash
# Clone and install
git clone <repository code URL>
cd span-panel-api
eval "$(poetry env activate)"
poetry install

# Run tests
poetry run pytest

# Check coverage
python scripts/coverage.py
```

### Project Structure

```bash
span_openapi/
├── src/span_panel_api/           # Main client library
│   ├── client.py                 # SpanPanelClient (high-level wrapper)
│   ├── simulation.py             # Simulation engine for dynamic test mode
│   ├── exceptions.py             # Exception hierarchy
│   ├── const.py                  # HTTP status constants
│   └── generated_client/         # Auto-generated OpenAPI client
├── tests/                        # Test suite
│   ├── test_core_client.py       # Core client and API error path tests
│   ├── test_context_manager.py   # Context manager tests
│   ├── test_cache_functionality.py # Cache and retry tests
│   ├── test_enhanced_circuits.py # Enhanced/virtual circuits tests
│   ├── test_simulation_mode.py   # Simulation mode tests
│   ├── test_factories.py         # Shared test fixtures and factories
│   ├── conftest.py               # Pytest shared fixtures
│   └── simulation_fixtures/      # Simulation fixture data (response .txt files)
├── scripts/coverage.py           # Coverage checking utility
├── openapi.json                  # SPAN Panel OpenAPI specification
├── pyproject.toml                # Poetry configuration
└── README.md                     # Project documentation

```

## Advanced Usage

### Home Assistant Integration

For Home Assistant integrations, the client provides a compatibility layer to handle asyncio timing issues that can occur in HA's event loop:

```python
from span_panel_api import SpanPanelClient, set_async_delay_func
import asyncio

# In your Home Assistant integration setup:
async def ha_compatible_delay(seconds: float) -> None:
    """Custom delay function that works well with HA's event loop."""
    # Use HA's async utilities or implement HA-specific delay logic
    await asyncio.sleep(seconds)

# Configure the client to use HA-compatible delay
set_async_delay_func(ha_compatible_delay)

# Now create and use clients normally
async with SpanPanelClient("192.168.1.100") as client:
    # Client will use your custom delay function for retry logic
    await client.authenticate("your_token")
    panel_state = await client.get_panel_state()

# To reset to default behavior (uses asyncio.sleep):
set_async_delay_func(None)
```

**Why This Matters:**

- Home Assistant's event loop can be sensitive to blocking operations
- The default `asyncio.sleep()` used in retry logic may not play well with HA
- Custom delay functions allow HA integrations to use HA's preferred async patterns
- This prevents integration timeouts and improves responsiveness

**Note:** This only affects the retry delay behavior. Normal API operations remain unchanged.

### SSL Configuration

```python
# For panels that support SSL
# Note: We do not currently observe panels supporting SSL for local access
client = SpanPanelClient(
    host="span-panel.local",
    use_ssl=True,
    port=443
)
```

### Timeout Configuration

```python
# Custom timeout for slow networks
client = SpanPanelClient(
    host="192.168.1.100",
    timeout=60.0  # 60 second timeout
)
```

## Testing and Coverage

```bash
# Run full test suite
poetry run pytest

# Generate coverage report
python scripts/coverage.py --full

# Run just context manager tests
poetry run pytest tests/test_context_manager.py -v

# Check coverage meets threshold
python scripts/coverage.py --check --threshold 90

# Run with coverage
poetry run pytest --cov=span_panel_api tests/
```

## Contributing

1. Get `openapi.json` SPAN Panel API specs

   (for example via REST Client extension)

   GET <https://span-panel-ip/api/v1/openapi.json>

2. Regenerate client: `poetry run python generate_client.py`
3. Update wrapper client in `src/span_panel_api/client.py` if needed
4. Add tests for new functionality
5. Update this README if adding new features

## License

MIT License - see LICENSE file for details.
