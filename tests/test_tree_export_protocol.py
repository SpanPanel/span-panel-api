"""The masked-tree export is an optional adapter capability, not a required one.

`adapters._REQUIRED_MEMBERS` is derived from `SchemaAdapter`, so a member added
there would be demanded of every built adapter wheel. The export lives in a
protocol of its own, which an adapter satisfies or not.
"""

from __future__ import annotations

from span_panel_api import MaskedTree, TreeExportingAdapter, adapters


def test_export_is_not_required_of_every_adapter() -> None:
    assert "export_masked_tree" not in adapters._REQUIRED_MEMBERS


def test_an_object_with_the_method_satisfies_the_protocol() -> None:
    class _Exporter:
        def export_masked_tree(self) -> MaskedTree | None:
            return None

    assert isinstance(_Exporter(), TreeExportingAdapter)
    assert not isinstance(object(), TreeExportingAdapter)
