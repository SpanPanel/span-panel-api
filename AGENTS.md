# Working on span-panel-api

Guidance for anyone changing this repository, person or coding agent. It lists what has to be kept up when the code changes; the procedures themselves live in [DEVELOPMENT.md](DEVELOPMENT.md) (setup, tests, captures, conformance) and
[RELEASE.md](RELEASE.md) (versions, floors, tags, pre-releases). Read those before changing a capture, a dependency floor or a version.

## The rule every field follows

A value the panel did not publish is `None` (or unknown), never a constant. A `0.0`, `False`, `""` or empty collection in a snapshot field is a reading the panel published, or an answer the library can derive from what the panel declared, and the docstring
of the field says which. Where a field is not yet optional, the adapter fidelity harness records its absence value as an explicit, reasoned exception rather than letting it pass unnoticed.

## When you add, rename or change a snapshot field or an adapter read

Every read the schema-1 adapter makes is accounted for in more than one place, and each place fails the suite if it is left behind:

- **Field metadata and discovery** (`packages/schema-1/src/span_panel_api_schema_1/field_metadata.py`). A reading with a unit gets a `_PROPERTY_FIELD_MAP` row, or a row in a lugs table; a property read into the snapshot without one goes in
  `_CONSUMED_WITHOUT_A_ROW`. Otherwise it is reported as discovered and becomes an extension reading. `tests/test_schema_one_discovery.py` proves each entry by perturbing it against the reference tree; an entry the tree does not declare goes in
  `_ABSENT_FROM_THE_CAPTURE` with a synthetic experiment of its own.
- **Conformance** (`tests/test_schema_one_conformance.py`). A property no vendored catalog defines goes in `_SPAN_EXTENSIONS`, and one the pinned emitter's reference tree never declares goes in `_NOT_EXERCISED_BY_THE_EMITTER`, each with its reason.
- **Public API** (`tests/test_public_api_unchanged.py`). A new export is added to `__all__` and to the expected set, with a dated note.
- **The adapter fidelity harness** (`tests/adapter_fidelity/`), below.

## The adapter fidelity harness

`harness.py` replays a captured device tree through the real adapter, perturbs every declared property, and classifies each one: mapped, an extension reading, intentionally dropped by code that says why, or silently dropped. It then holds every value to a
documented rule:

- **Transforms** (`rules.TRANSFORMS`): for each row (a device role and a `node/property`) and each snapshot field it moves, how the field follows from the wire value: identity, negation, integer, literal, boolean, or a derivation with the rule stated where
  it is simple. Every cell shares them, and `test_every_mapped_field_has_a_documented_transform` requires one for every field-metadata row. Change the transform when you change a read, and give a device whose frame differs its own role rather than bending
  a shared row.
- **Derivations**: a field that holds a value no published property moves on its own, such as one decided by a declaration, or a documented elimination. Each cell carries only the derivations its captures need. `test_every_derivation_is_needed` refuses one
  that no cell uses, because an unused justification is how a list of reasons becomes an allowlist. `rules.py` holds the derivations for the two captures in `tests/fixtures/`, and `rules_captured.py` holds those for the reference captures.
- **Drops** (`DropReason`): the three kinds of intentional drop, each citing the code. A cell pins every row it drops _with a value_, each with a reviewed reason (`dropped_with_a_value` in `test_main32_fidelity.py`, `DROPPED_WITH_A_VALUE` in
  `test_capture_fidelity.py`), so a drop rule that newly covers a published property waits for review. Rows the library drops today but should not are listed in `deferred` with the reason, and a deferral that no longer holds fails like a new silent drop.

`test_capture_fidelity.py` measures every capture in `tests/fixtures/captures/` and pins by row, never by capture, so a capture added to that directory is covered without editing the tests. `test_harness.py` plants each kind of defect and checks the
harness names it.

## The expected-failure registry

`tests/expected_failures.py` lists, by `<file>::<function>`, the acceptance tests this line does not pass yet. `tests/conftest.py` marks each one `xfail(strict=True)`, so:

- a `PENDING` row is behaviour a later change brings, and that change deletes the row: a listed test that starts passing fails the run until it is;
- a `DIVERGENT` row is behaviour this library deliberately does not have, and stays until the test or the decision changes;
- a row naming a file that does not exist, or a test function that file does not define (at module level or on a `Test` class), stops the run. The check reads the files rather than the tests a run collected, so a partial run (`-k`, one file) judges every
  row exactly as a full run does, and a renamed or deleted test file cannot orphan its rows.

Ported acceptance tests are never edited. A strict xfail stops at its first failing line, so a companion file (`*_companions.py`) asserts what each divergent test checks after its divergent line.

## Vendored captures

Captures are byte copies and must stay that way:

- `tests/fixtures/captures/` holds the reference captures with their upstream `LICENSE` and a `README.md` naming the upstream repository, path and commit; `SHA256SUMS` pins each capture and the `LICENSE` by SHA-256. `tests/test_captures_unchanged.py`
  checks every pin, requires a pin for every capture and the directory to hold nothing but the pinned files, `README.md` and `SHA256SUMS`; those two are this repository's records and are not pinned. The two captures in `tests/fixtures/` are pinned the same
  way by `tests/adapter_fidelity/test_main32_fidelity.py`.
- `.pre-commit-config.yaml` excludes them from every hook that rewrites files. Keep any new capture path in those excludes.
- A capture is replaced only by copying it again from its upstream commit and updating the pins, never by editing it.

The schema-1 reference tree (`packages/schema-1/src/span_panel_api_schema_1/reference/parent_child_tree.json`) is package data, produced by `scripts/capture_parent_child_reference.py` from `scripts/reference_panel.yaml` with the pinned emitter;
DEVELOPMENT.md, "Regenerating a vendored capture", has the procedure. Change the manifest, never the output, and keep every circuit inside the position range `PANEL_POSITIONS_BY_MODEL` gives the panel's model: the adapter reports the table's range and
warns once for a panel whose circuits fall outside it. A changed capture is a release of the adapter that ships it (RELEASE.md).

## Lessons from review

Each of these was a defect that review has already caught at least once, in this repository or in the emitter it is tested against.

- **Prove a test or a guard by breaking what it guards.** Revert the fix or mutate the code and confirm the test fails; a guard that survives every mutation guards nothing. Assert what a consumer can observe, the snapshot value or the published outcome,
  not an internal step.
- **A test that keeps documentation runnable reads the documentation.** A hand-copied recipe pins the sequence but lets the document rot.
- **Docstrings and the README ship.** A docstring is in the wheel and is what `help()` and an IDE show, and a package README is its PyPI page. A recipe in either runs as written and describes every path the code has.
- **A changelog is checked, not just written.** A `Fixed` entry is for a defect a released version had, not a revision between drafts. Check where each entry lands, since a new heading inserted above an entry re-files it with no deletion in the diff, and
  re-derive every count or list against the source at the tag.
- **The public surface is complete and nothing more.** Every type a public signature names is exported and recorded in `tests/test_public_api_unchanged.py`. Do not add a parameter or flag that gates nothing yet; a dormant surface reads as a feature and has
  to be un-shipped later.
- **Read data as published.** A `null` or empty value in a capture means unpublished, never zero. A predicate that reads declared metadata refuses what it cannot interpret instead of guessing.
- **One capture is not the firmware.** Check a change against every capture available; panels on the same firmware release do not always publish a value in the same form.
- **Cite the specification.** Where the eBus specification and a migration guide disagree, the specification wins, and a rule it states is implemented against the signal it names.
- **Measure claims.** A statement about a real panel, broker or built artifact is checked there, and a result is reported only after it has actually been run.
