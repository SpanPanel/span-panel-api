"""Reference wire payloads, as ordinary fixtures of this repository's suite.

Captures of what a panel actually serves. They lived inside the two source
packages until 3.1.0 and were therefore carried in the wheels — not through any
packaging declaration, but simply because a directory inside `src/<package>/`
ships. Nothing at runtime ever read them, so every install paid for test data it
could not use, and a consumer who imported them acquired a dependency on files
the distributions never meant to promise. They are here now, where their only
readers are.

Two modules rather than one, split the way the distributions are:

- `bootstrap` — `homie_schema.json`, the document `span_panel_api.auth` fetches
  and dispatch reads `data_model_version` out of. No adapter is involved, so
  this module imports nothing an adapter-less environment lacks.
- `schema_one` — `parent_child_tree.json`, a retained-topic capture, which is
  only interpretable by the parser that speaks its vocabulary. Importing it
  reaches the eBus SDK, which is `span-panel-api-schema-1`'s dependency alone.

Keeping the split means a test that needs the schema document never drags the
SDK in behind it, which is the same reason the two payloads were in different
distributions to begin with.

Imported as a top-level package — `from reference_payloads.schema_one import
...` — because pytest puts `tests/` on `sys.path`, the same arrangement that
makes `from conftest import ...` work throughout this suite.
"""

from __future__ import annotations
