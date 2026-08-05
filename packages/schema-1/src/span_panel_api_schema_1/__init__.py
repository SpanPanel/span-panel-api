"""Parent/child schema (data-model-version 1.x) support for span-panel-api.

Work in progress. This package does not yet register a `schema_1` adapter — see
the note in pyproject.toml. Until it can answer for a panel end to end, a 1.x
panel gets a clean SpanPanelAdapterMissingError rather than a late failure from
a half-built parser.
"""

from span_panel_api_schema_1.transport import ControllerRoutes

__all__ = ["ControllerRoutes"]
