# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0b12]

### Added

- **A vendor property on a device this library already models now reaches a consumer, instead of stopping at diagnostics.** The schema is explicitly vendor-extensible and adoption covered only half of that: a device type nothing here models is adopted with
  its readings, while a new property on the BESS, a charger, a circuit or the panel became a `DiscoveredMetadata` row — a declaration, no value, visible only to a maintainer reading a diagnostics attachment. `SpanPanelSnapshot.extension_properties` carries
  those properties with their values, so a consumer can render what the publisher published.
- **`ExtensionProperty` and `ExtensionSubject`.** The subject names which modelled snapshot subject a property hangs off — `battery`, `mid`, `pv`, `panel`, and `evse`/`circuit` with the instance key the snapshot's own maps use — so a consumer resolves the
  device it belongs on with a lookup it already performs. What is _not_ exposed is the field-level mapping: the subject is one value per device and cannot drift, while the wire-property-to-snapshot-field map is the adapter's internal business and exporting
  it would freeze it as API.

### Notes

- **The value never reaches diagnostics, and that is structural rather than remembered.** `ExtensionProperty` is deliberately not a `FieldMetadata`, so it cannot enter the map `partition()` walks and has no path into a payload that leaves the machine. The
  discovery rows keep flowing unchanged: the same property appears in both surfaces on purpose, joined by its `{node}/{property}` path — a declaration for the maintainer, a reading for the user.
- **Read-only by construction.** An extension property carries `settable` for curation triage and no set topic, and there is no member a write path could be built from. These properties live on exactly the devices whose curated controls do real work — the
  EVSE limit refuses a value above the commissioned ceiling, the islanding assertion translates `GRID` into `ON_GRID` — and a generic write beside them would have neither.
- **Additive in both skew directions.** The snapshot field defaults empty, so an older adapter degrades to the previous behaviour with no new `SchemaAdapter` member required — a required member would fail at _discovery_, taking down every install whose
  adapter wheel lags the bootstrap by a release. The reverse skew is handled by `span-panel-api-schema-1` 0.1.0b9 declaring this release as its floor.

## [3.0.0b11]

Carries `span-panel-api-schema-1` 0.1.0b8, which restores `dominant_power_source` on a panel with no MID. No change in this distribution.

## [3.0.0b10]

### Changed

- **The wait for a panel to finish rebooting no longer gives up.** It used to stop after a fixed number of attempts, and that bound was wrong twice for the same reason: it was sized against a reboot somebody had measured, and the next reboot was not that
  reboot. Giving up has nothing to recommend it — the only things that start another attempt are the reconnect edge and the panel republishing its data-model version, and a panel that finishes booting after the wait expired produces neither, so running out
  of attempts means stranded until somebody reloads by hand. It now waits as long as the panel takes.
- **Waiting costs nothing you were relying on.** Energy sensors already hold their last reading through an outage on their own grace period — fifteen minutes by default, configurable — which exists precisely so a gap does not become an `unknown` and a
  statistics spike. That is untouched by how long this waits, and it was the only thing that would have justified a deadline. What is left is one request every thirty seconds to a device on your own network.
- **The retry interval settles at thirty seconds rather than growing.** Backing off without a ceiling would mean a panel that took a while to return was then ignored for longer than it took. The gap goes 1, 2, 4, 8, 16, 30 and stays there, so once your
  panel is answering it is noticed within half a minute however long the wait has already run.

### Fixed

- **Four more ways a booting panel answers now count as "not ready" rather than as a hard failure.** b8 and b9 covered the 502 that a live upgrade produced; review found the fix had covered the observed shape rather than the class. A panel resetting its
  listener mid-request raises `ReadError` or `WriteError`, a proxy that dies mid-request raises `RemoteProtocolError`, and a panel part-way through starting can answer `200` with a truncated or empty body. All four escaped untranslated, skipped the retry
  entirely, and stranded the parser exactly as the 502 did. Transport failures are now `SpanPanelConnectionError` and an unusable body is `SpanPanelServerError`.
- **The last retry attempt happens after the reboot it is sized for.** The window ended with a sleep that no attempt followed: it read 241 seconds while the final request went out at 211, so a panel ready at 220 was still abandoned. The loop no longer
  sleeps after its final attempt — which also stopped it holding the in-flight guard, and the warning, for a pointless extra backoff — and the last request now lands at 241 seconds. The test asserts that offset instead of summing the sleeps, which was
  restating the implementation's own off-by-one.
- **The give-up warning no longer promises a recovery that cannot arrive.** It said data would read as missing "until the next reconnect". The triggers are the reconnect edge and the retained `data-model-version` message, and a panel that finishes booting
  produces neither again, so exhausting the window means stuck until a reload. It now says so.

## [3.0.0b9]

### Fixed

- **The retry window for a rebooting panel is actually widened this time.** b8 taught the schema fetch to treat a `502` as "not ready yet" but shipped with the old five attempts capped at eight seconds — about twenty-three seconds against a panel observed
  taking four minutes to come back, still answering 502 when the broker returned. The widening was written, lost to a failed edit in the same change, and shipped without it; nothing failed, because catching the 502 and giving up early looks exactly like
  working. Now twelve attempts backing off to thirty seconds, a little over four minutes, and pinned by a test that asserts the total window outlasts the observed reboot rather than checking the constants individually.

## [3.0.0b8]

### Fixed

- **A panel answering `502` while it reboots no longer costs the automatic reload.** When a panel upgrades its firmware it drops MQTT, comes back, and serves HTTP a little later — and a booting device brings its network stack and reverse proxy up before
  the application behind them, so the schema fetch is answered with `502` rather than refused. The retry that exists for exactly this handled "cannot reach" and "timed out" but not "answered, with 502", so the first attempt raised straight out of the loop,
  out of the fire-and-forget task that called it, and the parser was never swapped. Caught on two Home Assistant instances watching one panel through the same live upgrade: both logged `Task exception was never retrieved`, both stayed on the old parser,
  and neither recovered without a manual reload. `get_homie_schema` now raises `SpanPanelServerError` for any 5xx — "not ready yet", distinct from a 4xx that will not fix itself — and the retry treats it as retryable.
- **The wait is now the length of a real reboot.** Five attempts backing off to 8s gave up after about 23 seconds. The observed upgrade took four minutes from MQTT dropping to the broker returning, with HTTP still answering 502 at that point. Twelve
  attempts backing off to 30s covers it.
- **Nothing escapes the redispatch task any more.** An unexpected failure there used to surface as a bare `Task exception was never retrieved` while the parser silently stayed on the old generation — the failure the redispatch exists to prevent, reached by
  another route. It is now logged at ERROR naming the consequence and the remedy, because a reload is the user's only move and nothing else was going to tell them.

## [3.0.0b7]

### Changed

- **`SpanMqttClient` accepts an `httpx_client`, and so does `create_span_client`.** Four config-flow-facing entry points already took an injected client; the runtime path was the one that did not, so every schema read built a throwaway — including the
  retry loop that runs during a firmware upgrade, which built one per attempt at exactly the moment the panel was mid-reboot. Optional and defaulted, so nothing outside Home Assistant changes. The ownership rule is the one the existing entry points already
  state: a client handed in is never closed here, and its timeouts, limits and headers are the caller's, which is why the per-call `timeout` defaults are ignored when one is given. Home Assistant's shared client carries httpx's default timeout rather than
  this library's 10 s, and that is the caller exercising the policy it owns rather than a setting being lost.

## [3.0.0b6]

### Added

- **`SpanPanelSnapshot.lugs_at_service_entrance`, saying whether this enclosure's upstream lugs are the utility connection point.** `instant_grid_power_w` is those lugs' `meter/active-power`, and the name holds only at the service entrance: a BESS wired
  ahead of the main lugs, or an enclosure fed by another enclosure, leaves the lugs metering panel-side flow while the utility side differs by whatever that device contributes or absorbs. `power_flow_grid` stays site-level and correct in both, so the two
  legitimately disagree — and before this a consumer seeing them disagree could not tell a topology from a fault. Sourced from the lugs' `connection/fed-by-device-id`, which `power-flows` 0.3 names as the detection mechanism when it qualifies its own
  negation table; this library already read that property and then discarded it, so no consumer could compute this for itself. Defaults `True` because flat firmware predates chaining and a flat panel's lugs really are its service entrance, so schema_0
  leaves it alone. Additive, so it costs no protocol member and no contract bump. Worth knowing: the reference capture publishes `fed-by-device-id: bess` on its upstream lugs, so the reference panel reports `False`.

- **An adopted device carries the proxy link it declares: `AdoptedDevice.parent` and `AdoptedDevice.proxied`.** Carried rather than acted on — an adopted device is still registered under the enclosure — because a _proxied_ unmodelled device is a real shape
  that would otherwise be flattened away unrecorded. The reference tree already contains one: `bess-mid` declares `parent: bess`, the `{proxier-id}-{proxied-id}` naming of `devices/proxy.md`. `proxied` is derived against the tree `root` in the adapter,
  because device ids are opaque and a consumer holding one device cannot tell the enclosure's id from a sibling's.
- **The nesting is deliberately not built yet.** [python-sdk#49](https://github.com/electrification-bus/python-sdk/issues/49#issuecomment-5359203067) records that proxied ids differ by design and that consumers correlate by `info/serial-number` rather than
  by device id, and `ebus-sdk` 0.21.0 shipped `DeviceSpec`/`DeviceTreeBuilder` ([python-sdk#57](https://github.com/electrification-bus/python-sdk/issues/57)) with the graph builder still to be reconciled against it. The tree model is being reshaped
  upstream, so the fields capture the evidence and the topology waits.

- **A settable property on an adopted device can be written, and the write cannot reach anything else: `AdoptedProperty.set_topic` and `SpanMqttClient.set_adopted_property`.** The topic is populated only for a settable property on a device `is_modelled`
  rejects, so it is the scoping that authorises the write rather than a check a caller has to remember. The transport resolves the property against the current snapshot's `adopted_devices` and publishes to the topic that property carries; no topic is
  accepted from the caller, and a device this library models produces no `AdoptedDevice` to find.
- **The alternative was a `set_property_topic` member on `SchemaAdapter`, and it was rejected for two independent reasons.** It would have put every curated control one argument away, and two of them do real work on the way out —
  `dominant_power_source_payload` translates `GRID` into the `ON_GRID` the v1.0 islanding assertion accepts, and `evse_charge_limit_payload` refuses a value above the commissioned ceiling because publishing past it is the one write with a physical
  consequence. It would also have been required of every adapter package, since `_derive_required_members` derives the required set from the protocol, so an installation carrying an older adapter wheel would have failed at _discovery_ rather than losing
  one feature.
- **No translation and no bounds check on an adopted write, deliberately.** Both exist on curated controls because this library knows what those properties mean. It knows nothing about an adopted one beyond its declaration, and inventing a bound would be
  inventing a fact about somebody else's hardware. The consumer constrains the value to the declared `format`; the panel stays the authority on whether to accept it.
- **`AdoptedControlProtocol`**, so a consumer asks `isinstance` before offering the control, exactly as it does for circuit, panel and EVSE control.

- **A device type this adapter models nothing for is reported whole rather than ignored: `SpanPanelSnapshot.adopted_devices`.** `TreeRoles` sorts the tree into the roles the snapshot needs, and anything that matches none of them has always fallen off the
  end silently — a panel publishing a device nobody modelled produced no field, no metadata row and no sign it was there. The schema is explicitly vendor-extensible, so that is an expected arrival rather than a hypothetical one. `AdoptedDevice` carries the
  device's identity and its readings; `span_panel_api_schema_1.adoption` builds one per unmodelled child.
- **The unit is a device, never a property, and that is the whole design.** A new property on a device this adapter already models is a curation task with a short turnaround, and surfacing it automatically spends a consumer's entity identity permanently on
  a shape a human would likely have chosen differently — the sixteen `pcs` properties that curation collapsed into one entity and thirteen attributes are the worked example. An unmodelled _type_ is the opposite case: no curation is coming, so the silence
  is the only alternative. Extra instances of a modelled type are deliberately not adopted either: a second BESS is a multiplicity limit, not an unmodelled device, and adopting it would stand a machine-named record beside a curated one for the same
  hardware.
- **`info` and `connection` resolve away from readings, by node rather than by property name.** `info` is a device's build identity and becomes the card fields `AdoptedDevice` carries; `connection` is topology and becomes the device link. The partition is
  keyed on the node because the catalogs carry no marker for "this string is a device reference", which leaves a hard-coded name list as the only alternative — and such a list goes stale silently: `ebus-sdk`'s own `topology.py` covers `feeds-device-id` and
  `fed-by-device-id` and omits `grid-forming-entity`, which lives on the `grid` capability. A node is what the vocabulary defines, so keying on it cannot go stale the same way.
- **`AdoptedProperty` carries the value; `DiscoveredMetadata` still must not.** The two answer opposite questions and are separate types so that conflating them is a type error. Discovery rows are built to be forwarded in consumer diagnostics, which leave
  the machine, so they carry declarations only. An adopted property exists to become an entity on the machine that built it, so it carries the reading — along with the declared `format` and `settable` flag, which are together the value domain a consumer
  needs to build a control rather than a reading.
- **Additive, and deliberately not a protocol member.** `adopted_devices` defaults to `()`, so schema_0 — which has no device tree to find an unmodelled device in — is untouched, and `ADAPTER_CONTRACT_VERSION` does not move. `SchemaAdapter` derives its
  required members from itself, so a member there would be required of every adapter package and would invalidate built wheels.

- **The capability catalogs are used as a validator, not just as a vocabulary list: `span_panel_api_schema_1.catalog`.** Sixteen catalogs have been vendored since v1.0 landed and were read only to assert that a catalog _exists_ for every node the adapter
  addresses. Nothing compared a declared `unit` or `datatype` against the catalog's definition of the same property, which is the comparison that catches a mislabel — and the one mislabel this repository has met (`meter/active-power` declared `kW` while
  the values are watts, a 1000x error) was found because a person noticed a sibling device declaring the same quantity differently. The new module compares one declaration against one catalog definition and classifies the result;
  `tests/test_catalog_divergence.py` runs it across all four vendored producer captures and holds the outcome against an acknowledged-divergence register.
- **Agreement is silence; disagreement is surfaced, never silently resolved.** A finding is not a licence to change a wire reader to match the catalog, nor to assume the catalog is right — both sides have been wrong. It is recorded in `_REGISTER` with what
  the wire says, what the catalog says, which producers show it, a reason and a date, and the baseline fails in both directions: a new divergence fails until somebody records it, and a recorded divergence that has **disappeared** fails until its line is
  removed. That second direction is what keeps the register self-cleaning rather than a suppression list.
- **An abstract unit is a dimension, and comparing it as a string would report conformance as the defect.** `soc/soe`, `soc/total-energy-storage`, `soc/loadup-headroom` and `info/nameplate-capacity` are all `unit: "energy"`, which the specification
  requires a publisher to substitute a real unit for — a BESS in kWh, a water heater in Wh. `UNIT_FAMILIES` enumerates membership rather than deriving it from an SI-prefix rule, so a member is silent, echoing the placeholder back is a finding, and an
  energy unit nobody enumerated is a question for a human. A catalog unit token that is neither a known family nor a known concrete unit fails until it is classified, so a new abstract family upstream cannot arrive as sixty false findings.
- **An absence is terminal and is reported once.** A property no catalog defines — the EVSE's `config` node, which is not an eBus capability at all, and the `status`/`meter`/`info` extensions SPAN publishes — has no definition to disagree with, so it is
  reported as absent rather than as every field mismatching against nothing. That keeps `_SPAN_EXTENSIONS` the single home for the read-set half of that question instead of duplicating its judgements here.
- **The flat schema document is surveyed too, and it is where the known mislabel lives.** It declares properties per device type with no capability node to look a catalog up by, so its properties reach the catalogued vocabulary through the snapshot field
  path both adapters' metadata tables already name — derived from those tables rather than restated, so the join cannot outlive them. The join is admitted only where the two sides spell the property identically: fifteen flat properties reach a catalogued
  property under a different name (`dipole` for `breaker/poles`, `software-version` for `info/firmware-version`), and comparing across a rename would invent divergences out of the pre-catalog spelling that having two adapters already handles.

- **Per-DER connection health reaches the snapshot: `SpanEvseSnapshot.connected` and `SpanPVSnapshot.connected`.** `battery.connected` has carried the enclosure's view of the link to the BESS since v1.0 landed, from the upstream lugs'
  `connection/fed-by-device-status`. The other half of the same capability — a circuit's `connection/feeds-device-status`, which is how the enclosure reports the link to a PV or a charger — reached nothing, so only one of a panel's three DER classes had a
  link-health field. Both new fields are `bool | None` and mirror `battery.connected` exactly, read by `build_pv` and `build_evse` through the new `feed_connection_statuses`.
- **`None` is the specification's "unknown", and it is load-bearing.** The enum is `OK,LOST,DEGRADED` with no `UNKNOWN` member, so an unpublished property is the only way a panel can say it does not know — and `distribution-enclosure.md` states that a
  mixed-load or unsurveyed circuit publishes no connection record at all, which is the normal state for most of a panel's circuits. So absence is never a fault: a DER no circuit claims, or one whose circuit publishes an id without a status, reports `None`
  rather than `False`. `DEGRADED` collapses to `False`, because the question this field answers is whether the enclosure can talk to the device.
- **The charger's link is not the charger's session.** `evse.status` is the OCPP-style state the charger reports about the cable in front of it; `evse.connected` is the enclosure reporting whether it can reach the charger at all. A charger mid-session over
  a lost link publishes `CHARGING` and `connected=False` at once, and the two fields stay separate for the same reason `battery.connected` and `battery.communication_state` do.
- **`_PROPERTY_FIELD_MAP` rows for both**, from `(circuit, connection, feeds-device-status)` — the one place a row's device type and its field path deliberately differ, because v1.0 states the relationship on the circuit and the field belongs to the DER.
  One property carries two rows, since one circuit's record describes a PV and another's a charger. Both buy the datatype the circuit's own `$description` declares plus the three-way resolution contract.

- **The BESS's own meter and link health reach the snapshot: `SpanBatterySnapshot.power_w` and `SpanBatterySnapshot.communication_state`.** The battery device has published `meter/active-power` and `status/communication-state` all along and neither reached
  a field, so a consumer could show the enclosure's arbitrated `power_flow_battery` and nothing the BESS itself reports. Both are `None` on a BESS that publishes no such node, and on every flat panel — the flat schema's BESS device class declares neither
  property, so this is new surface rather than a re-sourcing, and nothing that exists today changes.
- **`power_w` is discharge-positive, and the wire is not.** The enclosure meters the BESS the way it meters a circuit it feeds, so a _discharging_ battery publishes a negative `meter/active-power`; `build_battery` negates it, exactly as `build_circuit`
  does for a load. Positive therefore means power flowing _out of_ the battery. This entry said charge-positive until the direction was settled by measurement rather than by reading: with the producer driven into self-consumption and the grid at exactly
  zero — PV 4181 W plus battery 1917 W meeting a 6099 W load, so the battery can only be discharging — the snapshot reported `+1917.49`. `_charge_positive` was renamed `_discharge_positive` in the same pass. No published value changed; the negation was
  always there and always right, and only the name and this note asserted a direction the code did not hold.
- The asymmetry with `panel.power_flow_battery` is real and unchanged: the enclosure's own arbitrated figure is passed through untouched by both adapters and is charge-positive, so it reads negative for the same discharging battery that makes `power_w`
  positive. The two describe the same physical power in opposite frames, and a consumer rendering both negates one of them — which is what the Home Assistant integration does, landing both of its entities on discharge-positive.
- **`communication_state` stays the published enum string** (`OK`/`DEGRADED`/`LOST`/`UNKNOWN`) rather than collapsing to a bool: `DEGRADED` is neither `OK` nor `LOST`, and a bool would have to pick one. It is deliberately not merged into
  `battery.connected`, which is the _enclosure's_ `connection/fed-by-device-status` view of the same link. One is the device speaking about itself and the other the panel speaking about it, and the migration guide warns against conflating them.
- **`_PROPERTY_FIELD_MAP` rows for both**, which buys them the unit and datatype the BESS's own `$description` declares plus the three-way resolution contract — a BESS that publishes the node while omitting the property reports degradation rather than
  absent hardware. The row describes the property; the sign flip the mapper applies is not a unit change.

- **`shed-forecast` reaches the snapshot: five new `SpanPanelSnapshot` fields.** `shed_time_to_priority_shed_min`, `shed_total_time_remaining_min`, `shed_full_charge_time_to_priority_shed_min`, `shed_full_charge_total_time_remaining_min` and
  `shed_forecast_confidence`. The enclosure has published `energy.ebus.capability.shed-forecast` 0.1 since r202633 and nothing read it — the backup-planning numbers ("how long before my battery starts shedding circuits", "how long before it is exhausted")
  were on the wire and stopped at the transport. All four times are `integer` minutes as the capability declares, parsed through `panel.integer` so a publisher that serialises a whole number with a decimal point still resolves; `confidence` stays the raw
  `LOW`/`MEDIUM`/`HIGH` string, because it qualifies the four times rather than standing alone. Every field is `None` when the panel publishes no such node, and `None` is load-bearing: zero minutes is a legitimate reading — shedding starts now — so a
  defaulted zero would be indistinguishable from the worst forecast the capability can report. Purely additive; a panel that publishes nothing here is unchanged.
- **`_PROPERTY_FIELD_MAP` rows for the two live estimates**, `panel.shed_time_to_priority_shed_min` and `panel.shed_total_time_remaining_min`. That buys them the unit and datatype the device's own `$description` declares, and with it the three-way
  resolution contract: a panel that publishes the node while omitting one of the two reports degradation rather than absent hardware. The `full-charge-*` pair and `confidence` deliberately get no row — a consumer renders them beside the two live estimates
  rather than as readings of their own, so there is no unit surface for a row to describe.
- **`shed-forecast` 0.1 vendored under `packages/schema-1/spec/catalogs/`** and pinned in `spec_lock.json`, byte-copied from the specification at the recorded `synced_commit`. The conformance suite requires a catalog for every capability node the adapter
  addresses, so a node read without one would be unchecked while looking checked.

## [3.0.0b5] - 08/2026

Pre-release. Publishes the captured schema document consumers were copying by hand.

### Added

- **`span_panel_api.reference_payloads`, shipping `homie_schema.json` as package data.** The captured `GET /api/v2/homie/schema` response moves out of `tests/fixtures/v2/` and into the wheel, reached by `homie_schema()` and `homie_schema_types()` rather
  than by path. It was already being consumed outside this repository: the Home Assistant integration checks the field paths it declares against what an adapter can actually produce, which needs a real schema document, so it vendored a byte copy with a
  README explaining where the copy came from. A copy has no version — it goes stale in silence, and a stale one turns the integration's conformance gate into a check against a schema no panel runs. Shipped, the payload carries the version of the release it
  came with: pin `span-panel-api==3.0.0b5` and you read the bytes that release was written against, with nothing left to keep in sync. `homie_schema_types()` returns `HomieSchemaTypes` — precisely what
  `span_panel_api_schema_0.field_metadata.build_field_metadata` accepts — so a caller building metadata never reaches into an untyped document to get it. This distribution owns the schema document rather than an adapter one because it is the response of
  `get_homie_schema()` here, modelled by `V2HomieSchema` here, and dispatch reads its `data_model_version` to decide which adapter parses the panel at all. The parent/child device tree is the other half and ships from `span-panel-api-schema-1`, with the
  parser that can interpret it.
- **This suite reads the payload through the same accessor.** `test_schema_provenance.py` and `test_detection_auth.py` no longer open a path, so the schema anchor is checked against the bytes a consumer installs rather than against a file that exists only
  in a checkout.

## [3.0.0b3] - 08/2026

Pre-release. Normalises DER identity onto v1.0's vocabulary, and stops deriving the grid answers that v1.0 states outright.

### Changed

- **BREAKING — DER identity speaks v1.0's vocabulary on every device class.** `model` is the human designation and `part_number` the SKU, on `battery`, `evse` and `pv` alike. `product_name` is retired on all three. Flat is the inconsistent side, not v1.0:
  it puts the SKU in `bess/model` and in `evse/part-number`, the same concept under two names, and gives PV neither. `schema_1` used to cross over (`info/part-number` → `battery.model`) to hold each entity's displayed meaning still, which worked and
  permanently encoded flat's irregularity in the snapshot. `schema_0` now translates flat into the normalised shape instead of mirroring it. Measured: every EVSE identity field reads identically on both adapters, so for that device class identity stops
  being a migration delta at all. **`battery.model` changes value for existing flat users at this upgrade** — it gains the designation where it carried the SKU. That is the deliberate trade: a change we schedule in a library release beats the same change
  arriving unplanned during a firmware upgrade a user did not choose the timing of.
- **Consumers reading `product_name` must move to `model` in the same release.** The Home Assistant integration builds its device-registry model from it; left unchanged, device cards go blank.

### Added

- **`SpanMidSnapshot`, and `SpanPanelSnapshot.mid`.** v1.0 publishes a Microgrid Interconnect Device and the enclosure model puts the `grid` capability on it rather than on the enclosure, so islanding state, grid state and the grid-forming entity live
  there. Previously one of its five properties was read and the device discarded. Purely additive: no flat panel publishes a MID, so nothing existing changes. Presence is `snapshot.mid is not None` rather than a sentinel field, and identity is
  `info/serial-number` rather than the Homie device id, which the proxy model warns is not stable across a proxy-to-native transition.

### Fixed

- **Adapter discovery no longer blocks the caller's event loop, and no longer imports adapters the panel will never use.** Two defects with one cause: discovery resolved the whole entry-point group up front, on the calling thread. A flat panel therefore
  imported `schema_1` — and with it the eBus SDK and jsonschema — on every connection, for a parser it would not call. Home Assistant reported the whole sequence (`listdir`, `read_text`, `open`, `scandir`) as blocking calls inside the event loop and asked
  for a bug report, with setup stalled 2.0s on a cold import cache. Enumeration and resolution are now separate: `installed_adapter_keys()` reads distribution metadata only, and an adapter is imported the first time a panel asks for that key. The async
  paths run both in a thread. Resolution stays cached per key, which is what keeps the synchronous pre-rebuild callback free of I/O. **`discover_adapters()` is replaced by `installed_adapter_keys()`**, which returns registered names rather than a registry
  of loaded classes — verifying every name would mean importing every package, which is the cost being removed. `SpanMqttClient.available_adapters` becomes `installed_adapters` for the same reason.
- **A firmware upgrade to a schema generation this install cannot parse is reported instead of raised into a background task.** The redispatch path resolves the new adapter before touching any state, so a flat-only install that meets a v1.0 panel logs
  which package is missing and keeps the parser it has. Previously `SpanPanelAdapterMissingError` escaped a fire-and-forget task as a bare traceback.
- **`dsm_state` and `current_run_config` are read from the MID instead of reading `UNKNOWN`.** Both are existing entities that had degraded on v1.0 — not because a source vanished, but because `schema_0` _derives_ them and the derivation was never ported.
  v1.0 states the answer, so the multi-signal heuristic is gone: sensed from a ready MID, falling back to the user's `shed/asserted-islanding-state` when it is not ready, then to a `power-flows/grid` heuristic when there is no MID at all, and unknown
  otherwise. A missing MID never reports on-grid — it means SPAN is not the islanding authority, not that the site is on grid, and a generator-fed island is the counterexample. `PANEL_BACKUP` versus `PANEL_OFF_GRID` becomes authoritative rather than
  guessed, because v1.0 names the forming device and its class is recoverable from the tree.
- **`grid_islandable` is mapped to `grid-forming/capable`** over the BESS's inverter children, as the disjunction — a panel does not island, its DER does, and flat expressed a property of the DER as a property of the enclosure. It returns `None` rather
  than `False` when nothing publishes it, so absence stays a gap instead of becoming a claim. No producer publishes it today, which is recorded rather than worked around.
- **EVSE identity survives the migration.** The snapshot key and `node_id` — which a consumer builds a `unique_id` and a device-registry identifier from — were the v1.0 device id on `schema_1` and firmware's node name on `schema_0`, so every charger would
  have orphaned and reappeared as a duplicate. Both are the Drive's serial now, which is what real flat firmware keys by.

## [3.0.0b2] - 08/2026

Pre-release. Releases the reshaped `SchemaAdapter` protocol that `3.0.0b1` predates, and makes the mismatch between the two detectable rather than fatal at construction.

### Added

- **Adapter contract versioning.** `SchemaAdapter` now requires an `ADAPTER_CONTRACT` integer, and discovery rejects any adapter that does not declare this package's `ADAPTER_CONTRACT_VERSION`. Member presence was never the whole contract: a Protocol
  cannot express signatures at runtime, so an adapter carrying every required name and the previous `__init__` arity passed discovery and failed much later inside the transport, as a bare `TypeError` about an argument count — the least actionable moment to
  learn that two installed packages were built against different versions of each other. Adapters must declare the value as a **literal**; one read from the installed bootstrap would agree with every bootstrap, which is the disagreement being looked for.
- **`SpanPanelAdapterIncompatibleError`**, raised when the adapter a panel needs is installed but unusable. Distinct from `SpanPanelAdapterMissingError` because the remedy inverts: missing means install something, incompatible means installing more cannot
  help. Reporting the second as the first sends someone to install a package they already have. Discovery still only _logs_ a rejection, so one unusable third-party adapter cannot take down a panel whose own adapter is fine; the error surfaces only when
  the rejected adapter turns out to be the one required.

### Fixed

- **`data-model-version` dispatch is live.** The factory hardcoded `None`, so the guard that refuses a parent/child panel was written, tested and never invoked — every panel resolved to the flat parser regardless of what it reported. The Homie schema is
  now fetched over REST **before** the broker is opened and the version drives adapter selection, which SPAN confirmed is a reliable flat-versus-parent/child signal on that endpoint. A `1.0` panel now raises `SpanPanelAdapterMissingError` naming the
  adapter to install, instead of dying inside the flat parser on a missing `energy.ebus.device.circuit/space` property.
- **A directly constructed `SpanMqttClient` dispatches too.** Building a client without `create_span_client` previously always resolved the flat adapter, so it carried the same defect the factory path had. Dispatch now happens wherever a parser is built,
  and fills in `data_model_version` / `schema_dispatch_reason` rather than leaving them reading `"not dispatched"`.

### Changed

- **BREAKING: `SchemaAdapter.__init__` takes the schema, not a panel size.** `adapter_cls(serial_number, schema)` replaces `adapter_cls(serial_number, panel_size)`. `panel_size` is read out of a block only the flat schema has, so the bootstrap had to
  understand a wire format it is meant to know nothing about, and an adapter whose schema is shaped differently had no way to say so. Each adapter now reads what its own format defines.
- **BREAKING: `SchemaAdapter.build_field_metadata()` takes no arguments.** It previously received `schema.types` — again a flat-shaped parameter on a format-agnostic protocol. The adapter holds the schema it was constructed with.
- **`V2HomieSchema.data_model_version`** carries the `dataModelVersion` field, `None` when the panel omits it. Absence is the flat signal and stays distinct from an empty string.
- **Tier 1 dispatch moved to `span_panel_api.dispatch.select_adapter_key`** from the private `factory._select_adapter_key`, so the transport can dispatch without importing the factory. `adapters.py` continues to answer "what is installed"; the new module
  answers "what does this panel need".

## [3.0.0b1] - 08/2026

Pre-release. `span-panel-api` becomes a transport and a dispatcher that contains **no parser**. Wire formats ship as separate distributions and register themselves via entry points, so support for a new panel schema arrives by installing a package rather
than by upgrading the transport. This is prototype work being proven end to end before any decision to land it on `main`.

### Removed

- **BREAKING: `span-panel-api` no longer contains a parser.** Installing it alone gives a client that connects and then raises `SpanPanelAdapterMissingError`. Flat-schema panels (firmware `r202603`–`r202627`) need **`span-panel-api-schema-0`** installed
  alongside it:

  ```console
  pip install span-panel-api span-panel-api-schema-0
  ```

- **BREAKING: `HomieLifecycle`, `HomiePropertyAccumulator` and `HomieDeviceConsumer` are no longer exported** from `span_panel_api` or `span_panel_api.mqtt`. All three are flat-schema-specific rather than Homie-convention-level: the accumulator filters
  every topic against a single device's prefix and stores `node → prop`, which drops nearly every message under the parent/child model; `HomieLifecycle`'s members are not Homie 5 `$state` values but a consumer-side progression encoding "one description
  received ⇒ ready", which is the flat readiness model. They now live in `span_panel_api_schema_0`.
- **Removed dead constants** `DEVICE_TOPIC_FMT`, `STATE_TOPIC_FMT`, `DESCRIPTION_TOPIC_FMT`, `PROPERTY_TOPIC_FMT` (unreferenced before the Phase 0 relocation) and `TYPE_PCS` (a real schema type this library does not consume).

### Added

- **`span_panel_api.adapters.resolve_adapter(key, reason)`** — the single place a missing adapter becomes a named error, used by both Tier 1 dispatch and the transport's default path.
- **`SpanPanelSchemaVersionError`**, raised when a panel reports a `data-model-version` whose schema major cannot be determined. Distinct from `SpanPanelAdapterMissingError` because the remedy differs: a missing adapter is a known schema with no installed
  parser, while this is a schema no adapter can even be named for.
- **`SpanPanelAdapterMissingError` and `SpanPanelSchemaVersionError` are now exported** from the top-level package — both are errors a user sees when their panel outruns their install, so catching them should not require reaching into a private module.
- **`SchemaAdapter.__init__` is declared on the protocol.** Construction was always part of the contract (the transport resolves an adapter class from the registry and calls it), but was previously typed only as a `Callable`, leaving the signature
  unchecked against implementations.
- **Entry-point validation.** `discover_adapters()` now verifies each loaded object is a class implementing the protocol before registering it, and skips it with a logged reason otherwise. One broken third-party adapter cannot take down a panel whose own
  adapter is fine.
- **`scripts/verify_adapterless_install.py`** and a CI step that runs it against a venv holding only the bootstrap wheel.

### Changed

- **`SpanMqttClient(adapter_factory=...)` is now optional.** When omitted, the parser is resolved through entry-point discovery at `_build_adapter()` rather than imported. Resolution is lazy by design: constructing a client must not require an adapter to
  be installed, only building a parser must.
- **Dispatch refuses an unreadable `data-model-version` instead of assuming flat.** Absence still means the flat schema — that is a real signal, since the property was introduced by the firmware that introduced parent/child. A value whose major _can_ be
  read but whose form is non-canonical (`1`, `1.0-beta`) dispatches on that major and logs the deviation. A value with no extractable major now raises. Previously all three fell through to the flat parser, which does not fail — it produces plausible but
  wrong power and energy figures.
- **Dispatch diagnostics travel through the `SpanMqttClient` constructor**, removing the window where a connected client reported a selected adapter alongside `schema_dispatch_reason='not dispatched'`.
- **Releases are now per-distribution and the tag no longer sets the version.** A tag selects which distribution to publish — `vX.Y.Z` for `span-panel-api`, `schema-N-vX.Y.Z` for an adapter — and the release fails unless the tagged version matches the one
  committed in that distribution's `pyproject.toml`. The previous workflow rewrote the root version from the tag and built only the root package, which under a two-distribution layout would have published the bootstrap with no adapter alongside it. Version
  numbers are now load-bearing between the two (the adapter declares a floor on the bootstrap), so they belong in the repository rather than being stamped at release time.

### Fixed

- **Adapter distributions ship a `py.typed` marker.** Without it a consumer's type checker refuses to read the adapter's annotations and resolves every symbol it exports as `Any`, silently erasing the strict typing at the wheel boundary. CI now fails any
  wheel built without one.
- **Protocol conformance checking no longer depends on member kind.** The required-member set is derived from every public member `SchemaAdapter` declares, not only the callable ones — a `property` or `classmethod` object is not callable, so the previous
  derivation would have quietly stopped requiring such a member the day the protocol declared one.

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
