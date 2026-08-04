from __future__ import annotations

from span_panel_api.adapters import _reset_adapter_cache, discover_adapters


def test_discovers_the_self_registered_schema_zero_adapter() -> None:
    _reset_adapter_cache()
    registry = discover_adapters()

    assert "schema_0" in registry
    assert registry["schema_0"].__name__ == "SchemaZeroAdapter"


def test_registry_is_cached_across_calls() -> None:
    _reset_adapter_cache()
    assert discover_adapters() is discover_adapters()
