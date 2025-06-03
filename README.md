# SPAN Panel OpenAPI Client

A modern Python client library for accessing the SPAN Panel API, specifically designed for Home Assistant integration with robust error handling and retry logic.

## Features

- **Home Assistant Ready**: Designed specifically for HA integration patterns
- **Smart Error Handling**: Distinguishes between retriable and non-retriable errors
- **Modern Async**: Built on httpx with proper async/await support
- **Type Safe**: Full type hints and Pydantic model validation
- **Well Tested**: 96% test coverage with comprehensive error scenarios
- **OpenAPI Generated**: Auto-generated from official SPAN Panel API specs

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

### Long-Lived Pattern (Home Assistant Integration)

**Best for**: Home Assistant integrations, long-running services, persistent connections

```python
import asyncio
from span_panel_api import SpanPanelClient

class SpanPanelIntegration:
    """Example Home Assistant integration pattern."""

    def __init__(self, host: str):
        # Create client but don't use context manager
        self.client = SpanPanelClient(host)
        self._authenticated = False

    async def setup(self) -> None:
        """Initialize the integration (called once)."""
        try:
            # Authenticate once during setup
            await self.client.authenticate("home-assistant", "Home Assistant Integration")
            self._authenticated = True
        except Exception as e:
            await self.client.close()  # Clean up on setup failure
            raise

    async def update_data(self) -> dict:
        """Update all data (called periodically by HA coordinator)."""
        if not self._authenticated:
            await self.client.authenticate("home-assistant", "Home Assistant Integration")
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
        """Set circuit priority (called by HA service)."""
        if not self._authenticated:
            await self.client.authenticate("home-assistant", "Home Assistant Integration")
            self._authenticated = True

        await self.client.set_circuit_priority(circuit_id, priority)

    async def cleanup(self) -> None:
        """Cleanup when integration is unloaded."""
        await self.client.close()

# Usage in Home Assistant
async def main():
    integration = SpanPanelIntegration("192.168.1.100")

    try:
        await integration.setup()

        # Simulate HA coordinator updates
        for i in range(10):
            data = await integration.update_data()
            print(f"Update {i}: {len(data['circuits'].circuits.additional_properties)} circuits")
            await asyncio.sleep(30)  # HA typically updates every 30 seconds

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

| Pattern | Use Case | Pros | Cons |
|---------|----------|------|------|
| **Context Manager** | Scripts, one-off tasks, testing | Automatic cleanup • Exception safe • Simple code | Creates/destroys connection each time • Not efficient for frequent calls |
| **Long-Lived** | Home Assistant, services, daemons | Efficient connection reuse • Better performance • Authentication persistence | Manual lifecycle management • Must handle cleanup |
| **Manual** | Custom requirements, debugging | Full control • Custom error handling | Must remember to call close() • More error-prone |

## Error Handling for Home Assistant

The client provides sophisticated error categorization designed for Home Assistant's retry logic:

### Exception Types

```python
from span_panel_api.exceptions import (
    SpanPanelError,           # Base exception
    SpanPanelAPIError,        # General API errors
    SpanPanelAuthError,       # 401/403 - need re-authentication
    SpanPanelConnectionError, # Network connectivity issues
    SpanPanelTimeoutError,    # Request timeouts
    SpanPanelRetriableError,  # 502/503/504 - temporary issues, SHOULD retry
    SpanPanelServerError,     # 500 - application bugs, DO NOT retry
)
```

### HTTP Error Code Mapping

| Status Code | Exception | Retry? | Description | HA Action |
|-------------|-----------|--------|-------------|-----------|
| **Authentication Errors** |
| 401, 403    | `SpanPanelAuthError` | Once (after re-auth) | Authentication required/failed | Re-authenticate and retry once |
| **Non-Retriable Server Errors** |
| 500         | `SpanPanelServerError` | **NO** | Internal server error (SPAN bug) | Show error, do not retry |
| **Retriable Server Errors** |
| 502         | `SpanPanelRetriableError` | Yes | Bad Gateway (proxy error) | Retry with exponential backoff |
| 503         | `SpanPanelRetriableError` | Yes | Service Unavailable | Retry with exponential backoff |
| 504         | `SpanPanelRetriableError` | Yes | Gateway Timeout | Retry with exponential backoff |
| **Other HTTP Errors** |
| 404, 400, etc | `SpanPanelAPIError` | Case by case | Client/request errors | Check request parameters |
| **Network Errors** |
| Connection failures | `SpanPanelConnectionError` | Yes | Network connectivity issues | Retry with backoff |
| Timeouts | `SpanPanelTimeoutError` | Yes | Request timed out | Retry with backoff |

### Retry Strategy for HA

```python
async def ha_friendly_request():
    """Example showing HA-appropriate error handling."""
    try:
        return await client.get_circuits()
    except SpanPanelAuthError:
        # Re-authenticate and retry once
        await client.authenticate("ha", "Home Assistant")
        return await client.get_circuits()
    except SpanPanelRetriableError as e:
        # Temporary server issues - HA should retry with backoff
        # 502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout
        logger.warning(f"Retriable error {e.status_code}, will retry: {e}")
        raise  # Let HA handle the retry
    except SpanPanelServerError as e:
        # Application bugs on SPAN side - DO NOT retry
        # 500 Internal Server Error (SPAN Panel bug, not your fault!)
        logger.error(f"Server error {e.status_code}, not retrying: {e}")
        raise  # HA will show notification but won't waste resources retrying
    except (SpanPanelConnectionError, SpanPanelTimeoutError):
        # Network issues - HA should retry
        raise
```

### Exception Handling

The client properly configures the underlying OpenAPI client with `raise_on_unexpected_status=True`, ensuring that HTTP errors (especially 500 responses) are converted to appropriate exceptions rather than being silently ignored.

## API Reference

### Client Initialization

```python
client = SpanPanelClient(
    host="192.168.1.100",    # Required: SPAN Panel IP
    port=80,                 # Optional: default 80
    timeout=30.0,            # Optional: request timeout
    use_ssl=False            # Optional: HTTPS (usually False for local)
)
```

### Authentication

```python
# Register a new API client (one-time setup)
auth = await client.authenticate(
    name="my-integration",           # Required: client name
    description="My Home Assistant"  # Optional: description
)
# Token is automatically stored and used for subsequent requests
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

## Development Setup

### Prerequisites
- Python 3.12+ (SPAN Panel requires Python 3.12+)
- [Poetry](https://python-poetry.org/) for dependency management

### Installation

```bash
# Clone and install
git clone <repository code URL>
cd span-panel-api
poetry install
poetry env activate

# Run tests
poetry run pytest

# Check coverage
python scripts/coverage.py
```

### Project Structure

```
span_openapi/
├── src/span_panel_api/        # Main client library
│   ├── client.py              # SpanPanelClient (high-level wrapper)
│   ├── exceptions.py          # Exception hierarchy
│   ├── const.py               # HTTP status constants
│   └── generated_client/      # Auto-generated OpenAPI client
├── tests/                     # test suite
├── scripts/coverage.py        # Coverage checking utility
├── openapi.json              # SPAN Panel OpenAPI specification
└── pyproject.toml            # Poetry configuration
```

## Home Assistant Integration

This client is specifically designed for Home Assistant integrations:

### Entity Updates

```python
async def update_entities():
    """Update all HA entities from SPAN Panel."""
    try:
        circuits = await client.get_circuits()
        panel_state = await client.get_panel_state()
        storage = await client.get_storage_soe()

        # Update your HA entities here

    except SpanPanelRetriableError:
        # Temporary issue - HA will retry automatically
        raise UpdateFailed("SPAN Panel temporarily unavailable")
    except SpanPanelServerError:
        # Don't retry server errors
        raise UpdateFailed("SPAN Panel server error")
    except SpanPanelAuthError:
        # Re-authenticate and retry once
        await client.authenticate("ha", "Home Assistant")
        # Then retry the update...
```

### Service Calls

```python
async def set_circuit_state(circuit_id: str, state: str):
    """HA service call to control circuit."""
    try:
        await client.set_circuit_relay(circuit_id, state)
    except SpanPanelRetriableError:
        # Let HA handle retries
        raise HomeAssistantError("Temporary error, will retry")
    except SpanPanelServerError:
        # Don't retry
        raise HomeAssistantError("SPAN Panel error, please try again later")
```

## Advanced Usage

### SSL Configuration

```python
# For panels with custom certificates
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
python scripts/coverage.py --check --threshold 95

# Run with coverage
poetry run pytest --cov=span_panel_api tests/
```

## Contributing

1. Get `openapi.json`  SPAN Panel API specs

    (for example via REST Client extension)

    GET <https://span-panel-ip/api/v1/openapi.json>

2. Regenerate client: `poetry run python generate_client.py`
3. Update wrapper client in `src/span_panel_api/` if needed
4. Add tests for new functionality
5. Ensure coverage stays above 95%
6. Update this README if adding new features

## License

MIT License - see LICENSE file for details.

---
