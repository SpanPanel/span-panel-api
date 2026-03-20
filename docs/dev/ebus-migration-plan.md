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
- ~~Keep `SpanBranchSnapshot`~~ → **Remove after Step 6** (unmapped tab synthesis replaces branch-based derivation; MQTT has no branch concept)

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

## Step 6: Unmapped Tab Synthesis in MQTT Transport

### Background

In v1, the REST client fetched both `/api/v1/circuits` (commissioned circuits) and `/api/v1/panel` (all 32 branch positions). Branches that had no circuit mapped were synthesized as `unmapped_tab_N` entries in `snapshot.circuits`. The integration consumed
these identically to real circuits — they backed solar inverter sensors and appeared as hidden entities for synthetic calculations.

In v2 MQTT, the Homie broker only publishes circuit nodes for commissioned circuits. There is no branch concept. **Unmapped tabs must be synthesized in the library** to preserve the integration's existing consumption pattern.

### Live Panel Verification (2026-02-25)

Tested against panel `nj-2316-005k6` (firmware `spanos2/r202603/05`):

- 23 circuit nodes published via MQTT, 7 non-circuit nodes (core, lugs, pcs, bess, pv, power-flows)
- Circuit UUIDs are **identical dashless strings** in both v1 REST and v2 MQTT — no correlation needed for current firmware
- Each circuit has `space` (int) and `dipole` (bool) properties
- Dipole formula: `tabs = [space, space + 2]` (same bus bar side, not `space + 1`). Verified against all 6 dipole circuits — matches v1 `tabs` exactly.
- Unmapped tabs derived by subtraction: all 32 spaces minus occupied tabs = unmapped positions

### Implementation in `HomieDeviceConsumer`

When building `SpanPanelSnapshot` from MQTT state:

1. **Determine panel size** — default 32 spaces (standard SPAN panel). May be derivable from `core/breaker-rating` or `$description` metadata in future firmware. For now, hardcode 32 with a constant.

2. **Collect occupied tabs** from all circuit nodes:

   - Single-pole (`dipole` absent or `false`): occupied = `[space]`
   - Dipole (`dipole == true`): occupied = `[space, space + 2]`

3. **Derive unmapped tabs** — `set(range(1, panel_size + 1)) - occupied`

4. **Synthesize `SpanCircuitSnapshot` entries** for each unmapped tab:

   ```python
   SpanCircuitSnapshot(
       circuit_id=f"unmapped_tab_{tab_number}",
       name=f"Unmapped Tab {tab_number}",
       relay_state="CLOSED",
       instant_power_w=0.0,
       produced_energy_wh=0.0,
       consumed_energy_wh=0.0,
       tabs=[tab_number],
       priority="UNKNOWN",
       is_user_controllable=False,
       is_sheddable=False,
       is_never_backup=False,
   )
   ```

5. **Include in `snapshot.circuits`** alongside real circuit entries. The integration filters on `circuit_id.startswith("unmapped_tab_")` to identify these.

### `SpanBranchSnapshot` removal

Once unmapped tab synthesis is in place, `SpanBranchSnapshot` serves no purpose — it was the v1 mechanism for the same data. Remove:

- `SpanBranchSnapshot` dataclass from `models.py`
- `branches` field from `SpanPanelSnapshot`
- Export from `__init__.py` and `__all__`

### Tests

- Test that a snapshot with N circuit nodes and known space/dipole values produces the correct set of `unmapped_tab_` entries
- Test dipole formula: `[space, space + 2]` for dipole circuits
- Test single-pole: `[space]` for non-dipole circuits
- Test that unmapped entries have zero power/energy values
- Test edge case: fully occupied panel (no unmapped tabs)

---

## Integration Architecture: Hybrid Coordinator Model

### Problem: Pure Push vs. Polling

HA supports two entity update models:

1. **Polling:** `DataUpdateCoordinator` calls the library on a timer, gets data, pushes to entities. This is what the v1 REST integration does.
2. **Pure push:** Each entity subscribes to MQTT callbacks, calls `async_write_ha_state()` on every update. This is what dcj's span-hass does.

Pure push has a **CPU cost problem** on SPAN panels. A 32-circuit panel publishes `active-power`, `exported-energy`, `imported-energy`, `relay`, `shed-priority`, and more per circuit — 160+ properties. Power values update continuously. Each
`async_write_ha_state()` call triggers:

- State machine write
- Event bus fire (`state_changed` event)
- All state change listeners notified (automations, templates, logbook)
- Recorder queues a DB write (batched at ~1s, but still per-entity)

On Raspberry Pi hardware common among HA users, hundreds of state writes per second is significant. **HA does not rate-limit or coalesce these.**

### Solution: Hybrid Coordinator

Use `DataUpdateCoordinator` with `update_interval=None` (no polling timer). MQTT pushes trigger snapshot builds on a controlled cadence rather than per-property.

**Data flow:**

```text
MQTT broker
  → paho socket → asyncio add_reader
    → loop_read() → paho internal dispatch (no background thread)
      → HomieDeviceConsumer._handle_property()     ← cheap dict write, no HA
        → property_values[node_id][prop_id] = value

(on controlled interval or debounced after burst)

HomieDeviceConsumer.build_snapshot()               ← builds frozen dataclass
  → coordinator.async_set_updated_data(snapshot)   ← single coordinator push
    → each entity reads its field from snapshot
      → async_write_ha_state() ONLY if value changed
```

**Key properties:**

- MQTT messages accumulate in `HomieDeviceConsumer`'s property dict with zero HA involvement — just Python dict writes
- Snapshot is built only when the coordinator requests it, not on every MQTT message
- `async_set_updated_data()` triggers all entities to re-read, but each entity compares its current value against the snapshot and only writes state if changed
- The snapshot is the **natural coalescing boundary** — many MQTT property updates collapse into one snapshot, one coordinator push, and selective entity state writes

### Why Not Pure Push

dcj's span-hass uses pure push (`should_poll = False`, per-entity MQTT callbacks). This works but:

- Every MQTT property change fires entity state writes through HA's full pipeline — no coalescing
- Each entity parses raw MQTT strings independently — duplicated conversion logic across entity classes
- No single point-of-truth for panel state — harder to implement cross-entity logic (e.g., grid power = sum of circuits)
- The gRPC prototype arrived at the same conclusion: snapshot polling outperformed per-property push on real hardware

### Why Not Pure Polling

The v1 REST integration polls on a timer (e.g., every 30s). This works but:

- MQTT data is already being pushed — polling wastes it
- Timer-based updates add latency (up to one full interval)
- The coordinator's `update_interval=None` + `async_set_updated_data()` gives us push semantics with coordinator lifecycle management

### Coordinator Implementation Notes

The integration's coordinator should:

1. On setup: call `SpanMqttClient.connect()`, then `register_snapshot_callback(on_snapshot)` and `start_streaming()`
2. In `on_snapshot` callback: call `coordinator.async_set_updated_data(snapshot)`
3. The library's `_dispatch_snapshot()` is already debounced by the MQTT message arrival rate — further debouncing in the integration is optional but recommended for burst scenarios (e.g., `asyncio.call_later` with a 0.5s delay, resetting on each new
   snapshot)
4. On teardown: call `stop_streaming()` then `SpanMqttClient.close()`

Entities inherit from `CoordinatorEntity` and read from `self.coordinator.data` (the `SpanPanelSnapshot`). No entity subscribes to MQTT directly.

---

## Integration Impact (span repo)

The following work is required in the **span** Home Assistant integration to complete the v2 transition:

### Authentication

- Replace v1 bearer-token config flow with passphrase-based `register_v2()` flow
- Store `V2AuthResponse` fields in config entry: access token, MQTT broker credentials, `hop_passphrase`
- Implement re-auth flow using stored `hop_passphrase` (calls `register_v2()` again to get fresh broker password)
- Handle `regenerate_passphrase()` for broker password rotation (broker password != hop passphrase after rotation)

### Circuit UUID Correlation (v1 → v2)

- **Live panel verification (2026-02-25):** v2 Homie circuit node IDs are **identical dashless UUIDs** to v1 REST circuit IDs. All 22 circuits matched exactly. No correlation or migration needed for current firmware.
- Defensive fallback should still be implemented for future firmware: try direct match → try dash-stripping → fall back to `(name, tabs)` pairs
- No library involvement needed; the integration has both sides (registry + snapshot)

### Coordinator

- Replace REST polling `DataUpdateCoordinator` with hybrid model (see above)
- `update_interval=None` — no polling timer
- `register_snapshot_callback()` + `start_streaming()` for push-based updates
- `on_snapshot` callback calls `coordinator.async_set_updated_data(snapshot)`
- Snapshot model is unchanged — entity code needs no modification beyond UUID migration
- Entities remain `CoordinatorEntity` subclasses reading from `coordinator.data`

### Config Flow

- Detection: `detect_api_version()` to confirm v2 firmware before setup
- Remove v1 token-entry path entirely (users told not to upgrade integration without v2 firmware)
- New fields: passphrase input step, optional MQTT transport selection (tcp vs websockets)
- Passphrase step must include user guidance: _"Open the SPAN Home app → Settings → All Settings → On-Premise Settings → Passphrase"_

### Dependency

- Update `manifest.json` to require `span-panel-api>=2.0.0`
- Remove any imports of `SpanPanelClient`, `SpanRestClient`, `set_async_delay_func`
- Remove any references to `CircuitCorrelationProtocol`, `CorrelationUnavailableError`

---

## Step 7: Refactor AsyncMqttBridge to HA Core's Async MQTT Pattern

### Problem

The current `AsyncMqttBridge` (`mqtt/connection.py`) uses paho-mqtt's `loop_start()` which spawns a background thread. MQTT callbacks run on that thread and are bridged to the asyncio event loop via `call_soon_threadsafe()`. Shared state is protected by
`threading.Lock`.

This does **not** match HA core's MQTT pattern. HA core's `homeassistant.components.mqtt` uses paho-mqtt with a fundamentally different architecture:

- **No background thread** — paho's socket is registered with the asyncio event loop via `add_reader`/`add_writer`, calling `loop_read()`/`loop_write()`/`loop_misc()` directly from the event loop
- **NullLock** — `AsyncMQTTClient` subclasses paho's `Client` and replaces all 7 internal threading locks with no-ops (everything runs on the single event loop thread)
- **Zero threading** — no `loop_start()`, no `threading.Lock`

span-panel-api must conform to this pattern before the integration can be considered for HA core submission.

### Reference implementation (read-only)

| File                                                                 | Content                                                                           |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `~/projects/HA/core/homeassistant/components/mqtt/async_client.py`   | `NullLock` (lines 21-45), `AsyncMQTTClient` (lines 47-74)                         |
| `~/projects/HA/core/homeassistant/components/mqtt/client.py:312-410` | `MqttClientSetup` — client creation in executor                                   |
| `~/projects/HA/core/homeassistant/components/mqtt/client.py:500-678` | Socket callbacks: `add_reader`/`add_writer`, `loop_read`/`loop_write`/`loop_misc` |
| `~/projects/HA/core/homeassistant/components/mqtt/client.py:705-777` | `async_connect` (executor), `_reconnect_loop` (async)                             |

### Design decision: Replicate pattern, do NOT depend on HA

span-panel-api replicates the async MQTT pattern (~80 lines of stable code) without importing from `homeassistant`. Reasons:

1. **HA core requires independence.** PyPI libraries listed in `manifest.json` requirements must not import from `homeassistant`. The library sits below the HA boundary.
2. **Circular dependency.** `span (integration)` → `span-panel-api` → `homeassistant` would require the full HA environment to install the library.
3. **Simulation engine stays standalone.** `DynamicSimulationEngine` produces snapshots without any HA context — tests run in <1s with zero HA dependency.
4. **Version decoupling.** HA's MQTT internals (lock names, socket callback signatures) are not a public API. Importing them creates fragile coupling.

The overhead is ~80 lines of trivially stable code (paho-mqtt's 7 internal lock names haven't changed across major versions).

### Step 7a: New file `mqtt/async_client.py`

Port of HA core's `async_client.py` (74 lines):

```python
class NullLock:
    """No-op lock for single-threaded event loop execution."""
    # __enter__, __exit__, acquire, release — all no-ops
    # @lru_cache(maxsize=7) on each method

class AsyncMQTTClient(paho.Client):
    """paho Client subclass with NullLock replacing all 7 internal locks."""
    def setup(self) -> None:
        self._in_callback_mutex = NullLock()
        self._callback_mutex = NullLock()
        self._msgtime_mutex = NullLock()
        self._out_message_mutex = NullLock()
        self._in_message_mutex = NullLock()
        self._reconnect_delay_mutex = NullLock()
        self._mid_generate_mutex = NullLock()
```

### Step 7b: Rewrite `mqtt/connection.py`

Replace the thread-based `AsyncMqttBridge` with event-loop-only implementation.

**Remove:**

- `import threading`
- `self._lock = threading.Lock()` and all `with self._lock:` blocks
- `self._client.loop_start()` / `self._client.loop_stop()`
- `call_soon_threadsafe()` in `_on_connect`, `_on_disconnect`, `_on_message`

**Add — Socket callback plumbing:**

| Callback                            | Purpose                                                        | Source pattern       |
| ----------------------------------- | -------------------------------------------------------------- | -------------------- |
| `_async_reader_callback`            | `loop.add_reader` handler → calls `client.loop_read()`         | HA core line 552-556 |
| `_async_writer_callback`            | `loop.add_writer` handler → calls `client.loop_write()`        | HA core line 646-650 |
| `_async_start_misc_periodic`        | Schedules `client.loop_misc()` every 1s via `loop.call_at()`   | HA core line 558-573 |
| `_async_on_socket_open`             | Register reader + start misc timer                             | HA core line 614-628 |
| `_async_on_socket_close`            | Remove reader, cancel misc timer                               | HA core line 630-644 |
| `_async_on_socket_register_write`   | Register writer                                                | HA core line 660-668 |
| `_async_on_socket_unregister_write` | Remove writer                                                  | HA core line 670-678 |
| `_on_socket_open_sync`              | Executor-time bridge → `call_soon_threadsafe` to async version | HA core line 606-612 |
| `_on_socket_register_write_sync`    | Executor-time bridge → `call_soon_threadsafe` to async version | HA core line 652-658 |

**Connect flow — executor pattern:**

```python
async def connect(self) -> None:
    self._loop = asyncio.get_running_loop()
    self._connect_event = asyncio.Event()

    # Build AsyncMQTTClient (replaces paho.Client)
    self._client = AsyncMQTTClient(
        callback_api_version=CallbackAPIVersion.VERSION2,
        transport=self._transport,
        reconnect_on_failure=False,  # We manage reconnection ourselves
    )
    self._client.setup()  # Replace paho locks with NullLock

    # TLS, auth, LWT configuration (unchanged)
    ...

    # Wire callbacks — run directly on event loop (no thread dispatch)
    self._client.on_connect = self._on_connect
    self._client.on_disconnect = self._on_disconnect
    self._client.on_message = self._on_message
    self._client.on_socket_close = self._async_on_socket_close
    self._client.on_socket_unregister_write = self._async_on_socket_unregister_write

    # Connect in executor (blocking DNS + TCP + TLS)
    # During executor connect, socket callbacks need sync→async bridge
    try:
        self._client.on_socket_open = self._on_socket_open_sync
        self._client.on_socket_register_write = self._on_socket_register_write_sync
        await self._loop.run_in_executor(
            None,
            partial(self._client.connect, host=..., port=..., keepalive=...),
        )
    finally:
        # Switch to direct async callbacks after executor returns
        self._client.on_socket_open = self._async_on_socket_open
        self._client.on_socket_register_write = self._async_on_socket_register_write

    # Wait for CONNACK
    await asyncio.wait_for(self._connect_event.wait(), timeout=MQTT_CONNECT_TIMEOUT_S)
```

**Callback simplification — no locks, no thread dispatch:**

```python
# BEFORE (thread-based):
def _on_connect(self, ...):
    with self._lock:
        self._connected = connected
    if self._loop is not None:
        self._loop.call_soon_threadsafe(self._connect_event.set)

# AFTER (event-loop-only):
def _on_connect(self, ...):
    self._connected = connected        # No lock — single-threaded
    if self._connect_event is not None:
        self._connect_event.set()       # No call_soon_threadsafe — already on loop
```

**Reconnection — async loop replaces paho's built-in:**

```python
async def _reconnect_loop(self) -> None:
    delay = MQTT_RECONNECT_MIN_DELAY_S
    while self._should_reconnect:
        if not self._connected:
            try:
                self._client.on_socket_open = self._on_socket_open_sync
                self._client.on_socket_register_write = self._on_socket_register_write_sync
                await self._loop.run_in_executor(None, self._client.reconnect)
            except OSError:
                _LOGGER.debug("Reconnect failed, retrying in %ss", delay)
            finally:
                self._client.on_socket_open = self._async_on_socket_open
                self._client.on_socket_register_write = self._async_on_socket_register_write
        await asyncio.sleep(delay)
        delay = min(delay * MQTT_RECONNECT_BACKOFF_MULTIPLIER, MQTT_RECONNECT_MAX_DELAY_S)
```

### Step 7c: Update tests

Existing tests for `HomieDeviceConsumer` and snapshot building are unaffected (they don't touch the connection layer).

New tests:

| Test file                        | Coverage                                                                      |
| -------------------------------- | ----------------------------------------------------------------------------- |
| `tests/test_async_client.py`     | NullLock no-op behavior, AsyncMQTTClient.setup() replaces 7 locks             |
| `tests/test_connection_async.py` | Executor connect, socket callback registration, misc timer, reconnect backoff |

### Step 7d: Verify no threading remains

```bash
# No threading imports in mqtt/:
grep -r "threading" src/span_panel_api/mqtt/
# Should return nothing

# call_soon_threadsafe only in executor-time sync bridges:
grep "call_soon_threadsafe" src/span_panel_api/mqtt/connection.py
# Should only appear in _on_socket_open_sync and _on_socket_register_write_sync

# No loop_start/loop_stop:
grep "loop_start\|loop_stop" src/span_panel_api/mqtt/
# Should return nothing
```

### Impact on `SpanMqttClient` (`client.py`)

Minimal. The client delegates to `AsyncMqttBridge` and its public API (connect, disconnect, subscribe, publish, callbacks) is unchanged. The `_on_message` and `_on_connection_change` callbacks already assume they run on the event loop — no code changes
needed.

### Updated data flow (after refactor)

```text
MQTT broker
  → paho socket → asyncio add_reader
    → loop_read() → paho internal dispatch
      → _on_message()                              ← runs on event loop, no thread
        → HomieDeviceConsumer._handle_property()
          → property_values[node_id][prop_id] = value   ← cheap dict write

(on controlled interval)
HomieDeviceConsumer.build_snapshot()               ← builds frozen dataclass
  → coordinator.async_set_updated_data(snapshot)   ← single coordinator push
```

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
    ├── async_client.py  # NullLock + AsyncMQTTClient (HA core pattern)
    ├── client.py        # SpanMqttClient
    ├── connection.py    # AsyncMqttBridge (event-loop-driven, no threads)
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

## Phase 7: MQTT Snapshot Debounce

**Goal:** Reduce CPU load from high-frequency MQTT messages by debouncing snapshot rebuilds.

**Problem:** The SPAN panel publishes ~100 MQTT messages/second. Each message triggers a full `build_snapshot()` — iterating all nodes, circuits, and properties — plus coordinator dispatch and entity updates. On low-end hardware (Raspberry Pi) this is
untenable, mirroring the gRPC streaming CPU problem.

**Solution:** Rate-limit `build_snapshot()` + callback dispatch in `SpanMqttClient`. MQTT messages continue to update the Homie property store immediately (cheap dict writes), but the expensive snapshot rebuild is gated by a configurable timer.

| Component                       | Change                                                  |
| ------------------------------- | ------------------------------------------------------- |
| `SpanMqttClient.__init__`       | `snapshot_interval` param (default 1.0s)                |
| `SpanMqttClient._on_message`    | Schedule debounce timer instead of per-message dispatch |
| `SpanMqttClient._fire_snapshot` | Timer callback — build + dispatch one snapshot          |
| Integration options flow        | `snapshot_update_interval` option (0–15s, default 1s)   |
| Integration `__init__.py`       | Pass interval from config to client constructor         |

Setting interval to 0 preserves current no-debounce behavior.

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
