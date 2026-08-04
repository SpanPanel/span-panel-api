"""Flat-schema adapter package (data-model-version absent)."""

from span_panel_api._impl.schema_0.adapter import SchemaZeroAdapter

# Re-exported from the adapter rather than restated. The protocol requires the
# range as a class attribute, so the class is the source of truth; a second
# literal here would be free to drift, and nothing would notice until a panel
# reported a version this adapter claims — falsely — to support.
SUPPORTS_DATA_MODEL_VERSIONS: tuple[str, str] = SchemaZeroAdapter.SUPPORTS_DATA_MODEL_VERSIONS

__all__ = ["SUPPORTS_DATA_MODEL_VERSIONS", "SchemaZeroAdapter"]
