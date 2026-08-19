"""Reference wire payloads, shipped as package data.

These are captures of what a panel actually serves, published as part of the
distribution rather than kept in `tests/` — because the consumers that need
them most are *other* repositories. The Home Assistant integration checks the
field paths it declares against what the adapters can actually produce, and it
can only do that against a real schema document. Vendoring a copy of one is the
obvious move and the wrong one: a copy has no version, so it goes stale in
silence and the check starts verifying declarations against a schema no panel
runs.

Shipping the payload here gives it the version of the release it came with. A
consumer that pins `span-panel-api==X` reads the document that release was
written against, by construction, with no copy to keep in sync.

`homie_schema.json` belongs to this distribution and not to an adapter one: it
is the response of `span_panel_api.auth.get_homie_schema()`, modelled by
`V2HomieSchema` here, and dispatch reads its `data_model_version` to decide
*which* adapter parses the panel at all. The parent/child device tree is the
other half of that story and lives with the parser that can interpret it, in
`span_panel_api_schema_1.reference_payloads`.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
import json

from span_panel_api.models import HomieSchemaTypes

_PACKAGE = "span_panel_api.reference_payloads"
_HOMIE_SCHEMA = "homie_schema.json"


def _load_object(name: str) -> Mapping[str, object]:
    """Read one shipped payload and require it to be a JSON object."""
    text = resources.files(_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    document: object = json.loads(text)
    if not isinstance(document, dict):
        raise TypeError(f"{name} is not a JSON object")
    return document


def homie_schema() -> Mapping[str, object]:
    """The captured `GET /api/v2/homie/schema` response, parsed.

    Taken from a live 32-space panel on `spanos2/r202603/05`; serial numbers are
    masked. `typesSchemaHash` is `sha256:d347556a07d98f40`, which is the value
    `span_panel_api_schema_0.const.SCHEMA_ANCHOR` is pinned to.
    """
    return _load_object(_HOMIE_SCHEMA)


def homie_schema_types() -> HomieSchemaTypes:
    """The captured schema's `types` map.

    Separate from `homie_schema()` because this is the shape a field-metadata
    build takes — `span_panel_api_schema_0.field_metadata.build_field_metadata`
    accepts exactly this type — so a caller checking an adapter's output against
    the schema never has to reach into an untyped document to get it.
    """
    types = homie_schema()["types"]
    if not isinstance(types, dict):
        raise TypeError(f"{_HOMIE_SCHEMA} has no `types` object")
    return types
