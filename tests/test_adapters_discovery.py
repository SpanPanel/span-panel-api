from __future__ import annotations

from typing import Protocol
from unittest.mock import patch

import pytest

from span_panel_api.adapters import DEFAULT_ADAPTER_KEY, _reset_adapter_cache, discover_adapters, resolve_adapter
from span_panel_api.exceptions import SpanPanelAdapterMissingError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import MOCK_SCHEMA


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

    adapter = client._build_adapter(MOCK_SCHEMA)

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
        client._build_adapter(MOCK_SCHEMA)

    assert exc.value.needed == DEFAULT_ADAPTER_KEY
    assert exc.value.available == []


def test_an_explicit_factory_bypasses_discovery_entirely() -> None:
    """Injection still wins — used by the factory's Tier 1 dispatch and by tests."""
    _reset_adapter_cache()
    real_cls = discover_adapters()[DEFAULT_ADAPTER_KEY]
    client = _client(adapter_factory=real_cls)

    with patch("span_panel_api.adapters.discover_adapters", side_effect=AssertionError("must not be consulted")):
        adapter = client._build_adapter(MOCK_SCHEMA)

    assert type(adapter) is real_cls


def test_resolve_adapter_names_what_is_installed() -> None:
    _reset_adapter_cache()
    with pytest.raises(SpanPanelAdapterMissingError) as exc:
        resolve_adapter("schema_9", "made-up key")

    assert exc.value.needed == "schema_9"
    assert DEFAULT_ADAPTER_KEY in exc.value.available


# ---------------------------------------------------------------------------
# Entry-point validation — a bad adapter package must not become an opaque
# TypeError deep inside connect()
# ---------------------------------------------------------------------------


class _FakeEntryPoint:
    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self._value = value

    def load(self) -> object:
        return self._value


def _discover_with(*eps: _FakeEntryPoint) -> dict[str, object]:
    _reset_adapter_cache()
    with patch("span_panel_api.adapters.entry_points", return_value=list(eps)):
        return dict(discover_adapters())


def test_required_members_are_derived_from_the_protocol() -> None:
    """The check must not restate the contract — a method added to SchemaAdapter
    becomes required of every adapter without anyone remembering to update a list."""
    from span_panel_api.adapters import _REQUIRED_MEMBERS
    from span_panel_api.protocol import SchemaAdapter

    assert set(SchemaAdapter.__annotations__) <= set(_REQUIRED_MEMBERS)
    assert "topics_to_subscribe" in _REQUIRED_MEMBERS
    assert "build_snapshot" in _REQUIRED_MEMBERS
    # Dunders are excluded: presence tells us nothing, every object has them.
    assert not [member for member in _REQUIRED_MEMBERS if member.startswith("_")]


def test_every_kind_of_declared_member_is_required_not_just_plain_methods() -> None:
    """A property is not callable and neither is a classmethod object, so a
    kind-filtered derivation would silently stop requiring them. SchemaAdapter
    declares only plain methods today; this pins the rule before it declares more."""
    from span_panel_api.adapters import _derive_required_members

    class SurfaceProbe(Protocol):
        annotated: str

        @property
        def a_property(self) -> int: ...

        @classmethod
        def a_classmethod(cls) -> None: ...

        @staticmethod
        def a_staticmethod() -> None: ...

        def a_method(self) -> None: ...

    assert set(_derive_required_members(SurfaceProbe)) == {
        "annotated",
        "a_property",
        "a_classmethod",
        "a_staticmethod",
        "a_method",
    }


def test_an_adapter_missing_a_non_method_member_is_still_rejected() -> None:
    """The end-to-end consequence of the rule above: presence checking has to
    reach members that are not plain methods, or a defective adapter registers.

    Built from _REQUIRED_MEMBERS so it stays honest as the protocol grows: the
    'complete' half proves the fixture really does satisfy the check, which is
    what makes the 'incomplete' half's rejection attributable to the one
    removed member rather than to an unrelated gap.
    """
    from span_panel_api.adapters import _REQUIRED_MEMBERS

    complete = {name: (lambda self, *args, **kwargs: None) for name in _REQUIRED_MEMBERS}
    incomplete = {name: value for name, value in complete.items() if name != "SUPPORTS_DATA_MODEL_VERSIONS"}

    assert _discover_with(_FakeEntryPoint("schema_9", type("Complete", (), complete))) != {}
    assert _discover_with(_FakeEntryPoint("schema_9", type("Incomplete", (), incomplete))) == {}


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("a module", pytest),
        ("a function", lambda serial, size: None),
        ("an instance rather than a class", object()),
        ("a string", "span_panel_api_schema_0:SchemaZeroAdapter"),
    ],
)
def test_non_class_entry_points_are_skipped_not_registered(label: str, value: object) -> None:
    """The failure that actually happens: an entry point pointing at the wrong
    kind of object. Phase 0 stored it and blew up later inside connect()."""
    assert _discover_with(_FakeEntryPoint("schema_9", value)) == {}, label


def test_a_class_missing_protocol_members_is_skipped() -> None:
    class NotAnAdapter:
        schema_major = "schema_9"

    assert _discover_with(_FakeEntryPoint("schema_9", NotAnAdapter)) == {}


def test_a_conforming_class_is_registered() -> None:
    from span_panel_api_schema_0 import SchemaZeroAdapter

    registry = _discover_with(_FakeEntryPoint("schema_0", SchemaZeroAdapter))

    assert registry == {"schema_0": SchemaZeroAdapter}


def test_one_bad_adapter_does_not_hide_the_good_ones() -> None:
    """A broken third-party adapter must not take down a panel whose own adapter
    is installed and fine."""
    from span_panel_api_schema_0 import SchemaZeroAdapter

    registry = _discover_with(
        _FakeEntryPoint("schema_9", "not a class"),
        _FakeEntryPoint("schema_0", SchemaZeroAdapter),
    )

    assert registry == {"schema_0": SchemaZeroAdapter}


def test_an_entry_point_that_raises_on_load_is_skipped() -> None:
    from span_panel_api_schema_0 import SchemaZeroAdapter

    class Exploding(_FakeEntryPoint):
        def load(self) -> object:
            raise ImportError("adapter package is half-installed")

    registry = _discover_with(Exploding("schema_9", None), _FakeEntryPoint("schema_0", SchemaZeroAdapter))

    assert registry == {"schema_0": SchemaZeroAdapter}
