# SPAN Panel OpenAPI Client

A modern httpx-based Python client library for accessing the SPAN Panel API, generated from OpenAPI specifications.

## Development Setup

### Prerequisites
- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management

### 1. Install Dependencies

First, install the project dependencies using Poetry:

```bash
# Install all dependencies including development tools
poetry install

# Install with specific groups
poetry install --with dev,generate
```

### 2. Generate the Client

The client is generated from the OpenAPI specification using `openapi-python-client`. Use the provided generation script:

```bash
# Generate the httpx-based client from openapi.json
poetry run python generate_client.py
```

This will:
- Clean any existing `generated_client/` directory
- Generate a new httpx-based client in `generated_client/`
- Handle naming conflicts and formatting issues automatically

### 3. Build the Package

After generation, you can build the package:

```bash
# Build wheel and source distribution
poetry build

# Install in development mode
poetry install
```

## Project Structure

```
span_openapi/
├── generate_client.py          # Root generation script
├── generated_client/           # Raw OpenAPI generated files (httpx-based)
│   ├── client.py              # Client and AuthenticatedClient classes
│   ├── api/default/           # API endpoint functions
│   ├── models/                # Pydantic data models
│   └── types.py               # Type definitions
├── src/span_panel_api/        # Wrapper client library
├── tests/                     # Test files
├── openapi.json               # OpenAPI specification
└── pyproject.toml             # Poetry configuration
```

## Usage

### Basic Client Usage

```python
from generated_client import Client

client = Client(base_url="https://your-span-panel.local")
```

For authenticated endpoints, use `AuthenticatedClient`:

```python
from generated_client import AuthenticatedClient

client = AuthenticatedClient(
    base_url="https://your-span-panel.local",
    token="your-api-token"
)
```

### API Calls

All API endpoints are available as functions with both sync and async variants:

```python
from generated_client.api.default import system_status_api_v1_status_get

# Synchronous
with client as client:
    status = system_status_api_v1_status_get.sync(client=client)

    # Or with detailed response info
    response = system_status_api_v1_status_get.sync_detailed(client=client)
    print(f"Status: {response.status_code}")
    print(f"Data: {response.parsed}")
```

### Async Usage

```python
import asyncio
from generated_client.api.default import system_status_api_v1_status_get

async def get_status():
    async with client as client:
        status = await system_status_api_v1_status_get.asyncio(client=client)
        return status

# Run async function
status = asyncio.run(get_status())
```

## Development Workflow

1. **Update OpenAPI Spec**: Update `openapi.json` with latest API changes
2. **Regenerate Client**: Run `poetry run python generate_client.py`
3. **Update Wrapper**: Modify `src/span_panel_api/` if needed (⚠️ currently needs updating for httpx client)
4. **Test**: Run tests with `poetry run pytest`
5. **Build**: Create package with `poetry build`

> **Note**: The wrapper client in `src/span_panel_api/` was written for the previous urllib3-based generated client and needs updating to work with the new httpx-based client. For now, use the generated client directly from `generated_client/`.

## Advanced Configuration

### SSL Configuration

```python
from generated_client import AuthenticatedClient

# Custom certificate bundle
client = AuthenticatedClient(
    base_url="https://your-span-panel.local",
    token="your-token",
    verify_ssl="/path/to/certificate_bundle.pem",
)

# Disable SSL verification (not recommended for production)
client = AuthenticatedClient(
    base_url="https://your-span-panel.local",
    token="your-token",
    verify_ssl=False
)
```

### Custom httpx Configuration

```python
from generated_client import Client

client = Client(
    base_url="https://your-span-panel.local",
    httpx_args={
        "timeout": 30.0,
        "event_hooks": {
            "request": [lambda req: print(f"Request: {req.method} {req.url}")],
            "response": [lambda resp: print(f"Response: {resp.status_code}")]
        }
    },
)
```

## API Reference

The generated client provides:

- **Client Classes**: `Client` and `AuthenticatedClient` for different authentication needs
- **API Functions**: Located in `generated_client.api.default.*` with sync/async variants
- **Models**: Pydantic models in `generated_client.models.*` for type-safe data handling
- **Types**: Common types and response wrappers in `generated_client.types`

Each API function provides four variants:
1. `sync()`: Blocking request returning parsed data or None
2. `sync_detailed()`: Blocking request returning full Response object
3. `asyncio()`: Async version of sync()
4. `asyncio_detailed()`: Async version of sync_detailed()

## Building / Publishing

This project uses [Poetry](https://python-poetry.org/) for dependency management and packaging:

```bash
# Update version in pyproject.toml, then:
poetry build
poetry publish  # For PyPI
# or
poetry publish -r your-private-repo  # For private repository
```

For development installation in other projects:

```bash
# In target project using Poetry:
poetry add /path/to/span_openapi

# Or install wheel directly:
pip install dist/span_openapi-*.whl
```
