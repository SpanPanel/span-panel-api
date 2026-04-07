# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5.2] - 04/2026

### Fixed

- **Clear stale property values on panel reboot** — after a panel reboot, snapshots could mix pre-reboot and post-reboot data. The accumulator now detects reboots (including fast reboots where the broker LWT is skipped) and clears stale state before
  building the next snapshot.
- **Snapshot cache invalidated on reboot** — the snapshot cache is now discarded when a reboot is detected, forcing a full rebuild from fresh data.

## [2.5.1] - 04/2026

### Fixed

- **Replaced `assert` with `RuntimeError` in production code** — `HomieDeviceConsumer._rebuild_dirty_circuits()` used an `assert` to guard a cached-snapshot invariant, which would be silently stripped by `python -O`. Replaced with an explicit
  `RuntimeError` raise.
- **Fixed broken bandit pre-commit hook** — bandit was pinned to v1.8.3, which is incompatible with Python 3.14. It silently skipped all source files (20/20) and reported "Passed" with zero issues. Bumped to v1.9.4 which scans all files correctly.

## [2.5.0] - 03/2026

### Added

- **`HomiePropertyAccumulator`** — new layer that handles generic Homie v5 protocol parsing (message routing, property/target storage, dirty-node tracking) with an explicit lifecycle state machine (`HomieLifecycle`), cleanly separated from SPAN-specific
  snapshot construction.
- **`$target` property support** — `SpanCircuitSnapshot` gains `relay_state_target` and `priority_target` fields, surfacing the desired-vs-actual state for relay and shed-priority commands.
- **Dirty-node snapshot caching** — `HomieDeviceConsumer.build_snapshot()` tracks which nodes changed since the last build and returns a cached snapshot when nothing is dirty, reducing per-scan CPU cost on constrained hardware.

### Changed

- **Layered Homie consumer architecture** — `HomieDeviceConsumer` no longer handles protocol plumbing. It reads from `HomiePropertyAccumulator` via a query API (`get_prop`, `get_target`, `nodes_by_type`, etc.) and focuses solely on SPAN domain
  interpretation: power sign normalization, DSM derivation, unmapped tab synthesis, and snapshot assembly.
- **`SpanMqttClient` composes both layers** — `connect()` creates an accumulator and wires it into the consumer. The public client API is unchanged.
- **Property callbacks fire only on value change** — retained messages replaying already-known values no longer trigger callback storms on MQTT reconnect.

## [2.4.2] - 03/2026

### Fixed

- **Moved SSL context creation to executor** — `httpx.AsyncClient()` eagerly calls `ssl.SSLContext.load_verify_locations()` with the system CA bundle, which is a blocking file I/O operation that triggers Home Assistant's event loop protection. The SSL
  context is now created in an executor thread and passed to httpx via `verify=ctx`.

## [2.4.1] - 03/2026

### Fixed

- **Added `license = "MIT"` to package metadata** — the `pyproject.toml` was missing the license field, causing license audit failures in downstream projects (HA core hassfest).
- **Loosened httpx version constraint** — changed from `>=0.28.1,<0.29.0` to `>=0.28.1` to satisfy HA core hassfest version restriction checks.

## [2.4.0] - 03/2026

### Added

- **`proximity_proven` on `V2StatusInfo`** — parsed from the v2 status endpoint response (firmware 202609+). Returns `None` on older panels where the field is absent, allowing callers to distinguish "not proven" from "unknown."
- **`HomieSchemaTypes` type alias** — replaces raw `dict[str, dict[str, object]]` throughout the codebase for Homie schema type signatures.
- **`log_schema_drift` test coverage** — raised `field_metadata.py` coverage from 58% to 98%.

### Changed

- **Injected HTTP client for v2 auth** — `detect_api_version`, `register_v2`, `download_ca_cert`, and other bootstrap functions accept an optional `httpx_client` parameter. Consumers (e.g. Home Assistant) can pass their managed client instead of the
  library creating ad-hoc ones.
- **Blocking file I/O moved to executor** — temp CA cert file write and cleanup in `AsyncMqttBridge.connect()` and `disconnect()` now run in an executor thread instead of on the event loop.
- **Narrowed CA cert download exception handling** — `connect()` catches specific `OSError`, `SpanPanelConnectionError`, and `SpanPanelTimeoutError` instead of bare `Exception` when fetching the CA certificate.
- **Removed `verify=False` from fallback HTTP client** — the library's internal fallback `httpx.AsyncClient` no longer sets `verify=False`. All bootstrap URLs are plain HTTP so the flag was irrelevant; removing it avoids misleading security impressions.

### Removed

- **59 low-value tests** — stripped tests that exercised Python language mechanics (dataclass construction, frozen, slots, IntFlag), tautological assertions, fragile source-code string inspection, redundant export checks, and duplicates across files. Test
  count: 310 → 251, coverage maintained at 96%.

## [2.3.2] - 03/2026

### Added

- **FQDN management endpoints** — `register_fqdn()`, `get_fqdn()`, `delete_fqdn()` for managing the panel's TLS certificate SAN via `/api/v2/dns/fqdn` ([spanio/SPAN-API-Client-Docs#10](https://github.com/spanio/SPAN-API-Client-Docs/issues/10))

## [2.3.1] - 03/2026

### Fixed

- **MQTT connection errors now wrapped as `SpanPanelConnectionError`** — `OSError` subclasses raised during MQTT broker connection (DNS resolution failure, connection refused, network unreachable, etc.) are now caught and wrapped as
  `SpanPanelConnectionError`. Previously these propagated as unhandled exceptions, preventing consumers from handling them gracefully.

## [2.3.0] - 03/2026

### Removed

- **Simulation engine removed** — `DynamicSimulationEngine`, `SimulationConfig`, and all simulation-related modules have been removed from the library. Simulation is now handled by the standalone SPAN Panel Simulator add-on.

## [2.2.4] - 03/2026

### Fixed

- **Negative zero on idle circuits** — Circuit power negation (`-raw_power_w`) produced IEEE 754 `-0.0` when the panel reported `0.0` for an idle circuit. The value is now normalized to positive zero after negation.

## [2.2.3] - 03/2026

### Changed

- **Panel size sourced from Homie schema** — `panel_size` is now derived from the circuit `space` property format in the Homie schema (`GET /api/v2/homie/schema`), which declares the valid range as `"1:N:1"` where N is the panel size. This replaces a
  non-deterministic heuristic that inferred panel size from the highest occupied breaker tab, which would undercount when trailing positions were empty.
- **`SpanMqttClient.connect()` fetches schema internally** — the client automatically calls `get_homie_schema()` during `connect()` and passes the panel size to `HomieDeviceConsumer`. Callers no longer need to fetch or pass `panel_size`.
- **`SpanPanelSnapshot.panel_size`** — type changed from `int | None` to `int`; always populated from the schema
- **`V2HomieSchema.panel_size`** — new property that parses the schema's circuit space format to extract the authoritative panel size
- **`V2HomieSchema` exported** from package public API
- **`HomieDeviceConsumer` requires `panel_size`** — new required constructor parameter; unmapped tabs now fill to the schema-defined panel size rather than deriving from circuit data
- **`create_span_client()` simplified** — `panel_size` parameter removed; schema is fetched internally by `SpanMqttClient.connect()`

### Removed

- **MQTT `core/panel-size` topic parsing** — removed from `HomieDeviceConsumer`; panel size comes from the schema, not a runtime MQTT property

## [2.0.0] - 02/2026

v2.0.0 is a ground-up rewrite. The REST/OpenAPI transport has been removed entirely in favor of MQTT/Homie — the SPAN Panel's native v2 protocol. This is a breaking change: all consumer code must be updated to use the new API surface.

### v1.x Sunset

Package versions prior to 2.0.0 depend on the SPAN v1 REST API. SPAN will sunset v1 firmware at the end of 2026, at which point v1.x releases of this package will cease to function. Users should upgrade to 2.0.0.

### Breaking Changes

- **REST transport removed** — `SpanPanelClient`, `SpanRestClient`, the `generated_client/` OpenAPI layer, and all REST-related modules have been deleted
- **No more polling** — `get_status()`, `get_panel_state()`, `get_circuits()`, `get_storage_soe()` replaced by `get_snapshot()` returning a single `SpanPanelSnapshot`
- **Protocol-based API** — consumers code against `SpanPanelClientProtocol`, `CircuitControlProtocol`, and `StreamingCapableProtocol` (PEP 544), not concrete classes
- **Authentication changed** — passphrase-based v2 registration via `register_v2()` replaces v1 token-based auth; factory handles this automatically
- **paho-mqtt is now required** — moved from optional `[mqtt]` extra to a core dependency
- **Circuit IDs are UUIDs** — dashless UUID strings replace integer circuit IDs
- **Shed priority values changed** — v2 uses `NEVER` / `SOC_THRESHOLD` / `OFF_GRID` instead of v1's `MUST_HAVE` / `NICE_TO_HAVE` / `NON_ESSENTIAL`
- **`SpanPanelRetriableError` removed** — retry logic is no longer in the library (no REST polling)
- **`set_async_delay_func()` removed** — no retry delay hook needed for MQTT transport
- **`cache_window` parameter removed** — no caching needed; MQTT delivers state changes in real time
- **`attrs`, `python-dateutil` dependencies removed**

### Added

- **MQTT/Homie transport** (`span_panel_api.mqtt`):
  - `SpanMqttClient` — implements all three protocols (panel, circuit control, streaming)
  - `AsyncMqttBridge` — paho-mqtt v2 wrapper with TLS/WebSocket, event-loop-driven socket I/O (no threads)
  - `HomieDeviceConsumer` — Homie v5 state machine parsing MQTT topics into snapshots
  - `MqttClientConfig` — frozen configuration with transport type and TLS settings
- **Snapshot dataclasses** — immutable `SpanPanelSnapshot`, `SpanCircuitSnapshot`, `SpanBatterySnapshot`, `SpanPVSnapshot`, `SpanEvseSnapshot` with v2-native fields
- **v2 auth functions** — `register_v2()`, `download_ca_cert()`, `get_homie_schema()`, `regenerate_passphrase()`
- **API version detection** — `detect_api_version()` probes `/api/v2/status` and returns `DetectionResult`
- **Factory function** — `create_span_client()` handles registration and returns a configured `SpanMqttClient`
- **PV/BESS metadata** — vendor name, product name, nameplate capacity parsed from Homie device tree
- **Power flows** — `power_flow_pv`, `power_flow_battery`, `power_flow_grid`, `power_flow_site` on panel snapshot
- **Lugs current** — per-phase upstream/downstream current (A) on panel snapshot
- **Per-leg voltages** — `l1_voltage`, `l2_voltage` on panel snapshot
- **Panel metadata** — `dominant_power_source`, `vendor_cloud`, `wifi_ssid`, `panel_size`, `main_breaker_rating_a`
- **Streaming callbacks** — `register_snapshot_callback()` + `start_streaming()` / `stop_streaming()` for real-time push
- **Snapshot debounce** — `snapshot_interval` parameter on `SpanMqttClient` (default 1.0s) rate-limits `build_snapshot()` + callback dispatch; set to 0 for immediate (no debounce). Runtime adjustment via `set_snapshot_interval()`
- **`PanelCapability` flag enum** — runtime feature advertisement (`EBUS_MQTT`, `PUSH_STREAMING`, `CIRCUIT_CONTROL`, `BATTERY_SOE`)

### Changed

- `412 Precondition Failed` now treated as auth error (`AUTH_ERROR_CODES` updated)
- Version bumped from 1.1.14 to 2.0.0
- Python requirement relaxed to `>=3.10` (from `3.12+`)

### Removed

- `src/span_panel_api/rest/` — entire REST client directory
- `src/span_panel_api/client.py` — backward-compat shim
- `src/span_panel_api/generated_client/` — OpenAPI v1 generated models
- `generate_client.py` — OpenAPI client generator script
- `examples/` directory (YAML configs moved to `tests/fixtures/configs/`)
- `DeprecationInfo`, `CircuitCorrelationProtocol`, `CorrelationUnavailableError`, `SpanPanelRetriableError`
- `PanelCapability.REST_V1`, `PanelCapability.SIMULATION` flags
- HTTP/retry constants from `const.py`
- `openapi.json` specification file

## [2.2.1] - 03/2026

### Added

- **`PanelControlProtocol`** — new protocol interface for panel-level settable properties, separate from `CircuitControlProtocol`
- **`set_dominant_power_source()`** — publishes a Dominant Power Source override to the panel's core node via MQTT
- **`find_node_by_type()` made public** — renamed from `_find_node_by_type()` on `HomieDeviceConsumer` to support external callers resolving node IDs by type

## [2.0.2] - 03/2026

### Added

- **EVSE snapshot model** — new `SpanEvseSnapshot` dataclass with status, lock state, advertised current, and device metadata (vendor, product, part number, serial number, software version)
- **EVSE Homie parsing** — `HomieDeviceConsumer._build_evse_devices()` extracts all 9 EVSE properties from `energy.ebus.device.evse` nodes
- **Multiple EVSE support** — `SpanPanelSnapshot.evse` dict keyed by node ID supports multiple commissioned chargers
- **EVSE simulation** — `DynamicSimulationEngine` generates EVSE snapshots for circuits with `device_type == "evse"`
- **`SpanEvseSnapshot` exported** from package public API

## [2.0.1] - 03/2026

### Added

- **Full BESS metadata parsing** — vendor name, product name, model, serial number, software version, nameplate capacity, and connected state from Homie BESS node
- **README documentation** — event-loop I/O architecture and circuit name synchronization sections

### Changed

- Bumped nodeenv dev dependency from 1.9.1 to 1.10.0

## [1.1.14] - 12/2025

### Fixed

- Recognize panel Keep-Alive at 5 sec, handle `httpx.RemoteProtocolError` defensively

## [1.1.9] - 9/2025

### Fixed

- Simulation mode sign correction for solar and battery power values
- Fixed battery State of Energy (SOE) calculation to use configured battery behavior instead of hardcoded time-of-day assumptions

### Changed

- Updated GitHub Actions setup-python from v5 to v6
- Updated dev dependencies group

## [1.1.8] - 2024

### Fixed

- Fixed sign on power values in simulation mode

### Changed

- Updated virtualenv from 20.33.0 to 20.34.0
- Updated GitHub Actions checkout from v4 to v5

## [1.1.6] - 2024

### Added

- Enhanced simulation API with YAML configuration and dynamic overrides
- Battery behavior simulation capabilities
- Phase validation functionality
- Support for host field as serial number in simulation mode
- Time-based energy accumulation in simulation
- Power fluctuation patterns for different appliance types
- Per-circuit and per-branch variation controls

### Fixed

- Fixed authentication in simulation mode
- Fixed locking issues in simulation mode
- Fixed energy accumulation in simulation
- Fixed cache for unmapped circuits

### Changed

- Refactored simulation to reduce code complexity

### Removed

- Removed unused client_utils.py

## [1.1.5] - 2024

### Added

- Simulation mode enhancements
- Test coverage for simulation edge cases

### Fixed

- Fixed panel constants and simulation demo
- Fixed energy accumulation in simulation

## [1.1.4] - 2024

### Added

- Formatting and linting scripts

### Removed

- Removed unused client_utils.py

## [1.1.3] - 2024

### Fixed

- Fixed tests and linting errors
- Excluded defensive code from coverage

## [1.1.2] - 2024

### Added

- **Simulation mode** — complete simulation system for development and testing without physical SPAN panel
- Dead code checking
- Test coverage for simulation mode

### Changed

- Updated ruff configuration
- Moved uncategorized tests to appropriate files

## [1.1.1] - 2024

### Changed

- Upgraded openapi-python-client to 0.24.0 and regenerated client
- Loosened ruff dependency constraints

### Fixed

- Fixed tests compatibility issues

## [1.1.0] - 2024

### Added

- Initial release of SPAN Panel API client library
- REST/OpenAPI transport for SPAN Panel v1 firmware
- Context manager, long-lived, and manual connection patterns
- Authentication system with token-based API access
- Panel status and state retrieval
- Circuit control (relay and priority management)
- Battery storage information (SOE)
- Virtual circuits for unmapped panel tabs
- Timeout and retry configuration with exponential backoff
- Time-based caching system
- Error categorization with specific exception types
- Home Assistant integration compatibility layer
- Simulation mode for testing without physical hardware
- Development toolchain with Poetry, pytest, mypy, ruff

---

## Version History Summary

| Version    | Date    | Transport  | Summary                                                                                           |
| ---------- | ------- | ---------- | ------------------------------------------------------------------------------------------------- |
| **2.5.2**  | 04/2026 | MQTT/Homie | Clear stale property values on panel reboot; fast reboot detection; cache generation invalidation |
| **2.5.1**  | 04/2026 | MQTT/Homie | Replace assert with RuntimeError; fix bandit pre-commit hook                                      |
| **2.5.0**  | 03/2026 | MQTT/Homie | Homie accumulator layer, $target support, dirty-node snapshot caching                             |
| **2.4.2**  | 03/2026 | MQTT/Homie | SSL context creation moved to executor                                                            |
| **2.4.1**  | 03/2026 | MQTT/Homie | License metadata, loosened httpx constraint                                                       |
| **2.4.0**  | 03/2026 | MQTT/Homie | proximityProven, injected HTTP client, executor file I/O, type alias, test cleanup                |
| **2.3.2**  | 03/2026 | MQTT/Homie | FQDN management endpoints                                                                         |
| **2.3.1**  | 03/2026 | MQTT/Homie | MQTT connection errors wrapped as SpanPanelConnectionError                                        |
| **2.3.0**  | 03/2026 | MQTT/Homie | Simulation engine removed                                                                         |
| **2.2.4**  | 03/2026 | MQTT/Homie | Negative zero fix on idle circuits                                                                |
| **2.2.3**  | 03/2026 | MQTT/Homie | Panel size from Homie schema; `panel_size` always populated on snapshot                           |
| **2.0.2**  | 03/2026 | MQTT/Homie | EVSE (EV charger) snapshot model, Homie parsing, simulation support                               |
| **2.0.1**  | 03/2026 | MQTT/Homie | Full BESS metadata parsing, README documentation                                                  |
| **2.0.0**  | 02/2026 | MQTT/Homie | Ground-up rewrite: MQTT-only, protocol-based API, real-time push, PV/BESS metadata                |
| **1.1.14** | 12/2025 | REST       | Keep-Alive and RemoteProtocolError handling                                                       |
| **1.1.9**  | 9/2025  | REST       | Simulation sign corrections                                                                       |
| **1.1.8**  | 2024    | REST       | Simulation power sign fix                                                                         |
| **1.1.6**  | 2024    | REST       | YAML simulation API, battery simulation                                                           |
| **1.1.5**  | 2024    | REST       | Simulation edge cases                                                                             |
| **1.1.4**  | 2024    | REST       | Formatting and linting                                                                            |
| **1.1.3**  | 2024    | REST       | Test and lint fixes                                                                               |
| **1.1.2**  | 2024    | REST       | Simulation mode added                                                                             |
| **1.1.1**  | 2024    | REST       | Dependency updates                                                                                |
| **1.1.0**  | 2024    | REST       | Initial release                                                                                   |
