# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-releases are not listed separately. A beta is a step towards the next public version, so its changes are folded into that version's entry as they land and are described against the **last public release**, never against the beta before it. What one
beta corrected in an earlier beta does not appear at all: from the point of view of somebody upgrading between released versions, it never happened.

## [3.0.0]

`span-panel-api` becomes a transport and a dispatcher that contains **no parser**. Wire formats ship as separate distributions and register themselves through the `span_panel_api.schema_adapters` entry-point group, so support for a new panel schema arrives
by installing a package rather than by upgrading the transport.

### Removed

- **BREAKING: `span-panel-api` no longer contains a parser.** Installing it alone gives a client that connects and then raises `SpanPanelAdapterMissingError`. A parser is an install:

  ```console
  # flat-schema panels, firmware r202603-r202627
  pip install "span-panel-api[schema-0]"

  # parent/child panels, firmware r202633+
  pip install "span-panel-api[schema-1]"
  ```

  The adapter distributions can equally be named directly; the extras exist because the dependency arrow runs the other way — an adapter declares a floor on the bootstrap, the bootstrap requires no adapter — so upgrading the bootstrap alone would otherwise
  leave a stale adapter wheel that discovery then rejects, with pip reporting success. The bootstrap never imports an adapter, and supporting a new panel schema on an existing install is an install rather than an upgrade.

- **BREAKING: `HomieLifecycle`, `HomiePropertyAccumulator` and `HomieDeviceConsumer` are no longer exported** from `span_panel_api` or `span_panel_api.mqtt`. All three are flat-schema-specific rather than Homie-convention-level: the accumulator filters
  every topic against a single device's prefix and stores `node → prop`, which drops nearly every message under the parent/child model; `HomieLifecycle`'s members are not Homie 5 `$state` values but a consumer-side progression encoding "one description
  received ⇒ ready", which is the flat readiness model. They now live in `span_panel_api_schema_0`.
- **Removed dead constants** `DEVICE_TOPIC_FMT`, `STATE_TOPIC_FMT`, `DESCRIPTION_TOPIC_FMT`, `PROPERTY_TOPIC_FMT` (unreferenced) and `TYPE_PCS` (a real schema type this library does not consume).

### Changed

- **BREAKING — DER identity speaks the parent/child vocabulary on every device class.** `model` is the human designation and `part_number` the SKU, on `battery`, `evse` and `pv` alike. `product_name` is retired on all three. Flat is the inconsistent side:
  it puts the SKU in `bess/model` and in `evse/part-number`, the same concept under two names, and gives PV neither. Mirroring that would have permanently encoded flat's irregularity in the snapshot, so `schema_0` translates flat into the normalised shape
  instead. Measured: every EVSE identity field reads identically on both adapters, so for that device class identity stops being a migration delta at all. **`battery.model` changes value for existing flat users at this upgrade** — it gains the designation
  where it carried the SKU. That is the deliberate trade: a change scheduled in a library release beats the same change arriving unplanned during a firmware upgrade a user did not choose the timing of.
- **Consumers reading `product_name` must move to `model` in the same release.** The Home Assistant integration builds its device-registry model from it; left unchanged, device cards go blank.
- **Dispatch refuses an unreadable `data-model-version` instead of assuming flat.** Absence still means the flat schema — that is a real signal, since the property was introduced by the firmware that introduced parent/child. A value whose major _can_ be
  read but whose form is non-canonical (`1`, `1.0-beta`) dispatches on that major and logs the deviation. A value with no extractable major raises `SpanPanelSchemaVersionError`. Previously all three fell through to the flat parser, which does not fail — it
  produces plausible but wrong power and energy figures.
- **`get_homie_schema()` tells "not ready yet" apart from "will not fix itself".** Any 5xx raises `SpanPanelServerError`, a transport failure raises `SpanPanelConnectionError`, and a `200` carrying a truncated or empty body raises `SpanPanelServerError`
  rather than surfacing as a parse error. A booting panel brings its network stack and reverse proxy up before the application behind them, so it answers rather than refuses; the distinction is what lets a caller retry that and not retry a 4xx.

### Added

#### Adapter architecture

- **The `SchemaAdapter` protocol, and `ADAPTER_CONTRACT` alongside it.** Member presence is not the whole contract — a Protocol cannot express signatures at runtime, so an adapter carrying every required name and the wrong `__init__` arity would pass
  discovery and fail much later inside the transport, as a bare `TypeError` about an argument count. Every adapter declares `ADAPTER_CONTRACT` as a **literal** and discovery rejects anything that does not match this package's `ADAPTER_CONTRACT_VERSION`; a
  value read from the installed bootstrap would agree with every bootstrap, which is the disagreement being looked for. The required-member set is derived from every public member the protocol declares, not only the callable ones.
- **`installed_adapter_keys()` and `SpanMqttClient.installed_adapters`.** Enumeration reads distribution metadata only; an adapter is imported the first time a panel asks for that key. A flat panel therefore never imports `schema_1`, and with it never
  imports the eBus SDK or jsonschema, for a parser it would not call. The async paths run both in a thread, and resolution stays cached per key, which is what keeps the synchronous pre-rebuild callback free of I/O.
- **`resolve_adapter(key, reason)`** — the single place a missing adapter becomes a named error, used by both dispatch and the transport's default path.
- **`span_panel_api.dispatch.select_adapter_key`**, so the transport can dispatch without importing the factory. `adapters.py` answers "what is installed"; `dispatch.py` answers "what does this panel need".
- **`SpanPanelAdapterMissingError`, `SpanPanelSchemaVersionError` and `SpanPanelAdapterIncompatibleError`**, all exported from the top-level package. The three are separate because the remedy differs: missing means install something, a schema version no
  adapter can even be named for means there is nothing to install yet, and incompatible means installing more cannot help. Reporting the third as the first sends someone to install a package they already have. Discovery only _logs_ a rejection, so one
  unusable third-party adapter cannot take down a panel whose own adapter is fine; the error surfaces only when the rejected adapter turns out to be the one required.
- **`SpanMqttClient(adapter_factory=...)` is optional.** When omitted the parser is resolved through entry-point discovery at `_build_adapter()`. Resolution is lazy by design: constructing a client must not require an adapter to be installed, only building
  a parser must. Dispatch happens wherever a parser is built, so a directly constructed client dispatches exactly as the factory path does.
- **`V2HomieSchema.data_model_version`**, carrying the `dataModelVersion` field and `None` when the panel omits it. Absence is the flat signal and stays distinct from an empty string.

#### Surviving a firmware upgrade

- **A panel that changes schema generation mid-life is redispatched rather than reloaded.** The schema is refetched over REST and the parser swapped in place, so an install that upgrades from flat to parent/child keeps running. The new adapter is resolved
  **before** any state is touched, so a flat-only install that meets a parent/child panel logs which package is missing and keeps the parser it has instead of raising into a background task.
- **The wait for a panel to finish rebooting does not give up.** Any bound here is sized against a reboot somebody measured, and the next reboot is not that reboot — a live firmware upgrade has been observed taking four minutes from MQTT dropping to the
  broker returning, still answering `502` at that point. Giving up has nothing to recommend it: the only things that start another attempt are the reconnect edge and the panel republishing its data-model version, and a panel that finishes booting after the
  wait expired produces neither, so running out of attempts means stranded until somebody reloads by hand.
- **The retry interval settles at thirty seconds rather than growing.** Backing off without a ceiling would mean a panel that took a while to return was then ignored for longer than it took. The gap goes 1, 2, 4, 8, 16, 30 and stays there, so once your
  panel is answering it is noticed within half a minute however long the wait has already run. Waiting costs nothing you were relying on — energy sensors hold their last reading through an outage on their own grace period, which is untouched by this — and
  what is left is one request every thirty seconds to a device on your own network.
- **Nothing escapes the redispatch task.** An unexpected failure there used to surface as a bare `Task exception was never retrieved` while the parser silently stayed on the old generation. It is logged at ERROR naming the consequence and the remedy,
  because a reload is the user's only move and nothing else was going to tell them.

#### Injected HTTP client on the runtime path

- **`SpanMqttClient` accepts an `httpx_client`, and so does `create_span_client`.** Four config-flow-facing entry points already took an injected client; the runtime path was the one that did not, so every schema read built a throwaway — including the
  retry loop that runs during a firmware upgrade, which built one per attempt at exactly the moment the panel was mid-reboot. Optional and defaulted, so nothing outside Home Assistant changes. The ownership rule is the one the existing entry points already
  state: a client handed in is never closed here, and its timeouts, limits and headers are the caller's, which is why the per-call `timeout` defaults are ignored when one is given.

#### Reference payloads shipped in the wheel

- **`span_panel_api.reference_payloads`, shipping `homie_schema.json` as package data.** The captured `GET /api/v2/homie/schema` response is reached by `homie_schema()` and `homie_schema_types()` rather than by path. It was already being consumed outside
  this repository — the Home Assistant integration checks the field paths it declares against what an adapter can actually produce — by vendoring a byte copy with a README explaining where the copy came from. A copy has no version: it goes stale in
  silence, and a stale one turns the integration's conformance gate into a check against a schema no panel runs. Shipped, the payload carries the version of the release it came with. `homie_schema_types()` returns `HomieSchemaTypes`, precisely what
  `span_panel_api_schema_0.field_metadata.build_field_metadata` accepts, so a caller building metadata never reaches into an untyped document to get it. The parent/child device tree is the other half and ships from `span-panel-api-schema-1`, with the
  parser that can interpret it.

#### New snapshot surface

Everything below is additive. Each field is `None` or empty on a panel that publishes no such thing, and no flat panel publishes any of it unless stated.

- **`SpanMidSnapshot` and `SpanPanelSnapshot.mid`.** The parent/child model puts the `grid` capability on a Microgrid Interconnect Device rather than on the enclosure, so islanding state, grid state and the grid-forming entity live there. Presence is
  `snapshot.mid is not None` rather than a sentinel field, and identity is `info/serial-number` rather than the Homie device id, which the proxy model warns is not stable across a proxy-to-native transition.
- **`dsm_state` and `current_run_config` are read from the MID.** Both are existing entities that would otherwise degrade to `UNKNOWN` on a parent/child panel: `schema_0` _derives_ them from a multi-signal heuristic, and the parent/child model states the
  answer outright. Sensed from a ready MID, falling back to the user's `shed/asserted-islanding-state` when it is not ready, then to a `power-flows/grid` heuristic when there is no MID at all, and unknown otherwise. A missing MID never reports on-grid — it
  means SPAN is not the islanding authority, not that the site is on grid, and a generator-fed island is the counterexample. `PANEL_BACKUP` versus `PANEL_OFF_GRID` becomes authoritative rather than guessed.
- **`grid_islandable` is mapped to `grid-forming/capable`** over the BESS's inverter children, as the disjunction — a panel does not island, its DER does, and flat expressed a property of the DER as a property of the enclosure. It returns `None` rather
  than `False` when nothing publishes it, so absence stays a gap instead of becoming a claim. No producer publishes it today, which is recorded rather than worked around.
- **`SpanPanelSnapshot.lugs_at_service_entrance`, saying whether this enclosure's upstream lugs are the utility connection point.** `instant_grid_power_w` is those lugs' `meter/active-power`, and the name holds only at the service entrance: a BESS wired
  ahead of the main lugs, or an enclosure fed by another enclosure, leaves the lugs metering panel-side flow while the utility side differs by whatever that device contributes or absorbs. `power_flow_grid` stays site-level and correct in both, so the two
  legitimately disagree — and before this a consumer seeing them disagree could not tell a topology from a fault. Sourced from the lugs' `connection/fed-by-device-id`, which `power-flows` 0.3 names as the detection mechanism when it qualifies its own
  negation table. Defaults `True`, because flat firmware predates chaining and a flat panel's lugs really are its service entrance.
- **`SpanBatterySnapshot.power_w` and `SpanBatterySnapshot.communication_state`.** The battery device has always published `meter/active-power` and `status/communication-state` and neither reached a field, so a consumer could show the enclosure's
  arbitrated `power_flow_battery` and nothing the BESS itself reports. `power_w` is **discharge-positive**: the enclosure meters the BESS the way it meters a circuit it feeds, so positive means power flowing _out of_ the battery, matching the eBus rule for
  a device's own meter. The asymmetry with `panel.power_flow_battery` is deliberate — the enclosure's arbitrated figure is passed through untouched by both adapters and is charge-positive, so it reads negative for the same discharging battery that makes
  `power_w` positive. The two describe the same physical power in opposite frames, and a consumer rendering both negates one of them. `communication_state` stays the published enum string (`OK`/`DEGRADED`/`LOST`/`UNKNOWN`) rather than collapsing to a bool,
  because `DEGRADED` is neither `OK` nor `LOST`; it is deliberately not merged into `battery.connected`, which is the _enclosure's_ view of the same link.
- **`SpanEvseSnapshot.connected` and `SpanPVSnapshot.connected`.** `battery.connected` has carried the enclosure's view of the link to the BESS from the upstream lugs' `connection/fed-by-device-status`; the other half of the same capability — a circuit's
  `connection/feeds-device-status` — reached nothing, so only one of a panel's three DER classes had a link-health field. `None` is the specification's "unknown" and is load-bearing: the enum is `OK,LOST,DEGRADED` with no `UNKNOWN` member, and a mixed-load
  or unsurveyed circuit publishes no connection record at all, which is the normal state for most of a panel's circuits. So absence is never a fault. `DEGRADED` collapses to `False`, because the question this field answers is whether the enclosure can talk
  to the device. The charger's link is not the charger's session: `evse.status` is the OCPP-style state the charger reports about the cable in front of it, and a charger mid-session over a lost link publishes `CHARGING` and `connected=False` at once.
- **Five `shed-forecast` fields**: `shed_time_to_priority_shed_min`, `shed_total_time_remaining_min`, `shed_full_charge_time_to_priority_shed_min`, `shed_full_charge_total_time_remaining_min` and `shed_forecast_confidence`. The backup-planning numbers —
  how long before my battery starts shedding circuits, how long before it is exhausted — were on the wire and stopped at the transport. All four times are `integer` minutes as the capability declares, parsed so that a publisher serialising a whole number
  with a decimal point still resolves; `confidence` stays the raw `LOW`/`MEDIUM`/`HIGH` string, because it qualifies the four times rather than standing alone. `None` is load-bearing here too: zero minutes is a legitimate reading — shedding starts now — so
  a defaulted zero would be indistinguishable from the worst forecast the capability can report.
- **`SpanPanelSnapshot.adopted_devices`, reporting a device type this library models nothing for rather than dropping it.** The schema is explicitly vendor-extensible, so an unmodelled device is an expected arrival rather than a hypothetical one; before
  this it produced no field, no metadata row and no sign it was there. `AdoptedDevice` carries the device's identity and its readings. **The unit is a device, never a property**: a new property on a device already modelled is a curation task with a short
  turnaround, and surfacing it automatically would spend a consumer's entity identity permanently on a shape a human would likely have chosen differently. An unmodelled _type_ is the opposite case — no curation is coming, so silence is the only
  alternative. Extra instances of a modelled type are deliberately not adopted either: a second BESS is a multiplicity limit, not an unmodelled device.
- **`AdoptedDevice.parent` and `AdoptedDevice.proxied`**, carrying the proxy link a device declares. Carried rather than acted on — an adopted device is still registered under the enclosure — because a _proxied_ unmodelled device is a real shape that would
  otherwise be flattened away unrecorded. The nesting is deliberately not built: proxied ids differ by design and consumers correlate by `info/serial-number` rather than by device id, and the tree model is being reshaped upstream, so the fields capture the
  evidence and the topology waits.
- **`AdoptedProperty.set_topic`, `SpanMqttClient.set_adopted_property` and `AdoptedControlProtocol`**, so a settable property on an adopted device can be written and the write cannot reach anything else. The topic is populated only for a settable property
  on a device `is_modelled` rejects, so it is the scoping that authorises the write rather than a check a caller has to remember: the transport resolves the property against the current snapshot's `adopted_devices` and publishes to the topic that property
  carries, no topic is accepted from the caller, and a device this library models produces no `AdoptedDevice` to find. There is deliberately no translation and no bounds check on an adopted write — both exist on curated controls because this library knows
  what those properties mean, and inventing a bound for somebody else's hardware would be inventing a fact. `AdoptedControlProtocol` lets a consumer ask `isinstance` before offering the control, exactly as it does for circuit, panel and EVSE control.
- **`SpanPanelSnapshot.extension_properties`, `ExtensionProperty` and `ExtensionSubject`**, so a vendor property on a device this library _already_ models reaches a consumer instead of stopping at diagnostics. Adoption covers the unmodelled-device half;
  this covers the other one, where a new property on the BESS, a charger, a circuit or the panel would otherwise be a declaration with no value, visible only to a maintainer reading a diagnostics attachment. The subject names which modelled snapshot
  subject a property hangs off — `battery`, `mid`, `pv`, `panel`, `lugs` with `upstream`/`downstream`, and `evse`/`circuit` with the instance key the snapshot's own maps use — so a consumer resolves the device with a lookup it already performs. What is
  _not_ exposed is the field-level mapping: the subject is one value per device and cannot drift, while the wire-property-to-snapshot-field map is the adapter's internal business and exporting it would freeze it as API.
- **An extension property's value never reaches diagnostics, structurally.** `ExtensionProperty` is deliberately not a `FieldMetadata`, so it cannot enter the map `partition()` walks and has no path into a payload that leaves the machine. The discovery
  rows keep flowing unchanged: the same property appears in both surfaces on purpose, joined by its `{node}/{property}` path — a declaration for the maintainer, a reading for the user. It is read-only by construction: it carries `settable` for curation
  triage and no set topic, and there is no member a write path could be built from.

### Fixed

- **A single HTTP 429 from the panel no longer aborts setup.** The panel rate-limits `GET /api/v2/certificate/ca` at roughly seven requests a second, and `download_ca_cert()` raised on any non-200 — so a reconnect storm, or simply a second client polling
  the same panel, turned a transient condition into a hard failure that needed a manual reload. It now retries a 429 with exponential backoff, honouring `Retry-After` when the panel sends it and falling back to the backoff curve when the header is absent
  or malformed. Non-429 responses still fail fast. `max_attempts` and `backoff_s` are parameters, so a caller can tune the behaviour or switch it off. Reported and fixed by [@brunocramos](https://github.com/brunocramos) in
  [#148](https://github.com/SpanPanel/span-panel-api/pull/148).

## [2.6.4] - 05/2026

### Fixed

- **MQTT reconnect now self-heals after persistent failure** — `AsyncMqttBridge._reconnect_loop` rebuilds the paho client from scratch (re-fetching the panel CA, constructing a fresh client, resetting the Homie accumulator) after
  `MQTT_FULL_REBUILD_AFTER_FAILURES` (3) consecutive failures, or immediately on any `ssl.SSLError`. The previous behavior pinned the panel's CA certificate into the paho client once at `connect()` time and re-used it across all reconnect attempts; if the
  panel rotated its private CA — most plausibly during a firmware upgrade — every subsequent reconnect raised `ssl.SSLCertVerificationError` (caught by the broad `OSError` clause and silently retried) and the bridge could not recover without a config-entry
  reload. The rebuild mirrors what a manual reload does without going through HA's `config_entry` teardown, so entities stay registered and the integration's grace-period logic continues to apply unchanged. The threshold-cadence design (counter reset on
  every rebuild attempt, success or fail) keeps the recovery path active throughout extended outages — multi-day disconnections recover whenever the panel becomes usable again, including if the CA rotates a second time mid-outage. See
  `SpanPanel_Docs/span-panel-api/2026-05-17-mqtt-ca-refresh-on-reconnect-design.md` for the full design.

### Added

- **`AsyncMqttBridge._rebuild_client()`** — internal recovery method invoked by the reconnect loop on persistent failure. Re-fetches the panel CA via `download_ca_cert()`, builds a fresh paho client via the new `_make_paho_client()` factory, fires the
  optional pre-rebuild callback so consumers can reset their own state, tears down the old client, and submits the initial connect via the executor. Restores the previous client on any failure.
- **`AsyncMqttBridge.set_pre_rebuild_callback()`** — internal API for `SpanMqttClient` to register a hook that fires before each rebuild. Used to reset the Homie accumulator so retained messages on the new subscription start from a clean slate.
- **`MQTT_FULL_REBUILD_AFTER_FAILURES`** constant in `mqtt/const.py`.

### Changed

- **`SpanPanelAPIError` now in the bridge's CA-fetch exception list** — a `download_ca_cert()` failure during rebuild (e.g. panel returns HTTP 502 mid-outage) is caught, logged at WARNING, and the loop continues retrying with the previous client instead of
  letting the reconnect task die.

## [2.6.2] - 04/2026

### Changed

- **Reconnect loop log noise reduced** — `SpanMqttClient._reconnect_loop` now splits the catch-all exception handler in two: expected transient failures (`OSError` family — refused connection, DNS miss, socket timeout, `ssl.SSLError`) log a one-line
  WARNING with the exception repr, while unexpected exceptions retain the full traceback via `exc_info=True`. The common "panel offline" case no longer buries logs in paho/stdlib stack frames that add no diagnostic signal; genuinely unknown failures still
  surface full tracebacks for support-ticket triage.

## [2.6.1] - 04/2026

### Changed

- **`get_fqdn()` returns `str | None`** — `None` now distinguishes "no FQDN configured" (HTTP 404 or missing field) from an explicit empty string. Callers that treated `""` as "not registered" must update to check for `None`.
- **Connection callback errors logged at WARNING** — `SpanMqttClient._on_connection_change` now logs callback exceptions via `_LOGGER.warning(..., exc_info=True)` instead of `_LOGGER.exception(...)`, consistent with `_dispatch_snapshot`.
- **Reconnect loop catches all exceptions** — `AsyncMqttBridge._reconnect_loop` no longer silently drops on non-`OSError` failures (e.g. `WebsocketConnectionError`, `ssl.SSLError`). All exceptions are logged at WARNING and the loop keeps backing off.
- **Abnormal MQTT disconnects logged at WARNING** — disconnects where `reason_code.is_failure` is true now log at WARNING; clean disconnects continue to log at DEBUG.

### Fixed

- **CA certificate no longer written to disk** — `AsyncMqttBridge.connect()` builds the `ssl.SSLContext` from the fetched PEM via `cadata`, eliminating the temp-file lifecycle (and the small leak window on unexpected process exit) that the prior
  `tls_set(ca_certs=path)` path required.
- **Deprecated `asyncio.get_event_loop()` removed** — `_wait_for_circuit_names` now uses `time.monotonic()`. The previous code emitted a `DeprecationWarning` on Python 3.12+.
- **Negative-zero on circuit `instant_power_w`** — explicit guard replaces a cryptic `-raw or 0.0` idiom in `HomieDeviceConsumer._build_circuit`.
- **DSM grid-exchanging heuristic uses epsilon** — replaces `!= 0.0` float comparison with `abs(x) > 1.0 W`, so the `DSM_OFF_GRID` branch is actually reachable when no BESS is commissioned and lugs readings hover near zero.
- **`SpanPanelAPIError.__str__` override removed** — the override silently hid exception args beyond the first; default `Exception.__str__` is now used.
- **Paho lock-layout check at import** — `span_panel_api.mqtt.async_client` verifies on import that the `_PAHO_LOCK_ATTRS` list exactly matches paho's `*_mutex` attributes. Raises `RuntimeError` (not `assert`, so `python -O` does not bypass it) on drift.

### Documentation

- **`register_v2()`** — docstring now warns that each call creates a new client entry on the panel; callers should persist and reuse the returned `V2AuthResponse` rather than re-registering on every restart.
- **Stale simulation transport references removed** from `protocol.py` and `models.py` module docstrings.

## [2.6.0] - 04/2026

### Added

- **`SpanMqttClient.register_connection_callback(cb)`** — subscribe to broker connection state transitions. Callback fires with `False` on broker disconnect and `True` on reconnect; returns an idempotent unregister function. Added to
  `SpanPanelClientProtocol` so any transport that claims the protocol must implement it.
- **`SpanPanelStaleDataError`** exception — raised by `get_snapshot()` when the client is not fully live. Derives from `SpanPanelError` (not from `SpanPanelConnectionError`), because "never connected" and "running but data not currently live" are
  semantically distinct states.

### Changed

- **`get_snapshot()` contract** — now raises `SpanPanelStaleDataError` when the bridge is not connected or the Homie device has not reached ready state. Previously, the method silently returned a snapshot built from whatever the in-memory accumulator
  happened to hold, which made offline panels indistinguishable from online ones. This is the primary reason the span integration could not detect panel-offline transitions.

### Fixed

- **Stale snapshot dispatch after bridge disconnect** — a pending snapshot-debounce timer scheduled just before a bridge disconnect could fire afterwards, delivering a snapshot built from the still-`ready()` accumulator to subscribers.
  `_on_connection_change(False)` now cancels the pending timer, and `_dispatch_snapshot` is now guarded by the same liveness predicate as `get_snapshot()`, so push consumers never receive a post-disconnect stale snapshot.

### Breaking

- Consumers of `get_snapshot()` must now handle `SpanPanelStaleDataError`. Any consumer with a broad `except Exception` (or `except SpanPanelError`) branch already handles this correctly.

## [2.5.4] - 04/2026

### Reverted

- **Revert accumulator to 2.5.1 behavior** — the 2.5.2 lifecycle changes (property clearing, unconditional lifecycle transition on `$state=init`, generation counter) caused false energy dip spikes on panel reboots and network interruptions. The 2.5.3
  partial fix (removing the clearing) was insufficient — the unconditional lifecycle disruption on transient `$state=init` events still triggered snapshot pipeline resets that produced 0.0 energy readings. Reverted `accumulator.py` and `homie.py` to their
  stable 2.5.1 state. The existing dirty-node tracking handles reboot transitions correctly without special-case lifecycle management.

## [2.5.3] - 04/2026 (retired)

> **Retired:** Partial fix for 2.5.2 — removed property clearing but kept the lifecycle disruption that still caused false dips. Superseded by 2.5.4.

### Fixed

- **Preserve property values on lifecycle reset** — removed the property/timestamp/target clearing from `_handle_description()`.

## [2.5.2] - 04/2026 (retired)

> **Retired:** Lifecycle changes caused false energy dip spikes. Superseded by 2.5.4.

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

| Version    | Date    | Transport  | Summary                                                                            |
| ---------- | ------- | ---------- | ---------------------------------------------------------------------------------- |
| **2.5.4**  | 04/2026 | MQTT/Homie | Revert accumulator to stable 2.5.1 behavior; fixes false energy dip spikes         |
| **2.5.3**  | 04/2026 | MQTT/Homie | _(retired)_ Partial fix — still caused false dips from lifecycle disruption        |
| **2.5.2**  | 04/2026 | MQTT/Homie | _(retired)_ Lifecycle changes caused false energy dip spikes                       |
| **2.5.1**  | 04/2026 | MQTT/Homie | Replace assert with RuntimeError; fix bandit pre-commit hook                       |
| **2.5.0**  | 03/2026 | MQTT/Homie | Homie accumulator layer, $target support, dirty-node snapshot caching              |
| **2.4.2**  | 03/2026 | MQTT/Homie | SSL context creation moved to executor                                             |
| **2.4.1**  | 03/2026 | MQTT/Homie | License metadata, loosened httpx constraint                                        |
| **2.4.0**  | 03/2026 | MQTT/Homie | proximityProven, injected HTTP client, executor file I/O, type alias, test cleanup |
| **2.3.2**  | 03/2026 | MQTT/Homie | FQDN management endpoints                                                          |
| **2.3.1**  | 03/2026 | MQTT/Homie | MQTT connection errors wrapped as SpanPanelConnectionError                         |
| **2.3.0**  | 03/2026 | MQTT/Homie | Simulation engine removed                                                          |
| **2.2.4**  | 03/2026 | MQTT/Homie | Negative zero fix on idle circuits                                                 |
| **2.2.3**  | 03/2026 | MQTT/Homie | Panel size from Homie schema; `panel_size` always populated on snapshot            |
| **2.0.2**  | 03/2026 | MQTT/Homie | EVSE (EV charger) snapshot model, Homie parsing, simulation support                |
| **2.0.1**  | 03/2026 | MQTT/Homie | Full BESS metadata parsing, README documentation                                   |
| **2.0.0**  | 02/2026 | MQTT/Homie | Ground-up rewrite: MQTT-only, protocol-based API, real-time push, PV/BESS metadata |
| **1.1.14** | 12/2025 | REST       | Keep-Alive and RemoteProtocolError handling                                        |
| **1.1.9**  | 9/2025  | REST       | Simulation sign corrections                                                        |
| **1.1.8**  | 2024    | REST       | Simulation power sign fix                                                          |
| **1.1.6**  | 2024    | REST       | YAML simulation API, battery simulation                                            |
| **1.1.5**  | 2024    | REST       | Simulation edge cases                                                              |
| **1.1.4**  | 2024    | REST       | Formatting and linting                                                             |
| **1.1.3**  | 2024    | REST       | Test and lint fixes                                                                |
| **1.1.2**  | 2024    | REST       | Simulation mode added                                                              |
| **1.1.1**  | 2024    | REST       | Dependency updates                                                                 |
| **1.1.0**  | 2024    | REST       | Initial release                                                                    |
