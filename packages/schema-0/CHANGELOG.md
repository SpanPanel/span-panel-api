# Changelog

All notable changes to `span-panel-api-schema-0` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Note that this package versions on the **library-API axis**, not the wire-format axis. The wire format it parses is fixed — the flat single-device schema, SPAN firmware `r202603` through `r202627` — and is identified by `SUPPORTS_DATA_MODEL_VERSIONS`
rather than by this version number. A release here means this parser changed, never that the panel did.

## [1.0.0b1] - 08/2026

Pre-release. First release as a standalone distribution.

### Added

- **The flat-schema parser, extracted from `span-panel-api` 2.6.4.** Relocated verbatim from `span_panel_api._impl.schema_0` to `span_panel_api_schema_0`; only import statements changed. Registers itself as `schema_0` under the
  `span_panel_api.schema_adapters` entry-point group, which is the only way `span-panel-api` reaches it — the bootstrap never imports this package.
- **`SCHEMA_ANCHOR`** (`sha256:d347556a07d98f40`, firmware `spanos2/r202603/05`) — the schema revision every hardcoded fact in this package was read from, with `SCHEMA_ANCHOR_FIELD` naming the field it comes from (`typesSchemaHash`). The field is
  per-adapter: parent/child firmware renames it to `deviceClassesSchemaHash` along with the block it covers, so a future `schema_1` declares its own rather than inheriting one that does not exist on its firmware.
- **Provenance tests** asserting that all 64 hardcoded `(node_type, property_id)` pairs still resolve against the captured schema, that `HOMIE_DOMAIN` / `HOMIE_VERSION` still match it, and that the two lugs subtypes real firmware publishes remain absent
  from the schema _and_ present in the metadata alias table. This is the only signal that catches schema drift before release; every other symptom reaches production as a silent absence.

### Known deviations from the published schema

- **Circuit `active-power` is treated as watts, though the schema declares kilowatts.** Real panels publish watts; this was established against live hardware and the 1000× correction was removed accordingly. A test asserts the schema still says `kW`, so
  the day SPAN corrects it we find out rather than discovering it as a factor-of-1000 error.
- **`energy.ebus.device.lugs.upstream` / `.downstream` are parsed but undeclared.** Firmware publishes these node types in `$description`; the schema declares only the base `energy.ebus.device.lugs`. Property metadata for them resolves through an alias to
  the base type.

### Retirement

SPAN retires the flat schema in the same firmware release that introduces the parent/child model (`r202633`; fleet rollout projected, not committed, for the first two weeks of September 2026). This package stops being published once the fleet has moved.
Published versions remain on PyPI for anyone still running older firmware.
