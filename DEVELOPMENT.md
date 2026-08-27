# Development

## Prerequisites

- Python 3.14 (every manifest declares `>=3.14,<4.0`, and CI runs the suite on 3.14)
- [uv](https://docs.astral.sh/uv/) for dependency management

**The declared floor and the tested version are the same on purpose.** A `requires-python` no job runs is a claim rather than a guarantee, and the two drift apart easily, because every developer and every other workflow here is on the newest interpreter.
Keeping them identical means a green run proves the whole declared range instead of one end of it. If the floor is ever widened, the CI matrix has to widen with it in the same change.

The floor tracks the consumer. Home Assistant requires Python `>=3.12` from 2025.1, `>=3.13.2` from 2025.10 and `>=3.14.2` from 2026.3 — and the SPAN integration that consumes this library requires HA 2026.8 or newer, which puts every install that reaches
this code on 3.14. Declaring anything lower would describe a configuration nobody runs and nothing verifies.

Two older versions are worth naming as specifically ruled out, so that a future "why not support 3.10?" gets answered without re-deriving it. `tests/test_packaging.py` imports `tomllib`, stdlib only from 3.11. More seriously, Python 3.10 replaces a
`Protocol`'s `__init__` with `(*args, **kwargs)`, so `SchemaAdapter`'s declared constructor signature is not introspectable there and `test_schema_adapter_construction_signature_matches_its_implementation` has nothing to read — the check that stops two
independently-versioned wheels disagreeing about how an adapter is constructed would be inert. For a library built around exactly that seam, that is the wrong place to have a hole.

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

Some tests verify this library against things it does not contain: the eBus **specification** (the capability catalogs vendored under `packages/schema-1/spec/catalogs/`) and the two **producers** whose output is vendored here — panelbench, whose captures
are byte-copied, and the eBus emitter, which produces the reference tree. All are reached through a local checkout named by an environment variable. Locally, they **skip when the variable is unset or wrong** — not every developer keeps sibling checkouts.
**Under `CI` they fail instead**, because CI clones all three, so an absent path there means the wiring came undone rather than that the checkout is unavailable.

Copy `.env.example` to `.env` and point them at real checkouts:

```bash
EBUS_SPEC_DIR=/path/to/ebus/specification
PANELBENCH_DIR=/path/to/span/panelbench
PANEL_SIM_DIR=/path/to/distribution-enclosure-simulator
```

### A skip here is not a pass

This is worth stating plainly because it has already cost us. `test_the_vendored_captures_match_the_simulator` compares the vendored capture byte-for-byte against panelbench's `golden_tree.json` and pins `peers.panelbench.commit` in `spec_lock.json`. It is
the check that catches the vendored fixture going stale while the producer moves on — which is exactly what happened during the v1.0 capability catch-up, where the reference tree left MID `info/*`, BESS `info/{part,serial,firmware}` and PV
`info/firmware-version` unvalued long after panelbench published all of them. The drift was found by hand.

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

### The other question: has the producer moved?

A sibling checkout answers _do our bytes still match the pin?_ It cannot answer _has the producer moved past it?_, because what is on your disk knows nothing about what has been pushed or released since. `scripts/peer_drift.py` asks that one — PyPI for the
emitter's latest release, GitHub's compare API for what panelbench changed, `git ls-remote` for where a branch is — reading every pin, path and repository out of `spec_lock.json`, and needing no checkout at all:

```bash
uv run python scripts/peer_drift.py                        # all three producers
uv run python scripts/peer_drift.py --peer panelbench      # one of them
```

It runs as a pre-commit hook so that drift is caught on the commit that should have moved the pin. Non-strict there: a producer that cannot be reached reports as **UNKNOWN** and the commit still goes through, because not having asked is not the same fact
as there being nothing new, and a laptop with no network still has to be able to commit. Only a producer that has actually moved past a pin stops one.

**Local commits ask; pull requests do not.** `ci.yml` runs the same hooks with `SKIP: peer-drift`, and the hook is `stages: [pre-commit]`, so a producer that moved cannot fail somebody's unrelated pull request — the answer changes because a third party
pushed, and failing an author for that is how a check gets ignored. `peer-drift.yml` asks it daily with `--strict`, where being unable to reach a producer is a broken run rather than bad wifi, and where a red result costs a notification and nothing else.

Two comparisons are verdicts, and both are verdicts about bytes here having gone stale: the emitter's **release**, because the reference tree is a capture of one, and a **panelbench commit that touches a file we vendor**. Everything else is reported and
stays green — unreleased commits on the emitter's branch, panelbench commits that change nothing we copy, and the specification itself, which says what a device class may publish rather than what one does.

### Regenerating a vendored capture

Two scripts, one per producer, and neither is run automatically — a capture is a deliberate act.

| Artifact                                          | Producer         | Script                                      |
| ------------------------------------------------- | ---------------- | ------------------------------------------- |
| `tests/fixtures/flat_wire.json`                   | `simulator`      | `scripts/capture_flat_reference.py`         |
| `tests/reference_payloads/parent_child_tree.json` | `ebus-panel-sim` | `scripts/capture_parent_child_reference.py` |

Both run from the **producer's** environment rather than this one — each producer caps a dependency this repo installs above — and both substitute the transport rather than reassembling the emitter, because a capture taken through different wiring than a
real panel uses is a capture of the wiring. Point them at a checkout with `SIMULATOR_DIR` / `PANEL_SIM_DIR`.

`capture_parent_child_reference.py` goes one step further than documenting its producer: it reads the release it is a capture of out of `spec_lock.json` (`peers.ebus-panel-sim.version`) and **refuses to write** when the installed package disagrees. The pin
therefore has exactly one home, and re-capturing against a newer emitter is a two-place change made together — that peer block, and the provenance section of `tests/reference_payloads/README.md`. That is what stops the bytes and the claim about them
drifting apart, and the drift is not hypothetical: it is how a producer defect in `$settable` on a locked relay reached about thirty test files across two repositories with no conformance gate objecting.

Its input is committed too, as `scripts/reference_panel.yaml`, pinned as `peers.ebus-panel-sim.manifest` — a capture whose input is not in the tree is the same class of problem as one whose producer is not recorded. That manifest is a synthetic `example-*`
panel that mirrors the emitter's own `examples/forty_tab_minimal.yaml` key for key and marks its two deliberate divergences at the head of the file: spec-legal shed priorities in place of a value the emitter degrades to `UNKNOWN`
(electrification-bus/distribution-enclosure-simulator#51), and the identity properties a real panel publishes. Read that file before assuming ours has drifted from theirs.

Expect a recapture to move every `$description`'s `version`, which is minted from the wall clock. A diff confined to those fourteen lines means the producer did not move.

### The producer is the specification, executable

`ebus-panel-sim` is not a third-party imitation to be second-guessed. It is published by electrification-bus, the organisation that writes the eBus specification, and is conformed against live panel output — the designated checkpoint for whether a consumer
reads a conforming tree correctly. Its `.ebus-spec.json` names the specification commit it implements, `spec_lock.json` records ours, and `test_the_emitters_pin_matches_ours` compares them, so a disagreement between this parser and the reference capture is
a disagreement about one document rather than about two.

So the lesson from the stale capture is not "depend on it less". It is that a dependency nobody can see cannot be maintained: the capture was taken once, nothing recorded what made it, and three emitter releases went by while this repository asserted a
producer defect as fact. Both producers are now tracked the same way — pinned in `spec_lock.json`, cloned by the same composite action, and watched by `peer-drift.yml`.

### Two questions, two workflows

The peer checks answer a question whose shape depends on which producer revision you point them at, and the two answers belong in different places.

- **`.github/workflows/ci.yml`** clones both peers at the commits `spec_lock.json` pins, via the `.github/actions/peer-checkouts` composite action, and runs the whole suite against them. The question is _do our vendored bytes match the commit we claim they
  came from?_ — deterministic, answerable on any commit, and fair to block a merge on. It catches an accidental local edit to a vendored file.
- **`.github/workflows/peer-drift.yml`** runs on a schedule, never on a pull request. The question is _has the producer moved past the pin?_ Its answer changes because someone else pushed, so it must not fail an author's unrelated change. It runs
  `scripts/peer_drift.py --strict`, which asks the producers directly and needs no clone; the clones it still takes are there to name the commits, which is the one thing that script cannot do. It goes red only on a verdict — a new emitter release, or a
  panelbench commit touching a file we vendor — so panelbench advancing with a change we do not copy stays green.

The second question is also asked on every local commit, by the `peer-drift` pre-commit hook running the same script non-strict — and skipped in `ci.yml`, so it stays off pull requests for the same reason the workflow does. The workflow is the backstop
rather than the check: a producer that moved is best found by the commit that should have moved the pin with it.

All three repositories are public, so no checkout needs a token. If either ever goes private, the checkout step in the composite action is what starts failing, and the fix is a read-scoped PAT in its `token:` — the pin is not involved.

The commits come out of `packages/schema-1/src/span_panel_api_schema_1/spec_lock.json` at run time rather than being written into the workflows, so the pin keeps exactly one home. A workflow that restated a commit would agree with the lock file right up
until the day someone re-vendored and updated only one of them.

### When the peer check fails

A failure means the vendored capture and panelbench have diverged. That is information, not an obstacle — decide which side is right:

- **Panelbench moved and we should follow**: re-capture the fixture, and update `peers.panelbench.commit` in `spec_lock.json` to the panelbench commit you captured from. Both, together — a capture without a commit bump records where the bytes came from as
  a guess.
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

### Writing to an adopted property

`AdoptedProperty.set_topic` is populated **only** for a settable property on a device `is_modelled` rejects. That scoping is the authorisation rather than a check somebody has to remember: `SpanMqttClient.set_adopted_property` resolves the property against
the current snapshot's `adopted_devices` and publishes to the topic that property carries, and accepts no topic from its caller.

The alternative — a `set_property_topic(device, node, property)` member on `SchemaAdapter` — was rejected twice over:

- It would put every curated control one argument away, and two of them do real work on the way out. `dominant_power_source_payload` translates `GRID` into the `ON_GRID` the v1.0 islanding assertion accepts, and `evse_charge_limit_payload` **refuses** a
  value above the commissioned ceiling because publishing past it is the one write here with a physical consequence.
- `_derive_required_members` derives the required set from the protocol, so the member would be required of every adapter package. An installation carrying an older adapter wheel would fail at **discovery** — the whole integration, not one feature.

No translation and no bounds check on the way out. Both exist on curated controls because this library knows what those properties mean; it knows nothing about an adopted one beyond its declaration.

### The proxy link is carried, not acted on

`AdoptedDevice.parent` holds the device id the device declares as its parent, and `AdoptedDevice.proxied` says whether that parent is a peer rather than the tree root. Neither changes topology: an adopted device is registered under the enclosure like every
other sub-device.

They exist because a _proxied_ unmodelled device is a real shape and we would otherwise flatten it away without noticing. The reference tree already contains one — `bess-mid` declares `parent: bess`, which is the `{proxier-id}-{proxied-id}` naming of the
specification's `devices/proxy.md`. A vendor gateway proxying its own sub-devices arrives the same way, and the parent link is the only structural information about how they relate.

`proxied` is computed here rather than left to the consumer because `root` is in hand here and is deliberately not carried onto the record: device ids are opaque, so a consumer holding one device cannot tell the enclosure's id from a sibling's.

**Why the nesting is not built.** [python-sdk#49](https://github.com/electrification-bus/python-sdk/issues/49#issuecomment-5359203067) settled two things that bear on it. Proxied ids differ by design — the prefix is the proxier's own id, so several
enclosures on a shared broker each proxying the same physical device produce different ids on purpose, and consumers are told to correlate by `info/serial-number` and never by device id. And `ebus-sdk` 0.21.0 shipped `DeviceSpec` and `DeviceTreeBuilder`
([python-sdk#57](https://github.com/electrification-bus/python-sdk/issues/57)), with the maintainer's stated next step being to reconcile the existing graph builder against it rather than land both.

So the tree model is under active reconciliation upstream. Carrying the two fields costs nothing and captures the evidence; building nesting semantics against a shape being reshaped this week would be building against a moving target.

That same comment strengthens two choices already made here. Its deferral mechanism — `device_id` accepts a callable, `None` defers the device, and `resolve_deferred()` resumes when the identifier arrives — is the producer-side form of resolving identity
_before_ a device exists, which is what a consumer's freeze-at-first-sighting does from the other end. And "there is deliberately no existence predicate … expressing it by not calling `add()` is right" is the rule `TreeRoles` and the capability gates
already follow: presence in the tree is the signal, and there is no flag to consult.

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
4. Ensure all checks pass across every distribution, not just the bootstrap:

   ```bash
   uv run pytest
   uv run mypy src packages
   uv run ruff check .
   ```

   `uv run pre-commit run --all-files` is what CI actually runs, and it covers these plus the markdown, security and dead-code hooks.

5. Submit a pull request
