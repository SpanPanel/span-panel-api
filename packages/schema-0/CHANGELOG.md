# Changelog

All notable changes to `span-panel-api-schema-0` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Note that this package versions on the **library-API axis**, not the wire-format axis. The wire format it parses is fixed — the flat single-device schema, SPAN firmware `r202603` through `r202627` — and is identified by `SUPPORTS_DATA_MODEL_VERSIONS`
rather than by this version number. A release here means this parser changed, never that the panel did.

## [1.0.0b3] - 08/2026

Pre-release. Requires `span-panel-api` 3.0.0b2 or newer — unchanged, because nothing added here reaches for anything newer.

### Changed

- **BREAKING — DER identity is translated into v1.0's vocabulary rather than mirroring flat's names.** `model` is the human designation and `part_number` the SKU, on `battery`, `evse` and `pv` alike; `product_name` is retired on all three. Flat is the
  irregular side: it puts the SKU in `bess/model` and in `evse/part-number` — the same concept under two names — and gives PV neither. `schema_1` used to cross over to preserve each entity's displayed meaning, which worked and permanently encoded flat's
  irregularity in the snapshot. This adapter now normalises instead: `bess/model` → `part_number`, `bess/product-name` → `model`. **`battery.model` changes value for existing flat users at this upgrade.** Measured: every EVSE identity field now reads
  identically on both adapters, so for that device class identity stops being a migration delta at all.

### Added

- **`dominant_power_source_payload`.** Flat already speaks this vocabulary, so the value passes through — the method exists because `schema_1` must translate, and a caller should not have to know which schema is underneath. Validated rather than passed
  blindly: an unrecognised value returns `None` and the transport refuses the command, matching `schema_1` rather than putting a string outside the enum on the wire.

## [1.0.0b2] - 08/2026

Pre-release. Follows the reshaped `SchemaAdapter` protocol released in `span-panel-api` 3.0.0b2.

### Added

- **`ADAPTER_CONTRACT = 1`**, declaring which version of the bootstrap-to-adapter contract this parser was built against. Declared as a literal rather than imported from `span_panel_api.protocol`: a value read from the installed bootstrap would agree with
  every bootstrap, which is exactly the disagreement the check exists to find.

### Changed

- **BREAKING: `SchemaZeroAdapter(serial_number, schema)`** replaces `SchemaZeroAdapter(serial_number, panel_size)`, following the protocol change in `span-panel-api`. Panel size is now derived here, by reading the circuit `space` format out of the flat
  schema's `types` block — knowledge that belongs to this package rather than to the transport, which was previously doing it on every adapter's behalf.
- **`build_field_metadata()` takes no arguments**, reading the schema this adapter was constructed with.
- **The `span-panel-api` floor is now `>=3.0.0b2`.** `1.0.0b1` declared `>=3.0.0b1`, which admitted a bootstrap that constructs adapters with `panel_size` — a pairing that could not work. Installing that combination now fails by name at discovery rather
  than on argument count inside the transport, but the floor is what stops a resolver reaching it at all.

## [1.0.0b1] - 08/2026

Pre-release. First release as a standalone distribution.

### Added

- **The flat-schema parser, extracted from `span-panel-api` 2.6.4.** Relocated verbatim from `span_panel_api._impl.schema_0` to `span_panel_api_schema_0`; only import statements changed. Registers itself as `schema_0` under the
  `span_panel_api.schema_adapters` entry-point group, which is the only way `span-panel-api` reaches it — the bootstrap never imports this package.
- **`SCHEMA_ANCHOR`** (`sha256:d347556a07d98f40`, firmware `spanos2/r202603/05`) — the schema revision every hardcoded fact in this package was read from, with `SCHEMA_ANCHOR_FIELD` naming the field it comes from (`typesSchemaHash`). The field is
  per-adapter: parent/child firmware renames it to `deviceClassesSchemaHash` along with the block it covers, so a future `schema_1` declares its own rather than inheriting one that does not exist on its firmware.
- **Provenance tests** asserting that all 64 hardcoded `(node_type, property_id)` pairs still resolve against the captured schema, that `HOMIE_DOMAIN` / `HOMIE_VERSION` still match it, and that the two lugs subtypes real firmware publishes remain absent
  from the schema _and_ present in the metadata alias table. This is the only signal that catches schema drift before release; every other symptom reaches production as a silent absence.
- **A `py.typed` marker**, so consumers type-check against this package's real annotations rather than resolving everything it exports as `Any`.

### Known deviations from the published schema

- **Circuit `active-power` is treated as watts, though the schema declares kilowatts.** Real panels publish watts; this was established against live hardware and the 1000× correction was removed accordingly. A test asserts the schema still says `kW`, so
  the day SPAN corrects it we find out rather than discovering it as a factor-of-1000 error.
- **`energy.ebus.device.lugs.upstream` / `.downstream` are parsed but undeclared.** Firmware publishes these node types in `$description`; the schema declares only the base `energy.ebus.device.lugs`. Property metadata for them resolves through an alias to
  the base type.

### Retirement

SPAN retires the flat schema in the same firmware release that introduces the parent/child model (`r202633`; fleet rollout projected, not committed, for the first two weeks of September 2026). This package stops being published once the fleet has moved.
Published versions remain on PyPI for anyone still running older firmware.
