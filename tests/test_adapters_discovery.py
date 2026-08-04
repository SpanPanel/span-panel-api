from __future__ import annotations

from unittest.mock import patch

import pytest

from span_panel_api.adapters import DEFAULT_ADAPTER_KEY, _reset_adapter_cache, discover_adapters, resolve_adapter
from span_panel_api.exceptions import SpanPanelAdapterMissingError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig


def test_discovers_the_self_registered_schema_zero_adapter() -> None:
    _reset_adapter_cache()
    registry = discover_adapters()

    assert "schema_0" in registry
    assert registry["schema_0"].__name__ == "SchemaZeroAdapter"


def test_registry_is_cached_across_calls() -> None:
    _reset_adapter_cache()
    assert discover_adapters() is discover_adapters()


# ---------------------------------------------------------------------------
# The default adapter path — the bootstrap must not import a parser to get one
# ---------------------------------------------------------------------------


def _client(adapter_factory: object = None) -> SpanMqttClient:
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    kwargs = {} if adapter_factory is None else {"adapter_factory": adapter_factory}
    return SpanMqttClient("panel.local", "SERIAL123", config, **kwargs)  # type: ignore[arg-type]


def test_default_factory_resolves_the_flat_adapter_through_discovery() -> None:
    """No adapter_factory means "resolve the default key", not "import SchemaZeroAdapter"."""
    _reset_adapter_cache()
    client = _client()

    adapter = client._build_adapter(32)

    assert adapter.schema_major == DEFAULT_ADAPTER_KEY
    assert type(adapter) is discover_adapters()[DEFAULT_ADAPTER_KEY]


def test_constructing_a_client_does_not_require_an_installed_adapter() -> None:
    """Construction must stay adapter-free; only building a parser needs one.

    This is the property that lets the bootstrap ship without a parser at all.
    """
    with patch("span_panel_api.adapters._REGISTRY", {}):
        _client()  # must not raise


def test_building_a_parser_without_any_adapter_raises_by_name() -> None:
    """The adapter-less install's failure mode: a named error, not ModuleNotFoundError."""
    _reset_adapter_cache()
    client = _client()

    with patch("span_panel_api.adapters._REGISTRY", {}), pytest.raises(SpanPanelAdapterMissingError) as exc:
        client._build_adapter(32)

    assert exc.value.needed == DEFAULT_ADAPTER_KEY
    assert exc.value.available == []


def test_an_explicit_factory_bypasses_discovery_entirely() -> None:
    """Injection still wins — used by the factory's Tier 1 dispatch and by tests."""
    _reset_adapter_cache()
    real_cls = discover_adapters()[DEFAULT_ADAPTER_KEY]
    client = _client(adapter_factory=real_cls)

    with patch("span_panel_api.adapters.discover_adapters", side_effect=AssertionError("must not be consulted")):
        adapter = client._build_adapter(32)

    assert type(adapter) is real_cls


def test_resolve_adapter_names_what_is_installed() -> None:
    _reset_adapter_cache()
    with pytest.raises(SpanPanelAdapterMissingError) as exc:
        resolve_adapter("schema_9", "made-up key")

    assert exc.value.needed == "schema_9"
    assert DEFAULT_ADAPTER_KEY in exc.value.available
