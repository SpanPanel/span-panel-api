# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.15] - 2/19/2026

### Added

- **Gen3 gRPC transport** (`grpc/` subpackage): `SpanGrpcClient` connects to Gen3 panels (MAIN40 / MLO48) on port 50065 via manual protobuf encoding. Supports push-streaming via `Subscribe` RPC with registered callbacks. No authentication required. Thanks
  to @Griswoldlabs for the Gen3 implementation (PR #169 in `SpanPanel/span`).
- **Protocol abstraction**: `SpanPanelClientProtocol` and capability-mixin protocols (`AuthCapableProtocol`, `CircuitControlProtocol`, `StreamingCapableProtocol`, etc.) provide static type-safe dispatch across transports.
- **`PanelCapability` flags**: Runtime advertisement of transport features. Gen2 advertises `GEN2_FULL`; Gen3 advertises `GEN3_INITIAL` (`PUSH_STREAMING` only).
- **Unified snapshot model**: `SpanPanelSnapshot` and `SpanCircuitSnapshot` are returned by `get_snapshot()` on both transports. Gen2- and Gen3-only fields are `None` where not applicable.
- **`create_span_client()` factory** (`factory.py`): Creates the appropriate client by generation or auto-detects by probing Gen2 HTTP then Gen3 gRPC.
- **Circuit IID mapping fix**: `_parse_instances()` now collects trait-16 and trait-26 IIDs independently, deduplicates and sorts both lists, and pairs them by position. A `_metric_iid_to_circuit` reverse map enables O(1) streaming lookup. Replaces the
  hardcoded `METRIC_IID_OFFSET` assumption that failed on MLO48 panels.
- **gRPC exception classes**: `SpanPanelGrpcError`, `SpanPanelGrpcConnectionError`.
- **`grpcio` optional dependency**: Install with `span-panel-api[grpc]` for Gen3 support.

## [1.1.14] - 12/2025

### Fixed in v1.1.14

- Recognize panel Keep-Alive at 5 sec, Handle httpx.RemoteProtocolError defensively

## [1.1.9] - 9/2025

### Fixed in v1.1.9

- Simulation mode sign correction for solar and battery power values
- Fixed battery State of Energy (SOE) calculation to use configured battery behavior instead of hardcoded time-of-day assumptions

### Changed in v1.1.9

- Updated GitHub Actions setup-python from v5 to v6
- Updated dev dependencies group

## [1.1.8] - 2024

### Fixed in v1.1.8

- Fixed sign on power values in simulation mode

### Changed in v1.1.8

- Updated virtualenv from 20.33.0 to 20.34.0
- Updated GitHub Actions checkout from v4 to v5

## [1.1.6] - 2024

### Added in v1.1.6

- Enhanced simulation API with YAML configuration and dynamic overrides
- Battery behavior simulation capabilities
- Phase validation functionality
- Support for host field as serial number in simulation mode
- Time-based energy accumulation in simulation
- Power fluctuation patterns for different appliance types
- Per-circuit and per-branch variation controls

### Fixed in v1.1.6

- Fixed authentication in simulation mode
- Fixed locking issues in simulation mode
- Fixed energy accumulation in simulation
- Fixed cache for unmapped circuits
- Fixed code complexity issues
- Fixed pylint and mypy errors
- Fixed spelling errors

### Changed in v1.1.6

- Refactored simulation to reduce code complexity
- Updated multiple dependencies including:
  - virtualenv from 20.32.0 to 20.33.0
  - coverage from 7.9.2 to 7.10.2
  - numpy from 2.3.1 to 2.3.2
  - docutils from 0.21.2 to 0.22
  - rich from 14.0.0 to 14.1.0
  - anyio from 4.9.0 to 4.10.0
  - certifi from 2025.7.14 to 2025.8.3
- Updated dependabot configuration
- Enhanced documentation and linting

### Removed in v1.1.6

- Removed unused client_utils.py

## [1.1.5] - 2024

### Added in v1.1.5

- Simulation mode enhancements
- Test coverage for simulation edge cases
- Panel constants and simulation demo improvements

### Fixed in v1.1.5

- Fixed panel constants and simulation demo
- Fixed energy accumulation in simulation
- Fixed demo script

### Changed in v1.1.5

- Updated pytest-asyncio in dev-dependencies
- Updated certifi from 2025.7.9 to 2025.7.14
- Adjusted code coverage and excluded test/examples from codefactor

## [1.1.4] - 2024

### Added in v1.1.4

- Formatting and linting scripts

### Removed in v1.1.4

- Removed unused client_utils.py

## [1.1.3] - 2024

### Fixed in v1.1.3

- Fixed tests and linting errors
- Fixed pylint and mypy errors
- Excluded defensive code from coverage

## [1.1.2] - 2024

### Added in v1.1.2

- **Simulation mode** - Complete simulation system for development and testing without physical SPAN panel
- Dead code checking
- Test coverage for simulation mode

### Changed in v1.1.2

- Updated multiple dependencies including:
  - openapi-python-client from 0.25.1 to 0.25.2
  - typing-extensions to 4.14.1
  - cryptography from 45.0.4 to 45.0.5
  - urllib3 from 2.4.0 to 2.5.0
  - pygments from 2.19.1 to 2.19.2
  - jaraco-functools from 4.1.0 to 4.2.1
- Updated ruff configuration
- Moved uncategorized tests to appropriate files

### Fixed in v1.1.2

- Addressed unused variables
- Adapted tests to use simulation mode
- Increased test coverage for simulation mode

## [1.1.1] - 2024

### Changed in v1.1.1

- Updated openapi-python-client
- Upgraded pytest-asyncio to 0.21.0
- Upgraded openapi-python-client to 0.24.0 and regenerated client
- Loosened ruff dependency constraints

### Fixed in v1.1.1

- Fixed tests compatibility issues

## [1.1.0] - 2024

### Added in v1.1.0

- Initial release of SPAN Panel API client library
- Support for Python 3.12 and 3.13
- Context manager pattern for automatic connection lifecycle management
- Long-lived pattern for services and integration platforms
- Manual pattern for custom connection management
- Authentication system with token-based API access
- Panel status and state retrieval
- Circuit control (relay and priority management)
- Battery storage information (SOE - State of Energy)
- Virtual circuits for unmapped panel tabs
- Timeout and retry configuration
- Time-based caching system
- Error categorization with specific exception types
- Home Assistant integration compatibility layer
- SSL configuration support
- Performance features including caching
- Development setup with Poetry
- Testing and coverage tools
- Pre-commit hooks and code quality tools

### Features in v1.1.0

- **Client Patterns**: Context manager, long-lived, and manual connection patterns
- **Authentication**: Token-based authentication with automatic token management
- **Panel Control**: Complete panel status, state, and circuit management
- **Error Handling**: Categorized exceptions with retry strategies
- **Performance**: Built-in caching and timeout/retry configuration
- **Integration**: Home Assistant compatibility layer
- **Development**: Complete development toolchain with testing and linting

---

## Version History Summary

- **v1.1.0**: Initial release with core API client functionality
- **v1.1.1**: Dependency updates and test fixes
- **v1.1.2**: Major addition of simulation mode for development/testing
- **v1.1.3**: Code quality improvements and test fixes
- **v1.1.4**: Formatting and linting enhancements
- **v1.1.5**: Simulation mode enhancements and edge case testing
- **v1.1.6**: Major simulation API improvements with YAML configuration
- **v1.1.8**: Power sign correction in simulation mode
- **v1.1.9**: Additional simulation sign corrections and dependency updates
