# Development

## Prerequisites

- Python 3.10+ (CI tests 3.13 and 3.14)
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
git clone https://github.com/SpanPanel/span-panel-api.git
cd span-panel-api
uv sync
```

## Testing

```bash
# Full test suite
uv run pytest

# Verbose with coverage
uv run pytest tests/ -v --cov=src/span_panel_api --cov-report=term-missing

# Check coverage meets threshold (85%)
python scripts/coverage.py --check --threshold 85

# Full coverage report
python scripts/coverage.py --full
```

## Conformance against the specification and the producer

Some tests verify this library against two things it does not contain: the eBus **specification** (the capability catalogs vendored under `packages/schema-1/spec/catalogs/`) and **panelbench**, the producer whose captures are vendored as reference
payloads. Both are reached through a local checkout named by an environment variable. Locally, both **skip when the variable is unset or wrong** — not every developer keeps sibling checkouts. **Under `CI` they fail instead**, because CI clones both, so an
absent path there means the wiring came undone rather than that the checkout is unavailable.

Copy `.env.example` to `.env` and point them at real checkouts:

```bash
EBUS_SPEC_DIR=/path/to/ebus/specification
PANELBENCH_DIR=/path/to/span/panelbench
```

### A skip here is not a pass

This is worth stating plainly because it has already cost us. `test_the_vendored_captures_match_the_simulator` compares the vendored capture byte-for-byte against panelbench's `golden_tree.json` and pins `peer.commit` in `spec_lock.json`. It is the check
that catches the vendored fixture going stale while the producer moves on — which is exactly what happened during the v1.0 capability catch-up, where the reference tree left MID `info/*`, BESS `info/{part,serial,firmware}` and PV `info/firmware-version`
unvalued long after panelbench published all of them. The drift was found by hand.

The test did not fail, because it never ran: `PANELBENCH_DIR` named a directory that did not exist, so it skipped, and a skip renders in a summary line exactly like a pass.

So: **if your run reports skips in `test_schema_one_conformance.py`, the provenance checks did not happen.** Run with `-rs` to see which and why:

```bash
uv run pytest tests/ -q -rs
```

A correctly configured run has no skips in that file. The only skips you should expect are in `test_live_flat_differential.py`, which needs a live panel capture that is deliberately gitignored (see `scripts/capture_live_flat.py`); those are flat-firmware
differentials and are not part of schema_1 work.

It cost us a second time on 2026-08-20, in the other vendored capture. `tests/fixtures/flat_wire.json` was taken from the flat simulator at v1.0.15 and described as frozen; 1.0.16 then made an EVSE's node id its drive serial and forced that serial
lower-case, which is the flat half of a change panelbench made on the v1.0 side the same week. Nothing compared the capture to its source, so for nine days the two vendored captures named the same charger differently. `scripts/capture_flat_reference.py`
now records the simulator commit its output came from, for the same reason `spec_lock.json` records the other two.

### Two questions, two workflows

The peer checks answer a question whose shape depends on which panelbench you point them at, and the two answers belong in different places.

- **`.github/workflows/ci.yml`** clones both peers at the commits `spec_lock.json` pins, via the `.github/actions/peer-checkouts` composite action, and runs the whole suite against them. The question is _do our vendored bytes match the commit we claim they
  came from?_ — deterministic, answerable on any commit, and fair to block a merge on. It catches an accidental local edit to a vendored file.
- **`.github/workflows/peer-drift.yml`** runs on a schedule, never on a pull request, and clones panelbench at `peer.ref` — the branch the producer develops on. The question is _has the producer moved past the pin?_ Its answer changes because someone else
  pushed, so it must not fail an author's unrelated change. It reports the distance from the pin in the job summary either way, and goes red only when the comparison itself fails, so panelbench advancing with a change we do not vendor stays green.

Both repositories are public, so neither checkout needs a token. If either ever goes private, the checkout step in the composite action is what starts failing, and the fix is a read-scoped PAT in its `token:` — the pin is not involved.

The commits come out of `packages/schema-1/src/span_panel_api_schema_1/spec_lock.json` at run time rather than being written into the workflows, so the pin keeps exactly one home. A workflow that restated a commit would agree with the lock file right up
until the day someone re-vendored and updated only one of them.

### When the peer check fails

A failure means the vendored capture and panelbench have diverged. That is information, not an obstacle — decide which side is right:

- **Panelbench moved and we should follow**: re-capture the fixture, and update `peer.commit` in `spec_lock.json` to the panelbench commit you captured from. Both, together — a capture without a commit bump records where the bytes came from as a guess.
- **We diverged deliberately** (the reference tree is trimmed and renamed to synthetic `example-*` identifiers, so it is not a verbatim copy): the comparison covers the artifacts that _are_ meant to match. Do not loosen it to accommodate a local edit.

### Catalogs are pinned by commit, not version

`spec_lock.json` records `synced_commit`, not a specification version. That is deliberate: the 2026-07-31 spec changelog changed circuit sign-frame semantics **in place** with no version bump and stated no re-pin was required. A version pin would not have
noticed.

### The acknowledged-divergence register

`test_schema_one_conformance.py` asks whether the names this adapter reads exist in the catalogs. `test_catalog_divergence.py` asks the next question, and it is the one that corrupts readings when the answer is wrong: **does the `unit` and `datatype` a
producer declares for a property agree with the catalog's definition of it?** Agreement is silence. Disagreement is a finding, and it is never resolved silently in either direction — do not change a wire reader to agree with the catalog, and do not assume
the catalog is right. Both have been wrong.

Four producers are surveyed: the two vendored simulator captures, the reference parent/child tree, and the flat schema document captured from a live panel. The flat document has no capability nodes, so its properties reach the catalogued vocabulary through
the snapshot field path both adapters' metadata tables name — and only where the two spell the property identically, so a pre-catalog **rename** (`dipole` for `breaker/poles`) is left out rather than reported as a divergence.

When a finding is real, record it in `_REGISTER` with what the wire says, what the catalog says, which producers show it, a reason, and a date. That is a human saying "SPAN ships this and we compensate", and it fails in both directions like every other
baseline here: a new divergence fails until somebody records it, and a **recorded divergence that has disappeared fails until its line is removed**. The second direction is what makes the register self-cleaning when a firmware or a catalog is fixed, and it
is why the register is not a suppression list.

Two rules keep it from producing false findings:

- **An abstract unit is a dimension, not a unit.** The catalog gives `soc/soe` and `info/nameplate-capacity` as `unit: "energy"` and requires the publisher to substitute a real one — a BESS in kWh, a water heater in Wh. A member of the family is silent;
  echoing the token back is not. Membership is enumerated in `catalog.py`'s `UNIT_FAMILIES`, and a catalog unit token that is neither a known family nor a known concrete unit fails until a human classifies it.
- **An absence is terminal.** A property no catalog defines — the EVSE's `config` node, which is not an eBus capability at all — is reported once as absent and never as a unit or datatype mismatch against a definition that does not exist.

### What a panel declares that this library reads nothing from

`schema_1`'s `build_field_metadata` returns a second kind of row alongside the curated ones: for every property a device's `$description` declares that the adapter addresses nowhere, a row under the `discovered.` namespace carrying the declared `datatype`,
the declared `unit`, and whether a value has been published for it. Never the value — these rows exist to be forwarded in a consumer's diagnostics, which leave the machine they were generated on.

It is additive by construction. There is no new `SchemaAdapter` member and no `ADAPTER_CONTRACT` bump: `_derive_required_members` makes every public protocol member required of every adapter distribution, so adding one would reject every built wheel. An
adapter that emits no such rows is indistinguishable from one built before the namespace existed.

The flat adapter emits none, deliberately. Its metadata comes from the REST `types` document, which the migration guide describes as the superset across all hardware rather than what one panel has — so "declared and unaddressed" there would describe the
schema document and could not answer the question this exists to ask.

**The report is only as good as the enumerations behind it, so they are proved rather than trusted.** The adapter decides "addressed" from four tables: `_PROPERTY_FIELD_MAP`, the lugs direction tables, the charge-limit resolution, and
`_CONSUMED_WITHOUT_A_ROW` — the properties the snapshot mapper reads that carry no metadata row because they are identity, topology, or a qualifier rather than a reading. A stale entry in the last of those fails _silently_, by keeping a property out of the
report, and a smaller report looks exactly like a panel with nothing new on it.

`test_schema_one_discovery.py` closes that by experiment: it republishes every property the reference tree declares with a legal different value, rebuilds the snapshot through the real mapper, and asserts both directions — every claimed-read property moves
a snapshot field, and every reported property moves none. `_CONSUMED_OFF_SNAPSHOT` holds the three declarations consumed by a route no snapshot field can show (tier-1 dispatch, the shadowed islanding tier, the unreached feedthrough branch); each names the
code that reads it, and each fails the day its property does move a field.

## Devices this library models nothing for

`TreeRoles` sorts a v1.0 tree into the roles a snapshot needs. Anything matching none of them used to fall off the end silently — a panel publishing a device type nobody modelled produced no field, no metadata row and no sign it was there. The eBus schema
is explicitly vendor-extensible, so that is an expected arrival rather than a hypothetical.

`span_panel_api_schema_1.adoption` builds an `AdoptedDevice` for each such child, and `build_snapshot` puts them on `SpanPanelSnapshot.adopted_devices`.

### The two rules that keep it from being a firehose

**The unit is a device, never a property.** A new property on a device this adapter already models is a curation task with a short turnaround, and surfacing it automatically would spend a consumer's entity identity permanently on a shape a human would
likely have chosen differently. An unmodelled _type_ is the opposite case: no curation is coming, so the alternative is silence.

**Extra instances of a modelled type are not adopted.** `TreeRoles` keeps the first BESS and ignores the rest, which is a real gap — but adopting the extra one would stand a machine-named record beside a curated one describing the same hardware. The gap
stays visible as a gap.

`MODELLED_TYPES` states the modelled set once, and `tests/test_adoption.py` parametrises over it through `build_snapshot` rather than through the classifier. That is what stops the tuple drifting from the builder: a type dropped from `TreeRoles` while left
in the tuple would make its devices invisible to both paths at once.

### `info` and `connection` resolve away from readings

`ADOPTION_IDENTITY_NODE` (`info`) becomes the device's card fields; `ADOPTION_TOPOLOGY_NODE` (`connection`) is dropped, because it is a device-tree question rather than a reading.

Keyed on the **node**, not on property names. The catalogs carry no marker for "this string is a device reference", so a name list is the only alternative — and it goes stale silently: `ebus-sdk`'s own `topology.py` covers `feeds-device-id` and
`fed-by-device-id` and omits `grid-forming-entity`, which lives on the `grid` capability. A node is what the vocabulary defines.

### `AdoptedProperty` carries the value; `DiscoveredMetadata` must not

The two answer opposite questions and are separate types so that conflating them is a type error rather than a leak:

| Type                 | Question                                     | Destination                       | Carries a value |
| -------------------- | -------------------------------------------- | --------------------------------- | --------------- |
| `DiscoveredMetadata` | "we model this device and read nothing here" | consumer diagnostics, which leave | **no**          |
| `AdoptedProperty`    | "nothing here models this device at all"     | an entity on the same machine     | **yes**         |

`AdoptedProperty` also carries the declared `format` and `settable` flag, which together are the value domain a consumer needs to build a control rather than a reading.

### Additive by construction

`adopted_devices` defaults to `()`. schema_0 never populates it — flat has no device tree to find an unmodelled device in, and panels upgrade to v1.0 and stay there, so adoption operates in the schema that is the terminus.

A defaulted snapshot field rather than a `SchemaAdapter` member, deliberately: the protocol derives its required members from itself, so a member there would be required of every adapter package and would invalidate built adapter wheels.
`ADAPTER_CONTRACT_VERSION` does not move.

## Linting and Formatting

Pre-commit hooks run automatically on commit. To run all hooks manually:

```bash
uv run pre-commit run --all-files
```

Individual tools:

```bash
# Ruff (lint + format)
uv run ruff check src/
uv run ruff format src/

# Type checking
uv run mypy src/

# Security scan
uv run bandit -r src/

# Dead code detection
uv run vulture src/span_panel_api/ --min-confidence 80
```

Or use the combined format script:

```bash
./scripts/format.sh
```

## Git Hooks

To install pre-commit hooks:

```bash
./setup-hooks.sh
```

This installs dependencies (if needed) and configures git pre-commit hooks.

## Workspace layout

This repository is a uv workspace publishing more than one distribution: the bootstrap (`span-panel-api`, at the root) and one parser package per panel schema (`packages/schema-N/`). `uv sync` installs the workspace, so the test suite runs against every
distribution together.

To work with the packages individually:

```bash
# Install the workspace including every member
uv sync --all-packages

# Build every distribution
uv build --all-packages

# Build just one
uv build --package span-panel-api-schema-0
```

## Releasing

See [RELEASE.md](RELEASE.md) — each distribution versions and publishes independently, and the tag name selects which one is published.

## Contributing

1. Fork and clone the repository
2. Install dev dependencies: `uv sync`
3. Make changes and add tests
4. Ensure all checks pass: `uv run pytest && uv run mypy src/ && uv run ruff check src/`
5. Submit a pull request
