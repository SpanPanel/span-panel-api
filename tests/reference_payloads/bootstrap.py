"""The bootstrap distribution's reference payload: the homie schema document.

`homie_schema.json` is the response of `span_panel_api.auth.get_homie_schema()`,
modelled by `V2HomieSchema`, and dispatch reads its `data_model_version` to
decide *which* adapter parses the panel at all. The parent/child device tree is
the other half of that story and lives in `schema_one`, beside the replay that
can interpret it.

**The bytes come out of the installed wheel, not out of this tree.** They are
package data of `span-panel-api-schema-0` — read through `importlib.resources`,
so a downstream test suite pinned to a version of that distribution loads the
same bytes this suite does, from its own site-packages, without vendoring a copy
and without a guard to keep that copy honest. Test-support data, never read on a
runtime path: nothing in the adapter opens it, and `V2HomieSchema` is
constructed from a live panel response in production.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
import json

from span_panel_api.models import HomieSchemaTypes

_HOMIE_SCHEMA = files("span_panel_api_schema_0") / "reference" / "homie_schema.json"


def homie_schema() -> Mapping[str, object]:
    """The captured `GET /api/v2/homie/schema` response, parsed.

    Taken from a live 32-space panel on `spanos2/r202603/05`; serial numbers are
    masked. `typesSchemaHash` is `sha256:d347556a07d98f40`, which is the value
    `span_panel_api_schema_0.const.SCHEMA_ANCHOR` is pinned to.
    """
    document: object = json.loads(_HOMIE_SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"{_HOMIE_SCHEMA.name} is not a JSON object")
    return document


def homie_schema_types() -> HomieSchemaTypes:
    """The captured schema's `types` map.

    Separate from `homie_schema()` because this is the shape a field-metadata
    build takes — `span_panel_api_schema_0.field_metadata.build_field_metadata`
    accepts exactly this type — so a caller checking an adapter's output against
    the schema never has to reach into an untyped document to get it.
    """
    types = homie_schema()["types"]
    if not isinstance(types, dict):
        raise TypeError(f"{_HOMIE_SCHEMA.name} has no `types` object")
    return types
