# Changelog

All notable changes to `span-panel-api-schema-1` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Note that this package versions on the **library-API axis**, not the wire-format axis. The wire format it parses is the parent/child device tree SPAN firmware `r202633+` publishes, identified by `SUPPORTS_DATA_MODEL_VERSIONS` rather than by this version
number. A release here means this parser changed, never that the panel did.

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
