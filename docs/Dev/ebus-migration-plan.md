# Cut REST, Go MQTT-Only — span-panel-api 2.0

## Context

v2 firmware is rolling out. Users will be told not to upgrade the integration until they have v2 firmware. This eliminates the need for dual-transport support. The REST client (~1430 lines), generated OpenAPI client, virtual circuits, object pooling, delay
registry, and branch handling are all dead weight.

The snapshot model (`SpanPanelSnapshot`) is **retained** — it serves as the library-to-integration contract and provides natural update coalescing (MQTT property updates accumulate cheaply, snapshot built on coordinator interval, entities only write state
when values change).

Circuit correlation (v1→v2 UUID mapping) moves to the **integration layer** — the integration already has v1 circuit IDs in its entity registry and can match against v2 snapshots without a live REST client.

Simulation engine is **retained** — no REST dependency, produces snapshots directly.

---

## Step 1: Move `auth_v2.py` → `auth.py`, delete `rest/` and `generated_client/`

### Move (content unchanged)

`src/span_panel_api/rest/auth_v2.py` → `src/span_panel_api/auth.py`

Functions preserved: `register_v2()`, `download_ca_cert()`, `get_homie_schema()`, `regenerate_passphrase()`, `get_v2_status()`, `_str()`, `_int()`

### Import updates

| File                                    | Old                                            | New                                                                                        |
| --------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `src/span_panel_api/mqtt/connection.py` | `from ..rest.auth_v2 import download_ca_cert`  | `from ..auth import download_ca_cert`                                                      |
| `src/span_panel_api/factory.py`         | `from .rest.auth_v2 import register_v2`        | `from .auth import register_v2`                                                            |
| `src/span_panel_api/__init__.py`        | `from .rest import ..., download_ca_cert, ...` | `from .auth import download_ca_cert, get_homie_schema, regenerate_passphrase, register_v2` |

### Delete entirely

| Path                                                      | Reason                                                    |
| --------------------------------------------------------- | --------------------------------------------------------- |
| `src/span_panel_api/rest/` (entire directory)             | v1 REST transport, delay, snapshot, virtual circuits      |
| `src/span_panel_api/client.py`                            | Backward-compat shim (`SpanPanelClient = SpanRestClient`) |
| `src/span_panel_api/generated_client/` (entire directory) | OpenAPI v1 generated models                               |
| `generate_client.py`                                      | OpenAPI client generator script                           |

### Verify

No remaining imports from `rest.` or `generated_client.` anywhere in `src/`.

---

## Step 2: Simplify protocols, models, exceptions, const

### `src/span_panel_api/protocol.py`

- Remove `REST_V1` and `SIMULATION` from `PanelCapability` flag enum
- Remove `CircuitCorrelationProtocol` entirely
- Keep: `SpanPanelClientProtocol`, `CircuitControlProtocol`, `StreamingCapableProtocol`

### `src/span_panel_api/models.py`

- Remove `DeprecationInfo` dataclass
- Remove `deprecation_info` field from `SpanPanelSnapshot`
- Keep `SpanBranchSnapshot` (MQTT returns empty list; model stays for type completeness)

### `src/span_panel_api/exceptions.py`

- Remove `CorrelationUnavailableError`
- Remove `SpanPanelRetriableError`
- Keep all others

### `src/span_panel_api/const.py`

- Remove: `HTTP_*` status codes, `AUTH_ERROR_CODES`, `RETRIABLE_ERROR_CODES`, `SERVER_ERROR_CODES`, `RETRY_*` constants
- Keep: `DSM_*`, `PANEL_*`, `MAIN_RELAY_*` (used by simulation + MQTT derivation)

---

## Step 3: Simplify MQTT client and factory

### `src/span_panel_api/mqtt/client.py`

- Remove `set_correlation_source()` method
- Remove `get_circuit_correlation()` method
- Remove `_correlate_by_name_tabs()` static method
- Remove `_correlation_client` attribute from `__init__`
- Remove imports: `CorrelationUnavailableError`, `SpanPanelClientProtocol` (from protocol), `SpanCircuitSnapshot` (if only used by correlation), `denormalize_circuit_id` (if only used by correlation)

### `src/span_panel_api/factory.py`

Remove `RestClientConfig`, `_build_rest_client()`, v1 path, correlation injection. Simplified:

```python
async def create_span_client(
    host: str,
    passphrase: str | None = None,
    mqtt_config: MqttClientConfig | None = None,
    serial_number: str | None = None,
) -> SpanMqttClient:
```

- Remove: `from .rest.client import SpanRestClient`
- Remove: `access_token`, `auth_refresh_callback`, `simulation_mode`, `simulation_config_path`, `rest_config` params
- Remove: `RestClientConfig` dataclass
- Remove: `_build_rest_client()` function
- Remove: v1 branch in `create_span_client()`
- Remove: correlation injection in `_build_mqtt_client()`
- Inline `_build_mqtt_client()` into `create_span_client()` (no longer needs separate function)

### `src/span_panel_api/__init__.py`

Remove from imports and `__all__`:

- `SpanPanelClient`, `SpanRestClient`, `set_async_delay_func`
- `CircuitCorrelationProtocol`
- `CorrelationUnavailableError`, `SpanPanelRetriableError`

Update import source for auth functions: `from .auth import ...`

---

## Step 4: Delete REST test files and examples

### Test files to DELETE (~21 files)

| File                                         | Reason                             |
| -------------------------------------------- | ---------------------------------- |
| `tests/test_rest_client.py`                  | REST client                        |
| `tests/test_authentication.py`               | v1 auth                            |
| `tests/test_bearer_token_validation.py`      | v1 token                           |
| `tests/test_context_manager.py`              | REST async context                 |
| `tests/test_core_client.py`                  | REST core                          |
| `tests/test_enhanced_circuits.py`            | REST circuits                      |
| `tests/test_error_handling.py`               | REST errors                        |
| `tests/test_client_retry_properties.py`      | REST retry                         |
| `tests/test_client_simulation_errors.py`     | REST simulation errors             |
| `tests/test_client_simulation_start_time.py` | REST simulation start time         |
| `tests/test_helpers.py`                      | REST helpers                       |
| `tests/test_relay_behavior_demo.py`          | REST relay demo                    |
| `tests/test_factories.py`                    | REST factory                       |
| `tests/test_40_tab_unmapped.py`              | Virtual circuits                   |
| `tests/test_32_panel_solar_unmapped.py`      | Virtual circuits                   |
| `tests/test_unmapped_power_verification.py`  | Virtual circuits                   |
| `tests/test_unmapped_tabs_specific.py`       | Virtual circuits                   |
| `tests/test_workshop_unmapped_tabs.py`       | Virtual circuits                   |
| `tests/test_enhanced_battery_behavior.py`    | REST battery                       |
| `tests/test_panel_circuit_alignment.py`      | REST alignment                     |
| `tests/test_coverage_completion.py`          | REST coverage                      |
| `tests/test_phase4_factory_correlation.py`   | Correlation (moves to integration) |

### Test files to MODIFY

| File                                       | Change                                                                                                         |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| `tests/test_protocol_conformance.py`       | Remove `TestRestProtocolConformance`, remove `SpanRestClient` import, remove `CircuitCorrelationProtocol` test |
| `tests/test_phase2_detection_auth.py`      | Update `auth_v2` import paths to `auth`. Remove any REST client references. Keep detection + v2 auth tests.    |
| `tests/test_phase5_simulation_snapshot.py` | Remove `SpanRestClient` references. Keep simulation snapshot tests.                                            |
| `tests/test_ha_compatibility.py`           | Remove `set_async_delay_func` / `_delay_registry` tests. Delete if entirely REST-focused.                      |
| `tests/test_simulation_mode.py`            | Check: if it tests REST simulation path → DELETE. If it tests engine directly → keep.                          |
| `tests/test_example_phase_validation.py`   | Uses `DynamicSimulationEngine` — verify imports, keep.                                                         |

### Example files to DELETE (all 7 + YAML configs)

All files in `examples/` that use `SpanPanelClient`. Keep `examples/README.md` if it exists.

---

## Step 5: Update `pyproject.toml` and version

- Remove `attrs` from dependencies
- Remove `python-dateutil` from dependencies
- Move `paho-mqtt` from optional to **required**
- Remove `[project.optional-dependencies] mqtt = [...]` section
- Remove `openapi-python-client` from dev/generate group
- Version: `2.0.0rc1` → `2.0.0`

---

## Integration Impact (span repo)

The following work is required in the **span** Home Assistant integration to complete the v2 transition:

### Authentication

- Replace v1 bearer-token config flow with passphrase-based `register_v2()` flow
- Store `V2AuthResponse` fields in config entry: access token, MQTT broker credentials, `hop_passphrase`
- Implement re-auth flow using stored `hop_passphrase` (calls `register_v2()` again to get fresh broker password)
- Handle `regenerate_passphrase()` for broker password rotation (broker password != hop passphrase after rotation)

### Circuit UUID Correlation (v1 → v2)

- Entity registry contains v1 dashless UUIDs as `unique_id` — these will not match v2 UUIDs
- On first coordinator refresh after upgrade, build a correlation map: match v1 `unique_id` to v2 circuit by `(name, sorted(tabs))` pairs from the snapshot
- Migrate entity registry entries: update `unique_id` from v1 UUID to v2 UUID
- One-time operation — once migrated, v2 UUIDs are used going forward
- No library involvement needed; the integration has both sides (registry + snapshot)

### Coordinator

- Replace REST polling with `SpanMqttClient.connect()` + `get_snapshot()`
- Optionally use `register_snapshot_callback()` / `start_streaming()` for push-based updates instead of timed polling
- Snapshot model is unchanged — entity code needs no modification beyond UUID migration

### Config Flow

- Detection: `detect_api_version()` to confirm v2 firmware before setup
- Remove v1 token-entry path entirely (users told not to upgrade integration without v2 firmware)
- New fields: passphrase input step, optional MQTT transport selection (tcp vs websockets)

### Dependency

- Update `manifest.json` to require `span-panel-api>=2.0.0`
- Remove any imports of `SpanPanelClient`, `SpanRestClient`, `set_async_delay_func`
- Remove any references to `CircuitCorrelationProtocol`, `CorrelationUnavailableError`

---

## Final Package Structure

```text
src/span_panel_api/
├── __init__.py          # Public API exports
├── auth.py              # v2 HTTP provisioning (register, cert, schema)
├── const.py             # Panel state constants (DSM, relay)
├── detection.py         # API version detection
├── exceptions.py        # Exception hierarchy (simplified)
├── factory.py           # create_span_client() → SpanMqttClient
├── models.py            # Snapshot dataclasses + auth response models
├── phase_validation.py  # Electrical phase utilities
├── protocol.py          # PEP 544 protocols (3 protocols, simplified capability flags)
├── simulation.py        # Simulation engine (produces snapshots)
└── mqtt/
    ├── __init__.py
    ├── client.py        # SpanMqttClient
    ├── connection.py    # AsyncMqttBridge (paho-mqtt wrapper)
    ├── const.py         # MQTT/Homie constants + UUID helpers
    ├── homie.py         # HomieDeviceConsumer (Homie v5 parser)
    └── models.py        # MqttClientConfig, MqttTransport
```

### Approximate lines removed

| Category              | Lines      |
| --------------------- | ---------- |
| `rest/` (all files)   | ~2100      |
| `client.py` (shim)    | ~17        |
| `generated_client/`   | ~2000+     |
| REST test files (~22) | ~3000+     |
| Example scripts (7)   | ~500+      |
| **Total**             | **~7600+** |

### Dependencies after cut

| Package           | Status                       |
| ----------------- | ---------------------------- |
| `httpx`           | Kept (auth.py, detection.py) |
| `paho-mqtt`       | Required (was optional)      |
| `attrs`           | Removed                      |
| `python-dateutil` | Removed                      |

---

## Verification (after each step)

```bash
poetry lock --no-update
poetry install
poetry run pytest tests/ -x -q
poetry run mypy src/span_panel_api/
poetry run ruff check src/span_panel_api/ tests/
poetry run ruff format --check src/span_panel_api/ tests/
```
