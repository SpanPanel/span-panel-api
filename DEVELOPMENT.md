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
