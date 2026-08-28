# Releasing

This repository publishes **more than one PyPI distribution** from a single source tree. That makes releasing less obvious than `git tag && push`, so this document is the reference: what lives where, how a tag selects what gets published, and what an
administrator has to do to release everything.

## Layout

One repository, one [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/), independent distributions:

| Distribution              | Directory            | Manifest                           | Purpose                                                                 |
| ------------------------- | -------------------- | ---------------------------------- | ----------------------------------------------------------------------- |
| `span-panel-api`          | repository root      | `pyproject.toml`                   | The **bootstrap** — transport, dispatch, protocols. Contains no parser. |
| `span-panel-api-schema-0` | `packages/schema-0/` | `packages/schema-0/pyproject.toml` | Flat-schema parser (firmware `r202603`–`r202627`)                       |

Adapters are discovered at runtime through the `span_panel_api.schema_adapters` entry-point group. The bootstrap never imports an adapter, and adding an adapter to the field is an install, not an upgrade. Future adapters follow the same pattern under
`packages/schema-N/`.

Consequences for releasing:

- **Each distribution has its own version number** in its own manifest.
- **Each distribution is its own PyPI project**, with its own trusted publisher.
- **A release publishes exactly one distribution.** Releasing "the repo" means cutting one release per distribution.

## Two version axes

The bootstrap and the adapters do not share a version, and this is deliberate rather than an oversight.

- **The bootstrap** versions on its own library API — the transport and the `SchemaAdapter` protocol.
- **An adapter** versions on _its_ library API. The wire format it parses is fixed and is declared by `SUPPORTS_DATA_MODEL_VERSIONS`, not by the version number. A release of `span-panel-api-schema-0` means the parser changed, never that the panel did.

So `span-panel-api 3.0.0` and `span-panel-api-schema-0 1.0.0` are unrelated numbers, and either can move without the other.

Adapters declare a floor on the bootstrap (`span-panel-api>=3.0.0,<4.0`). That dependency is why the versions committed in the manifests are load-bearing: they participate in resolution, so they are not placeholders that a release process may overwrite.

Those floors name **stable** versions on purpose. A specifier that names a prerelease is pip's own signal that prereleases are acceptable for that requirement, so a floor left pointing at a beta would leave a released install willing to resolve a future
beta of its sibling without anyone asking for one.

## How a tag selects a distribution

`.github/workflows/release.yml` runs on `release: published` and derives everything from the tag name. There is no lookup table — the manifest path is computed by convention:

| Tag               | Distribution published    | Manifest read                      |
| ----------------- | ------------------------- | ---------------------------------- |
| `vX.Y.Z`          | `span-panel-api`          | `pyproject.toml`                   |
| `schema-N-vX.Y.Z` | `span-panel-api-schema-N` | `packages/schema-N/pyproject.toml` |

Worked example for `schema-0-v1.0.0b1`:

```text
TAG                 = schema-0-v1.0.0b1
${TAG#schema-}      → 0-v1.0.0b1          strip leading "schema-"
${SCHEMA%%-v*}      → 0                   strip trailing "-v…"      ⇒ schema number
PACKAGE             = span-panel-api-schema-0
MANIFEST            = packages/schema-0/pyproject.toml
VERSION             = ${TAG#schema-0-v} → 1.0.0b1
```

Because the schema number is _extracted_ rather than enumerated, a future `schema-1-v0.1.0` resolves to `packages/schema-1/pyproject.toml` with no change to the workflow.

A tag matching neither form (`1.2.3`, `nightly`) fails immediately with a message naming both accepted forms.

## The tag does not set the version

The workflow **verifies** the version; it does not write it.

```text
tag  schema-0-v1.0.0b1
  ⇒ packages/schema-0/pyproject.toml must declare version = "1.0.0b1"
  ⇒ otherwise the job fails without publishing
```

This means the release ritual is **bump, commit, then tag** — never tag-and-let-CI-stamp. Earlier versions of this workflow rewrote the version from the tag with `sed`, which cannot work here: there is no single manifest to stamp, and the adapter's
dependency floor on the bootstrap means a stamped version could silently disagree with what resolution actually uses.

A mismatch is a hard failure with both numbers in the message, so the common mistake — tagging before committing the bump — is caught before anything reaches PyPI.

## Releasing one distribution

1. **Bump the version** in that distribution's manifest, and record the change in its `CHANGELOG.md` (the root one for the bootstrap, `packages/schema-N/CHANGELOG.md` for an adapter).

   **Changelogs carry public versions only.** A beta gets no heading of its own: fold its changes into the entry for the public version it is working towards, described against the **last public release** rather than against the beta before it. A fix that
   only repairs something an earlier beta broke does not appear at all — from the point of view of somebody upgrading between released versions, it never happened. This keeps the file answering the question a reader actually has ("what changes if I
   upgrade?") instead of narrating development.

2. **Merge to `develop`** (or `main`, once this work is no longer prototype) and let CI go green.
3. **Create a GitHub Release:**
   - **Tag** — `vX.Y.Z` or `schema-N-vX.Y.Z`, per the table above.
   - **Target** — the branch holding the bump. This defaults to the repository's default branch, which is the easiest thing to get wrong; a tag cut from the wrong branch builds the wrong version and fails the verification step.
   - **Set as a pre-release** — tick this for any `aN` / `bN` / `rcN` version.
4. **Watch the run.** `gh run watch "$(gh run list --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId')"`

The job prints exactly what it resolved, which is the first thing to read if something looks wrong:

```text
Tag 'schema-0-v1.0.0b1' releases span-panel-api-schema-0 1.0.0b1 from packages/schema-0/pyproject.toml
Version 1.0.0b1 confirmed.
```

## Releasing every distribution

There is no "release everything" button, and that is intentional — the distributions version independently, so a coordinated release is a sequence of single-distribution releases rather than one action.

To release the whole workspace:

1. Bump every manifest that changed, in one branch, with its changelog entry.
2. If the bootstrap's version moved and adapters need the new floor, update `span-panel-api>=…` in each adapter manifest **in the same branch**. Do not release an adapter whose floor points at a bootstrap version that is not yet on PyPI.
3. Merge and let CI go green.
4. Cut the releases **bootstrap first, then each adapter**:

   ```text
   v3.0.0                → span-panel-api
   schema-0-v1.0.0       → span-panel-api-schema-0
   schema-1-v1.0.0       → span-panel-api-schema-1
   ```

   PyPI accepts them in any order, but bootstrap-first means there is never a window in which an adapter is installable and its dependency is not.

5. Verify from PyPI rather than from CI — see below.

Only bump and release what actually changed. A distribution with no changes does not need a release just because a sibling had one.

## Adding a new adapter

When `packages/schema-N/` lands, the workflow needs no edit — but PyPI does, and this is the step that will be forgotten:

1. **Create the PyPI project and its trusted publisher before the first release.** Because the project does not exist yet, this is a _pending publisher_, added from account/organization publishing settings rather than from the (non-existent) project page:

   | Field             | Value                     |
   | ----------------- | ------------------------- |
   | PyPI Project Name | `span-panel-api-schema-N` |
   | Owner             | `SpanPanel`               |
   | Repository name   | `span-panel-api`          |
   | Workflow name     | `release.yml`             |
   | Environment name  | `release`                 |

   Every field except the project name is identical across all distributions here, since they all publish from the same repository and workflow. Once the project exists, the same entry is visible and editable at
   `https://pypi.org/manage/project/<name>/settings/publishing/`.

2. **Add the package to the workspace** — it is matched by `members = ["packages/*"]` automatically, but the root `[tool.uv.sources]` and the dev dependency group need an entry if the test suite is to exercise it.
3. **Ship a `py.typed` marker** in the new package. CI fails the build without it.

Trusted publishing verifies repository, workflow filename, and environment — it cannot distinguish _which_ distribution a run is building. That is inherent to a monorepo, and it is why the workflow builds only the tagged package: `dist/` never contains a
sibling that could be uploaded by accident.

## What the workflow checks

In order, all before anything is uploaded:

1. **Tag names a known distribution** — otherwise fail, naming both accepted forms.
2. **The derived manifest exists** — catches a `schema-N` tag with no matching directory.
3. **The tag version equals the committed version** — read with `tomllib`, compared exactly.
4. **Only the tagged package is built** — `uv build --package <name>`, so `dist/` holds exactly one distribution.
5. **Every built wheel ships `py.typed`** — a fully annotated distribution that omits it resolves as `Any` for every downstream consumer, silently undoing the strict typing this repository maintains.

| Failure                                     | Meaning                                                                                                         |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `Tag '…' names no distribution`             | Tag is malformed. Use `vX.Y.Z` or `schema-N-vX.Y.Z`.                                                            |
| `resolves to '…', which does not exist`     | Tag names a schema whose directory is not in this commit — usually a tag cut from the wrong branch.             |
| `declares version 'A' but the tag says 'B'` | The bump was not committed, or the release targets the wrong branch.                                            |
| `ships no py.typed marker`                  | The new package is missing the marker file.                                                                     |
| OIDC / trusted publishing rejection         | The PyPI publisher for that project is missing or does not match. Nothing was uploaded; fix and re-run the job. |

A failed release is safe. Every check runs before upload, so a failure means nothing reached PyPI and the same tag can be re-run once the cause is fixed.

## Verifying a release

CI going green proves the build, not the install. The seam this repository is built around — a bootstrap that finds a parser it never imports — can only be exercised across a real package boundary, so verify from PyPI:

```bash
# 1. The bootstrap alone must fail by name, not with ModuleNotFoundError
python3 -m venv .solo && ./.solo/bin/pip install span-panel-api
./.solo/bin/python -c "
from span_panel_api.adapters import installed_adapter_keys, resolve_adapter, DEFAULT_ADAPTER_KEY
from span_panel_api.exceptions import SpanPanelAdapterMissingError
print('adapters:', installed_adapter_keys())
try:
    resolve_adapter(DEFAULT_ADAPTER_KEY, 'release check')
except SpanPanelAdapterMissingError as exc:
    print('raised as designed:', exc.needed, exc.available)
"

# 2. Both packages: the adapter resolves through discovery
python3 -m venv .both && ./.both/bin/pip install "span-panel-api[schema-0]"
./.both/bin/python -c "
from span_panel_api.adapters import installed_adapter_keys
print('adapters:', installed_adapter_keys())
"

# 3. The extra is the upgrade path, so check it resolves the adapter too
python3 -m venv .all && ./.all/bin/pip install "span-panel-api[schema-0,schema-1]"
./.all/bin/python -c "
from span_panel_api.adapters import installed_adapter_keys
print('adapters:', installed_adapter_keys())
"
```

Expected: `adapters: []` then a named `SpanPanelAdapterMissingError` in the first, `adapters: ['schema_0']` in the second, and both keys in the third.

Add `--pre` only when the versions being verified are pre-releases. It is not the default verb any more: from 3.0.0 onwards every distribution here publishes stable versions, and no floor in any manifest names a prerelease — which is deliberate, since a
specifier that names one is pip's own signal that prereleases are acceptable for that requirement.

## Pre-releases

Versions like `3.0.0b1` are pre-releases in both places that matter:

- **PyPI** will not install them without `--pre`, so `pip install span-panel-api` continues to resolve the last stable release.
- **GitHub** should have "Set as a pre-release" ticked, which keeps them out of the repository's "Latest release" slot.

The publish workflow itself does not care — `on: release: published` fires either way.

## When the emitter releases

`ebus-panel-sim` is a pinned dev dependency, so a release past the pin arrives the way every other dependency's does: as a Dependabot pull request, ungrouped and on its own. Following it is bump the pin, re-run `scripts/capture_parent_child_reference.py`,
run the suite — and the suite is what says whether the wire moved. See DEVELOPMENT.md, "Conformance against the specification and the producer". If the capture changes, that is a release of `span-panel-api-schema-1`, for the reason the next section gives.

## A capture change is a release of the adapter that ships it

Each adapter carries the reference capture its consumers test against — `span_panel_api_schema_1/reference/parent_child_tree.json` and `span_panel_api_schema_0/reference/homie_schema.json`. Those are package data, so re-running a capture changes what the
wheel contains even when no parser did, and that is a release of that adapter like any other change to its contents. Bump the adapter's version and add a CHANGELOG entry saying the capture moved; downstream test suites read these bytes out of the version
they pin, and a version that silently means two different trees is the thing this arrangement exists to prevent.
