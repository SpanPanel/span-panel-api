# Development Guide

## Prerequisites

- Python 3.12 or 3.13
- [Poetry](https://python-poetry.org/) for dependency management

## Setup

```bash
git clone <repository-url>
cd span-panel-api

# Activate the Poetry-managed environment
eval "$(poetry env activate)"

# Install all dependencies including dev extras
poetry install

# Install pre-commit hooks
poetry run pre-commit install
```

## Running Tests

```bash
# Full test suite
poetry run pytest

# With verbose output
poetry run pytest -v

# Specific test file
poetry run pytest tests/test_core_client.py -v

# With coverage
poetry run pytest --cov=span_panel_api tests/

# Generate HTML coverage report
python scripts/coverage.py --full

# Check coverage meets the threshold
python scripts/coverage.py --check --threshold 90
```

## Code Quality

```bash
# Run all pre-commit hooks on all files (lint, format, type-check, security)
poetry run pre-commit run --all-files

# Lint only
poetry run ruff check src/span_panel_api/

# Format code
poetry run ruff format src/span_panel_api/

# Type checking
poetry run mypy src/span_panel_api/

# Security audit
poetry run bandit -c pyproject.toml -r src/span_panel_api/
```

## Project Structure

```text
span-panel-api/
├── src/span_panel_api/          # Main library
│   ├── __init__.py              # Public API surface
│   ├── client.py                # SpanPanelClient — Gen2 REST client
│   ├── factory.py               # create_span_client — auto-detect factory
│   ├── protocol.py              # Protocol definitions for type-safe dispatch
│   ├── models.py                # Transport-agnostic data models
│   ├── simulation.py            # Simulation engine (Gen2 only)
│   ├── exceptions.py            # Exception hierarchy
│   ├── const.py                 # HTTP status constants
│   ├── phase_validation.py      # Solar / phase utilities
│   ├── generated_client/        # Auto-generated OpenAPI client (do not edit)
│   └── grpc/                    # Gen3 gRPC client
│       ├── client.py            # SpanGrpcClient
│       ├── models.py            # Low-level gRPC data models
│       └── const.py             # gRPC constants (port, trait IDs, etc.)
├── tests/                       # Test suite
│   ├── test_core_client.py
│   ├── test_context_manager.py
│   ├── test_cache_functionality.py
│   ├── test_enhanced_circuits.py
│   ├── test_simulation_mode.py
│   ├── test_factories.py
│   ├── conftest.py
│   └── simulation_fixtures/     # Pre-recorded API response fixtures
├── examples/                    # Example scripts and simulation configs
├── scripts/                     # Developer utility scripts
├── docs/                        # This documentation
├── openapi.json                 # SPAN Panel OpenAPI specification (Gen2)
└── pyproject.toml               # Poetry / project configuration
```

## Updating the Gen2 OpenAPI Client

The `generated_client/` directory is auto-generated from `openapi.json`. Do not edit it manually.

1. Obtain a fresh `openapi.json` from a live panel:

   ```text
   GET http://<panel-ip>/api/v1/openapi.json
   ```

2. Replace `openapi.json` in the repo root.

3. Regenerate:

   ```bash
   poetry run python generate_client.py
   ```

4. Update `src/span_panel_api/client.py` if the API surface changed.

5. Add or update tests for any changed behaviour.

## Gen3 gRPC Development

The Gen3 client uses manual protobuf encoding/decoding to avoid generated stubs, keeping the dependency surface to the single optional `grpcio` package.

Key files:

- `grpc/client.py` — `SpanGrpcClient` implementation, protobuf helpers, metric decoders
- `grpc/models.py` — `CircuitInfo`, `CircuitMetrics`, `PanelData`
- `grpc/const.py` — port number, trait IDs, product identifiers

The gRPC client connects to `TraitHandlerService` at port 50065 and uses three RPC methods:

| RPC            | Purpose                          |
| -------------- | -------------------------------- |
| `GetInstances` | Discover circuit trait instances |
| `GetRevision`  | Fetch circuit names by trait IID |
| `Subscribe`    | Stream real-time power metrics   |

## Adding a New Feature

1. If adding a new API capability, update `PanelCapability` in `models.py`.
2. If adding a new method to both transports, add it to the appropriate `Protocol` in `protocol.py`.
3. Add type hints and docstrings to all new public functions and classes.
4. Write tests covering the new code (target > 80% coverage for new code).
5. Update the relevant `docs/` page.

## Release Process

Versioning follows [Semantic Versioning](https://semver.org/).

1. Update `__version__` in `src/span_panel_api/__init__.py`.
2. Update `CHANGELOG.md`.
3. Run the full test suite and pre-commit hooks.
4. Tag and push — CI will publish to PyPI automatically.
