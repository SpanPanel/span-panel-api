# Changelog

All notable changes to `span-panel-api-schema-0` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Note that this package versions on the **library-API axis**, not the wire-format axis. The wire format it parses is fixed — the flat single-device schema, SPAN firmware `r202603` through `r202627` — and is identified by `SUPPORTS_DATA_MODEL_VERSIONS`
rather than by this version number. A release here means this parser changed, never that the panel did.

Pre-releases are not listed separately. A beta is a step towards the next public version, so its changes are folded into that version's entry as they land and are described against the last public release, never against the beta before it.

## [1.1.0]

Requires `span-panel-api` **3.1.0 or newer**, and the two must be upgraded together in both directions: this wheel is rejected at discovery by a 3.0.x bootstrap, and a 1.0.0 wheel is rejected by 3.1.0.

### Changed

- **BREAKING: `set_circuit_relay_topic`, `set_circuit_priority_topic`, `set_dominant_power_source_topic` and `set_evse_charge_limit_topic` become `set_*_target`, returning a `ControlTarget` instead of a topic string.** 3.1.0 verifies a control write by
  matching the topic it published to against the property that reports the change, and only an adapter knows both — flat spells this control `(serial, circuit_id, "relay")` where parent/child spells it `(circuit_id, "switch", "relay")`, so the transport
  cannot derive one from the other without learning two topic grammars. `ControlTarget` returns the topic and that triple from a single call, in the same spelling `_on_property_changed` reports under, so the two cannot disagree.

  Renamed rather than re-typed under the old name so that the mismatch is caught at discovery, where the remedy can be named, instead of surfacing as an `AttributeError` on a `str` deep inside a setter. `ADAPTER_CONTRACT` stays **1** — the contract's
  member list changed, which discovery already checks by name.

- **`set_circuit_relay_target` and `set_circuit_priority_target` return `ControlTarget | None`, and refuse a circuit the panel declares non-commandable.** Both formatted a topic from a node id and consulted nothing, so a command aimed at an always-on relay
  or a never-backup priority was published — while `consumer.py` was already reading `always-on` into `is_user_controllable` and `never-backup` into `is_never_backup` for the snapshot. `HomieDeviceConsumer` gains `relay_is_settable` and
  `priority_is_settable`, so the command path and the snapshot make one reading rather than two.

  Absence reads as permission on both flags, which is what they mean: each marks the exception, and defaulting to locked would refuse every circuit on a panel that omits them. Flat publishes no `$settable`, so there is one signal here where the
  parent/child adapter reads two — a statement about the schema rather than a weaker rule.

  A locked relay keeps a settable priority: always-on is not never-backup on either schema.

  **And both refuse a circuit id this panel never published.** Absence-reads-as-permission is right for the two flags and wrong for the node itself: `get_prop` answers `""` for an id nothing published, `""` parses as `false`, and `not false` is permission
  — so an unknown id produced a well-formed topic aimed at nothing, including for the synthetic `unmapped_tab_*` keys the snapshot invents. `schema_1` already refused an id its tree did not carry; two adapters answering the same question differently was
  the defect, and the question is answered here from the node type in `$description`.

- **`HomieDeviceConsumer.is_circuit_node` is public**, and `SchemaZeroAdapter` gains `has_circuit`, which delegates to it. `SchemaAdapter` declares `has_circuit` in 3.1.0 so a transport can tell "no such circuit" apart from "this circuit's control is
  locked" when it reports a refusal. One reading behind both answers: what counts as a circuit here decides the refusal and its stated reason together, so the two cannot drift.

## [1.0.0]

First release as a standalone distribution. Requires `span-panel-api` 3.0.0 or newer.

### Added

- **The flat-schema parser, extracted from `span-panel-api` 2.6.4.** Relocated from `span_panel_api._impl.schema_0` to `span_panel_api_schema_0`, and registered as `schema_0` under the `span_panel_api.schema_adapters` entry-point group, which is the only
  way `span-panel-api` reaches it — the bootstrap never imports this package. Installing it is what makes flat-schema panels work; `span-panel-api` alone connects and then raises `SpanPanelAdapterMissingError` naming the adapter it could not find.
- **`HomieLifecycle`, `HomiePropertyAccumulator` and `HomieDeviceConsumer` live here now.** All three left the bootstrap because they are flat-schema-specific rather than Homie-convention-level: the accumulator filters every topic against a single device's
  prefix and stores `node → prop`, and `HomieLifecycle`'s members are not Homie 5 `$state` values but a consumer-side progression encoding "one description received ⇒ ready".
- **`SCHEMA_ANCHOR`** (`sha256:d347556a07d98f40`, firmware `spanos2/r202603/05`) — the schema revision every hardcoded fact in this package was read from, with `SCHEMA_ANCHOR_FIELD` naming the field it comes from (`typesSchemaHash`). The field is
  per-adapter: parent/child firmware renames it to `deviceClassesSchemaHash` along with the block it covers, so `schema_1` declares its own rather than inheriting one that does not exist on its firmware.
- **`ADAPTER_CONTRACT = 1`**, declaring which version of the bootstrap-to-adapter contract this parser was built against. Declared as a literal rather than imported from `span_panel_api.protocol`: a value read from the installed bootstrap would agree with
  every bootstrap, which is exactly the disagreement the check exists to find.
- **`dominant_power_source_payload`.** Flat already speaks this vocabulary, so the value passes through — the method exists because `schema_1` must translate, and a caller should not have to know which schema is underneath. Validated rather than passed
  blindly: an unrecognised value returns `None` and the transport refuses the command, matching `schema_1` rather than putting a string outside the enum on the wire.
- **`set_evse_charge_limit_topic` and `evse_charge_limit_payload`.** Both are required of every adapter, because `_derive_required_members` makes each public protocol member mandatory of every adapter wheel — an adapter without them is rejected at
  discovery no matter which panel it would have parsed. Flat firmware publishes no charge-limit surface, so this distribution answers for the absence rather than for a topic; the point is that answering is not optional.
- **`adopted_devices` reports empty.** Adoption is a parent/child idea: a flat panel is one device with no unmodelled children to adopt, so the honest answer is a stable empty tuple rather than an unimplemented member. `set_adopted_property` therefore
  raises on a flat panel for the same reason it raises for a device that does not exist, which is what makes the snapshot lookup an authorization rather than a lookup.
- **Panel size is derived here**, by reading the circuit `space` format out of the flat schema's `types` block — knowledge that belongs to this package rather than to the transport, which previously did it on every adapter's behalf.
- **Provenance tests** asserting that all 64 hardcoded `(node_type, property_id)` pairs still resolve against the captured schema, that `HOMIE_DOMAIN` / `HOMIE_VERSION` still match it, and that the two lugs subtypes real firmware publishes remain absent
  from the schema _and_ present in the metadata alias table. This is the only signal that catches schema drift before release; every other symptom reaches production as a silent absence.
- **A `py.typed` marker**, so consumers type-check against this package's real annotations rather than resolving everything it exports as `Any`.

### Changed

- **BREAKING — DER identity is translated into the parent/child vocabulary rather than mirroring flat's names.** `model` is the human designation and `part_number` the SKU, on `battery`, `evse` and `pv` alike; `product_name` is retired on all three. Flat
  is the irregular side: it puts the SKU in `bess/model` and in `evse/part-number` — the same concept under two names — and gives PV neither. Mirroring that would have permanently encoded flat's irregularity in the snapshot, so this adapter normalises
  instead: `bess/model` → `part_number`, `bess/product-name` → `model`. **`battery.model` changes value for existing flat users at this upgrade.** Measured: every EVSE identity field now reads identically on both adapters, so for that device class identity
  stops being a migration delta at all.

### Known deviations from the published schema

- **Circuit `active-power` is treated as watts, though the schema declares kilowatts.** Real panels publish watts; this was established against live hardware and the 1000× correction was removed accordingly. A test asserts the schema still says `kW`, so
  the day SPAN corrects it we find out rather than discovering it as a factor-of-1000 error.
- **`energy.ebus.device.lugs.upstream` / `.downstream` are parsed but undeclared.** Firmware publishes these node types in `$description`; the schema declares only the base `energy.ebus.device.lugs`. Property metadata for them resolves through an alias to
  the base type.

### Retirement

SPAN retires the flat schema in the same firmware release that introduces the parent/child model (`r202633`; fleet rollout projected, not committed, for the first two weeks of September 2026). This package stops being published once the fleet has moved.
Published versions remain on PyPI for anyone still running older firmware.
