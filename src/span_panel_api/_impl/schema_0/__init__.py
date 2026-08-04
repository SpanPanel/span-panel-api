"""Flat-schema adapter package (data-model-version absent)."""

from span_panel_api._impl.schema_0.adapter import SchemaZeroAdapter

# Inclusive lower bound, exclusive upper bound. The flat schema publishes no
# data-model-version, so it is treated as the synthetic version 0 range.
SUPPORTS_DATA_MODEL_VERSIONS: tuple[str, str] = (">=0", "<1.0")

__all__ = ["SUPPORTS_DATA_MODEL_VERSIONS", "SchemaZeroAdapter"]
