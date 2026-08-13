# Changelog

All notable changes to `span-panel-api-schema-1` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Note that this package versions on the **library-API axis**, not the wire-format axis. The wire format it parses is the parent/child device tree SPAN firmware `r202633+` publishes, identified by `SUPPORTS_DATA_MODEL_VERSIONS` rather than by this version
number. A release here means this parser changed, never that the panel did.

## [0.1.0b3] - 08/2026

Pre-release. **Requires `span-panel-api` 3.0.0b3 or newer** — see Fixed.

### Added

- **Spec conformance checking.** `spec_lock.json` ships with the package and records what this parser targets: the firmware range, the eBus specification commit its vocabulary was read from, and the version of every capability, device and registry it
  implements. It is the consumer counterpart to the simulator's publisher lockfile, and both are pinned to the same specification commit — though the anchor shared between them is the **firmware range**, not that commit, because the specification says what
  a device class _may_ publish while a panel publishes one specific tree.
- **The 13 capability catalogs this adapter addresses**, byte-copied under `spec/` along with the device-types registry. Vendored rather than depended on because the specification is a git repository of versioned documents, not a package. They exist to be
  checked against, never parsed in production: units and datatypes still come from each device's `$description`, since the catalog is the superset across all hardware rather than a statement about the panel in front of us. Formatting hooks are excluded
  from `spec/`, because a lint fix there would quietly invalidate the byte comparison that makes the copies worth having.
- **`tests/test_schema_one_conformance.py`**, which asks the consumer's question rather than the publisher's. A publisher asks whether everything it emits is legal, and for it an omission is unremarkable. This asks whether every name the adapter _reads_ is
  one the specification defines — because a consumer addressing a name that no longer exists does not fail, it goes quiet: the property never arrives, metadata lookup returns `None`, and an entity disappears. `ebus-sdk` 0.18.0 removing the `battery`
  capability key in favour of `soc`, with no alias, is exactly that shape.
- **An explicit SPAN extension allowlist.** Fourteen of the forty-two properties this adapter reads are absent from every catalog — per-phase meter readings, panel link states, circuit `spaces`, the EVSE surface. All are legal, since the specification
  permits properties it has never heard of. They are enumerated with reasons so that a name missing from the catalog must be a deliberate claim about SPAN's vocabulary rather than an unnoticed typo; at runtime the two are indistinguishable. Tests also fail
  when an extension is later adopted upstream, or when one is declared for a property nothing reads.
- **A peer record and simulator coverage check.** `spec_lock.json` now records the producer this parser is developed against — the SPAN simulator, `role: publisher` — with the specification commit and firmware range it pins, and a captured copy of the tree
  it publishes is vendored alongside the catalogs. Two sides reading different vocabularies is now a test failure rather than something noticed later, and the anchor asserted between them is the **firmware range**, since the specification says what a
  device class may publish while a panel publishes one specific tree.
- **An explicit record of what the producer does not exercise.** Of the 42 `(capability, property)` pairs this adapter reads, the simulator's captured tree declares 41. The exception is `grid/islanding-state`: the simulator models a MID but its tracked
  config publishes none, so `grid_state` — corrected in `0.1.0b2` to read `islanding-state` rather than `grid-state` — is the single mapping the producer gives no evidence for. Recorded rather than left implicit, because a passing suite otherwise reads as
  coverage it does not have. The entry is rejected once the simulator starts publishing it.
- **The parser is now driven end to end from what the producer actually publishes.** Every other test in this package runs on a fixture captured off the upstream _generic_ eBus panel simulator, which by construction never carries SPAN's own vocabulary.
  `spec/fixtures/simulator_wire.json` is a capture from SPAN's publisher instead — descriptions, `$state` and all 494 property values across 37 devices — fed in sorted topic order, the way a retained store replays it rather than the way a tree is walked.
  The parser reaches ready on it, sizes the panel from `MAIN_40`, and parses all 30 circuits. Values are deliberately not asserted: the producer's config carries `noise_factor` and its clock advances, so pinning a wattage would fail on every recapture for
  a reason nobody could act on.
- **Two producer-side gaps are pinned rather than left to be noticed.** `grid_state` stays `None` because nothing instantiates a MID, and every DER — BESS, PV and both EVSEs — declares `info/model` in its `$description` and never publishes a value (PV
  declares five `info` properties and publishes one). The second breaks the single standing obligation eBus places on a publisher, to declare accurately what it publishes, and is invisible to a conformance checker: comparing declarations against catalogs
  cannot see a declaration nothing fulfils. Only a capture carrying values can, which is the argument for this fixture existing. Both are asserted as current expectations, so closing either fails the test that describes it.

Provenance (byte comparison against a specification or simulator checkout) is skipped unless `EBUS_SPEC_DIR` / `PANELBENCH_DIR` are set, so conformance and coverage run everywhere while the byte checks stay opportunistic. The wire capture is compared on
shape rather than bytes for the same reason its values are not asserted. Provenance proves the right bytes were copied; it cannot prove they were understood, which is what the other two are for.

### Fixed

- **The conformance check was reading the wrong set of names.** Built from `_PROPERTY_FIELD_MAP` alone, it covered only properties that carry field metadata and silently skipped everything the snapshot mapper reads directly — the MID, `connection`
  feeds/fed-by, `info/direction`. `grid_state`, the most recently corrected mapping in this package, was among them. The read set is now derived from the source itself, so it cannot fall behind the code; that immediately surfaced `info/direction` as a
  fifteenth undeclared extension.
- **The bootstrap floor is raised to 3.0.0b3**, which is where it should always have been: this parser imports `SpanMidSnapshot`, and 3.0.0b2 does not define it. The declared `>=3.0.0b2` let a resolver pair this wheel with 3.0.0b2 and fail on import.
  Caught before the first release that would have shipped it. `schema-0` keeps its b2 floor; every name it imports is present there, checked rather than assumed.

## [0.1.0b2] - 08/2026

Pre-release. Corrects the dependency floor `0.1.0b1` shipped with, and follows the reshaped `SchemaAdapter` protocol released in `span-panel-api` 3.0.0b2.

### Added

- **`ADAPTER_CONTRACT = 1`**, declaring which version of the bootstrap-to-adapter contract this parser was built against. Declared as a literal rather than imported from `span_panel_api.protocol`: a value read from the installed bootstrap would agree with
  every bootstrap, which is exactly the disagreement the check exists to find.

### Fixed

- **The `span-panel-api` floor was `>=3.0.0b1`, which no published bootstrap could satisfy in practice.** `0.1.0b1` was built against a bootstrap that reads the panel's `data-model-version` and constructs adapters with the whole schema; the only bootstrap
  on PyPI at the time did neither. Its `V2HomieSchema` had no `data_model_version` field at all, so a `1.x` panel could not even be represented, and its factory hardcoded the version to `None` — meaning this adapter was discoverable and never selectable.
  The floor is now `>=3.0.0b2`, the first release where both hold. Nothing was installed against the old floor; the combination was unreachable rather than broken in the field.

## [0.1.0b1] - 08/2026

Pre-release. First release as a standalone distribution, and the first parser for the parent/child data model.

### Added

- **`SchemaOneAdapter`**, registered as `schema_1` under the `span_panel_api.schema_adapters` entry-point group. A panel reporting `data-model-version` `1.x` resolves to it; a panel without this package installed still gets the named
  `SpanPanelAdapterMissingError`, so installing it is the opt-in.
- **`ControllerRoutes`** — an `ebus_sdk.MqttControllerTransport` that records `Controller`'s subscriptions instead of making them, so the SDK parses the tree over span-panel-api's own connection to the panel's broker. The adapter is built before a
  connection exists and never receives one; a single wildcard subscription made by the transport layer covers the whole tree, and this routes each message to whichever SDK callback asked for it.
- **The snapshot mapper.** Sorts the tree by declared device type — never by device id — and maps it onto `SpanPanelSnapshot`: circuits, both lugs, the MID, and the BESS/PV/EVSE devices.
- **Panel size from `info/model`** via `PANEL_SIZE_BY_MODEL`, which is what restores the unmapped-position entries the integration builds from the difference between total and occupied spaces. `info/spaces` has no format and the panel publishes no size
  property, so the model is the only source; `panel_model_drift()` reports a model the panel declares that we have no size for, because the alternative is a user noticing missing positions.
- **Field metadata read from each device's `$description`** rather than a schema document. The same capability type exposes different properties on different device classes — `meter` is voltage on the panel, power and energy on a circuit, both currents on
  lugs — so the per-device description is what this panel actually has.
- **A `py.typed` marker**, so consumers type-check against this package's real annotations.

### Known deviations and deliberate gaps

- **`set_dominant_power_source_topic()` returns `None`.** The v1.0 property split into `grid-forming-entity` and `asserted-islanding-state`, which are different controls on different devices rather than a rename. `None` makes the transport reject the
  command instead of publishing where nothing listens; which successor to expose is a product decision.
- **`dsm_state`, `current_run_config`, `grid_islandable` and `pv.relative_position`** have no direct v1.0 equivalent and are left to the product decisions tracked separately. Fields the mapper declines carry no metadata row, so the integration never
  validates against a field nothing populates.

### Fixed before first release

Both found by verifying reconnect against a live broker, and both presented as a healthy connection.

- **Messages arriving before the SDK registered a route for them were dropped.** `Controller` learns its topics as it walks the tree, but one subscription delivers the whole tree at once in whatever order the broker replays its retained store. Seeded
  children-first, a 40-space panel parsed as zero circuits. Unrouted messages are now held and released when the matching route appears — the value a per-device subscription would have been given at subscribe time — with a ceiling so an unclaimed subtree
  cannot leak.
- **Readiness asked only about the root**, so a connection completed with a fraction of its circuits and no panel size. It now waits for every declared device to describe itself, at any depth. Child _state_ is deliberately not required, so an offline DER
  does not block a connection; the model is required only when the root's description declares it.
- **`grid_state` read the wrong one of the MID's two grid properties.** The MID publishes both `grid/islanding-state` (`ON_GRID`/`OFF_GRID`/`UNKNOWN`) and `grid/grid-state` (`UP`/`DOWN`/`DEGRADED`/`UNKNOWN`). The flat schema's `grid_state` was the BESS's
  `grid-state`, an islanding answer, so its successor is `islanding-state`; `grid/grid-state` asks whether the utility supply is healthy and is new in v1.0 with no flat equivalent. Matching on the property name rather than the value set put `UP` where a
  consumer expects `ON_GRID` — an entity keeping its id and history while its vocabulary silently changed. `grid/grid-state` is left unmapped, being a new signal rather than a replacement for an existing field.
