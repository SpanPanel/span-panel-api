# Changelog

All notable changes to `span-panel-api-schema-1` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Note that this package versions on the **library-API axis**, not the wire-format axis. The wire format it parses is the parent/child device tree SPAN firmware `r202633+` publishes, identified by `SUPPORTS_DATA_MODEL_VERSIONS` rather than by this version
number. A release here means this parser changed, never that the panel did.

Pre-releases are not listed separately. A beta is a step towards the next public version, so its changes are folded into that version's entry as they land and are described against the last public release, never against the beta before it.

## [1.1.0]

Requires `span-panel-api` **3.1.0 or newer**, and the two must be upgraded together in both directions: this wheel is rejected at discovery by a 3.0.x bootstrap, and a 1.0.0 wheel is rejected by 3.1.0.

### Changed

- **BREAKING: `set_circuit_relay_topic`, `set_circuit_priority_topic`, `set_dominant_power_source_topic` and `set_evse_charge_limit_topic` become `set_*_target`, returning a `ControlTarget` instead of a topic string.** 3.1.0 verifies a control write by
  matching the topic it published to against the property that reports the change, and only an adapter knows both. It matters more here than on the flat side: under parent/child a device is a peer of the panel rather than a node beneath it, so the topic
  grammar the transport would have to parse is not even the same one. `ControlTarget` returns the topic and the `(device_id, node_id, property_id)` triple from a single call, in the spelling `_on_property_changed` reports under.

  Renamed rather than re-typed under the old name so that the mismatch is caught at discovery, where the remedy can be named, instead of surfacing as an `AttributeError` on a `str` deep inside a setter. `ADAPTER_CONTRACT` stays **1** — the contract's
  member list changed, which discovery already checks by name.

## [1.0.0]

First release as a standalone distribution, and the first parser for the parent/child data model. Requires `span-panel-api` 3.0.0 or newer, and `ebus-sdk` `>=0.19,<0.24`.

### Added

#### The adapter

- **`SchemaOneAdapter`**, registered as `schema_1` under the `span_panel_api.schema_adapters` entry-point group. A panel reporting `data-model-version` `1.x` resolves to it; a panel without this package installed still gets the named
  `SpanPanelAdapterMissingError`, so installing it is the opt-in.
- **`ADAPTER_CONTRACT = 1`**, declaring which version of the bootstrap-to-adapter contract this parser was built against. Declared as a literal rather than imported from `span_panel_api.protocol`: a value read from the installed bootstrap would agree with
  every bootstrap, which is exactly the disagreement the check exists to find.
- **`ControllerRoutes`** — an `ebus_sdk.MqttControllerTransport` that records `Controller`'s subscriptions instead of making them, so the SDK parses the tree over span-panel-api's own connection to the panel's broker. The adapter is built before a
  connection exists and never receives one; a single wildcard subscription made by the transport layer covers the whole tree, and this routes each message to whichever SDK callback asked for it.
- **The snapshot mapper.** Sorts the tree by declared device type — never by device id — and maps it onto `SpanPanelSnapshot`: circuits, both lugs, the MID, and the BESS/PV/EVSE devices.
- **Retained messages are held until their route exists.** `Controller` learns its topics as it walks the tree, but one subscription delivers the whole tree at once in whatever order the broker replays its retained store; seeded children-first, a 40-space
  panel would otherwise parse as zero circuits. Unrouted messages are held and released when the matching route appears — the value a per-device subscription would have been given at subscribe time — with a ceiling so an unclaimed subtree cannot leak.
- **Readiness asks about every declared device, at any depth**, not only the root, so a connection cannot complete with a fraction of its circuits and no panel size. Child _state_ is deliberately not required, so an offline DER does not block a connection;
  the model is required only when the root's description declares it.
- **Panel size from `info/model`** via `PANEL_SIZE_BY_MODEL`, which is what restores the unmapped-position entries a consumer builds from the difference between total and occupied spaces. `info/spaces` has no format and the panel publishes no size
  property, so the model is the only source; `panel_model_drift()` reports a model the panel declares that we have no size for, because the alternative is a user noticing missing positions.
- **Field metadata read from each device's `$description`** rather than a schema document. The same capability type exposes different properties on different device classes — `meter` is voltage on the panel, power and energy on a circuit, both currents on
  lugs — so the per-device description is what this panel actually has.
- **A `py.typed` marker**, so consumers type-check against this package's real annotations.

#### Mapping decisions worth knowing

- **`grid_state` reads the MID's `grid/islanding-state`, not its `grid/grid-state`.** The MID publishes both: `islanding-state` is `ON_GRID`/`OFF_GRID`/`UNKNOWN`, and `grid-state` is `UP`/`DOWN`/`DEGRADED`/`UNKNOWN`. Flat's `grid_state` was the BESS's
  `grid-state`, an islanding answer, so its successor is `islanding-state`; matching on the property name rather than the value set would put `UP` where a consumer expects `ON_GRID` — an entity keeping its id and history while its vocabulary silently
  changed. `grid/grid-state` asks whether the utility supply is healthy, is new in v1.0 with no flat equivalent, and is left unmapped as a new signal rather than a replacement.
- **`dominant_power_source` reports `GRID` on a panel with no MID**, rather than nothing. The field's source moved: flat published a closed enum of source classes on the panel, and v1.0 names the forming device on the MID's `grid` node — so a panel with no
  battery has no MID, the property has no publisher, and the field would go `None`. Observed on a live install that read `Grid` on flat all night and went unknown the moment it upgraded, with nothing about the site having changed.

  A missing MID settles the answer by elimination rather than leaving it open. `BATTERY` needs a BESS and a BESS brings a MID; `PV` cannot form a grid alone, because anything that can is a grid-forming inverter and therefore a MID; `NONE` describes a panel
  supplying nothing, which is a panel that is not publishing. What remains is a generator, and that is two cases of which only one reaches here: a generator wired through a MID is named by that MID and answered before this point, while a generator with no
  MID interface is what SPAN treats as the grid — and it is the only kind an install with no MID can have. The elimination therefore keeps holding if MID-integrated generators arrive, because they bring a MID. A site running off-grid without storage is not
  a counterexample: it goes dark at sunset.

  This deliberately does not follow `resolve_islanding_state`, which refuses the same shortcut, and the counterexample that defeats it there is what supports it here: a generator-fed island **is** islanded, so inferring on-grid from a missing MID would be
  wrong, while its grid-forming entity really is what SPAN calls the grid. A MID that exists and has not answered still reports nothing — that is genuinely unknown, and distinct from there being no islanding authority at all.

- **`battery.power_w` is discharge-positive.** The enclosure meters the BESS the way it meters a circuit it feeds, so a discharging battery publishes a negative `meter/active-power` and the mapper negates it. Positive therefore means power flowing _out of_
  the battery, which is the eBus rule for a device's own meter. It stays deliberately opposite to `panel.power_flow_battery`, the enclosure's arbitrated figure, which is passed through untouched by both adapters and is charge-positive. The two are the same
  physical power in different frames, and a consumer rendering both negates one of them.

#### Adoption and vendor extensions

- **`adoption`, building `AdoptedDevice` records for device types this parser does not model**, with `set_topic` populated only where the declaration says the property is settable. Subtype-aware, so a curated device never lands in `adopted_devices`.
- **Vendor properties on modelled devices are emitted with their values.** Every property a modelled device declares that this adapter maps to no snapshot field — excluding `info` and `connection`, which resolve to the device card and the tree — arrives as
  an `ExtensionProperty` carrying its subject, its declaration and its retained value. A battery vendor hanging `battery-2/cell-temperature` off the BESS would otherwise reach a consumer nowhere.
- **The two lugs devices are two extension subjects, not one.** A subject is an _identity_: a consumer keys an entity on `(kind, instance_key, node/property)`, so pairing both lugs with a single subject would give one identity for two readings, and
  identical firmware on both lugs makes that the expected case rather than a coincidence. They are `kind="lugs"` with `upstream`/`downstream` as the instance key, matched on `info/direction` for the reason `find_lugs` documents: the reference tree's ids
  are the simulator's naming, and the direction property is what the schema defines. A lugs device declaring no direction is left unpaired rather than keyed on something unstable — its properties stay in discovery, which is where an unidentifiable device
  belongs.
- **`node_has_curated_siblings`**, one bit per row: whether this adapter reads any _other_ property of the same node. A vendor extending `meter` is probably extending the meter, and that is the whole of what the bit says — which fields are read stays
  internal. `addressed_rows()` is shared with `build_discovery`, so the discovery rows and the extension rows cannot disagree about what "unaddressed" means.

#### Conformance against the specification and the producer

- **`spec_lock.json` ships with the package** and records what this parser targets: the firmware range, the eBus specification commit its vocabulary was read from, and the version of every capability, device and registry it implements. It is the consumer
  counterpart to the simulator's publisher lockfile, and both are pinned to the same specification commit — though the anchor shared between them is the **firmware range**, not that commit, because the specification says what a device class _may_ publish
  while a panel publishes one specific tree.
- **The capability catalogs this adapter addresses are byte-copied under `spec/`**, along with the device-types registry. Vendored rather than depended on because the specification is a git repository of versioned documents, not a package. They exist to be
  checked against, never parsed in production: units and datatypes still come from each device's `$description`, since the catalog is the superset across all hardware rather than a statement about the panel in front of us. Formatting hooks are excluded
  from `spec/`, because a lint fix there would quietly invalidate the byte comparison that makes the copies worth having.
- **A conformance suite that asks the consumer's question rather than the publisher's.** A publisher asks whether everything it emits is legal, and for it an omission is unremarkable. This asks whether every name the adapter _reads_ is one the
  specification defines — because a consumer addressing a name that no longer exists does not fail, it goes quiet: the property never arrives, metadata lookup returns `None`, and an entity disappears. The read set is derived from the source itself rather
  than from the metadata table alone, so it cannot fall behind the code.
- **An explicit SPAN extension allowlist.** A number of the properties this adapter reads are absent from every catalog — per-phase meter readings, panel link states, circuit `spaces`, `info/direction`, the EVSE surface. All are legal, since the
  specification permits properties it has never heard of. They are enumerated with reasons so that a name missing from the catalog must be a deliberate claim about SPAN's vocabulary rather than an unnoticed typo; at runtime the two are indistinguishable.
  Tests also fail when an extension is later adopted upstream, or when one is declared for a property nothing reads.
- **The catalogs are used as a validator, not just as a vocabulary list.** `span_panel_api_schema_1.catalog` compares a declared `unit` or `datatype` against the catalog's definition of the same property, which is the comparison that catches a mislabel.
  Agreement is silence; disagreement is surfaced, never silently resolved — a finding is not a licence to change a wire reader to match the catalog, nor to assume the catalog is right, since both sides have been wrong. Divergences are recorded with what
  the wire says, what the catalog says, which producers show it, a reason and a date, and the baseline fails in **both** directions: a new divergence fails until somebody records it, and a recorded divergence that has disappeared fails until its line is
  removed. That second direction is what keeps the register self-cleaning rather than a suppression list.
- **An abstract unit is a dimension, and comparing it as a string would report conformance as the defect.** `soc/soe`, `soc/total-energy-storage`, `soc/loadup-headroom` and `info/nameplate-capacity` are all `unit: "energy"`, which the specification
  requires a publisher to substitute a real unit for — a BESS in kWh, a water heater in Wh. `UNIT_FAMILIES` enumerates membership rather than deriving it from an SI-prefix rule, so a member is silent, echoing the placeholder back is a finding, and an
  energy unit nobody enumerated is a question for a human.
- **A peer record and a producer coverage check.** `spec_lock.json` records the producer this parser is developed against — the SPAN simulator, `role: publisher` — and a capture of the tree it publishes is vendored alongside the catalogs, so two sides
  reading different vocabularies is a test failure rather than something noticed later. Of the `(capability, property)` pairs this adapter reads, the capture declares all but `grid/islanding-state`: the simulator models a MID but its tracked config
  publishes none. That gap is recorded rather than left implicit, because a passing suite otherwise reads as coverage it does not have, and the entry is rejected once the simulator starts publishing it.
- **The parser is driven end to end from what the producer actually publishes.** `spec/fixtures/simulator_wire.json` is a capture from SPAN's publisher — descriptions, `$state` and all property values across every device — fed in sorted topic order, the
  way a retained store replays it rather than the way a tree is walked. The parser reaches ready on it, sizes the panel from `MAIN_40`, and parses all 30 circuits. Values are deliberately not asserted: the producer's config carries `noise_factor` and its
  clock advances, so pinning a wattage would fail on every recapture for a reason nobody could act on.
- **Provenance is opportunistic; conformance is not.** Byte comparison against a specification or simulator checkout is skipped unless `EBUS_SPEC_DIR` / `PANELBENCH_DIR` are set, so conformance and coverage run everywhere while the byte checks stay
  opportunistic — and CI sets both, so a skip there is a failure. Provenance proves the right bytes were copied; it cannot prove they were understood, which is what the other two are for.

#### Reference payloads

- **`span_panel_api_schema_1.reference_payloads`, shipping `parent_child_tree.json` as package data.** The captured retained-topic tree of a full 40-space panel is reached by `parent_child_tree()` rather than by path. Consumers outside this repository need
  a real capture to check an adapter's output against, and the only alternative to shipping one is vendoring a byte copy that has no version and goes stale in silence. It ships from _this_ distribution rather than the bootstrap because a retained topic
  tree is only interpretable by the parser that speaks its vocabulary — and the eBus SDK that turns it back into devices is this distribution's dependency alone.
- **`devices_from_tree` and `device_from_topics`.** A tree is not directly usable: every consumer has to replay the retained topics through `DiscoveredDevice` first, and that replay is this parser's own knowledge of how the transport feeds it. Shipping the
  capture without the replay would just move a copy of that logic into every consumer. `devices_from_tree` takes the tree rather than reading it, so a consumer can filter the capture first — dropping the BESS to model a panel that has none — and still
  build devices the same way.

### Known deviations and deliberate gaps

- **`set_dominant_power_source_topic()` returns `None`.** The v1.0 property split into `grid-forming-entity` and `asserted-islanding-state`, which are different controls on different devices rather than a rename. `None` makes the transport reject the
  command instead of publishing where nothing listens; which successor to expose is a product decision.
- **`pv.relative_position` has no v1.0 equivalent** and is left to the product decisions tracked separately. Fields the mapper declines carry no metadata row, so a consumer never validates against a field nothing populates.
- **`grid_islandable` returns `None` until something publishes it.** It maps to `grid-forming/capable` over the BESS's inverter children, as the disjunction — a panel does not island, its DER does. No producer publishes it today, which is recorded rather
  than worked around; `None` keeps absence a gap instead of a claim.
- **Two producer-side gaps are pinned rather than left to be noticed.** `grid_state` stays `None` on the captured tree because nothing instantiates a MID, and every DER — BESS, PV and both EVSEs — declares `info/model` in its `$description` and never
  publishes a value. The second breaks the single standing obligation eBus places on a publisher, to declare accurately what it publishes, and is invisible to a conformance checker: comparing declarations against catalogs cannot see a declaration nothing
  fulfils. Only a capture carrying values can, which is the argument for that fixture existing. Both are asserted as current expectations, so closing either fails the test that describes it.
