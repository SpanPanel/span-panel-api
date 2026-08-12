from __future__ import annotations

from typing import Any, Protocol
from unittest.mock import patch

import pytest

from span_panel_api.adapters import (
    DEFAULT_ADAPTER_KEY,
    _reset_adapter_cache,
    installed_adapter_keys,
    resolve_adapter,
)
from span_panel_api.exceptions import SpanPanelAdapterIncompatibleError, SpanPanelAdapterMissingError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig
from span_panel_api.protocol import ADAPTER_CONTRACT_VERSION

from conftest import MOCK_SCHEMA


def test_discovers_the_self_registered_schema_zero_adapter() -> None:
    _reset_adapter_cache()

    assert "schema_0" in installed_adapter_keys()
    assert resolve_adapter("schema_0", "test").__name__ == "SchemaZeroAdapter"


def test_resolution_is_cached_across_calls() -> None:
    """Per key, and it has to be: `_on_pre_rebuild` resolves from a synchronous
    bridge callback and relies on there being no import left to do."""
    _reset_adapter_cache()
    assert resolve_adapter("schema_0", "test") is resolve_adapter("schema_0", "test")


# ---------------------------------------------------------------------------
# The default adapter path — the bootstrap must not import a parser to get one
# ---------------------------------------------------------------------------


def _client(adapter_factory: object = None) -> SpanMqttClient:
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    kwargs = {} if adapter_factory is None else {"adapter_factory": adapter_factory}
    return SpanMqttClient("panel.local", "SERIAL123", config, **kwargs)  # type: ignore[arg-type]


def _nothing_installed() -> Any:
    """Patch enumeration to a completed scan that found nothing.

    A completed empty scan, not a missing one: `None` would make the next call
    re-scan and pick up this environment's real adapters.
    """
    return patch("span_panel_api.adapters._ENTRY_POINTS", {})


def test_default_factory_resolves_the_flat_adapter_through_discovery() -> None:
    """No adapter_factory means "resolve the default key", not "import SchemaZeroAdapter"."""
    _reset_adapter_cache()
    client = _client()

    adapter = client._build_adapter(MOCK_SCHEMA)

    assert adapter.schema_major == DEFAULT_ADAPTER_KEY
    assert type(adapter) is resolve_adapter(DEFAULT_ADAPTER_KEY, "test")


def test_constructing_a_client_does_not_require_an_installed_adapter() -> None:
    """Construction must stay adapter-free; only building a parser needs one.

    This is the property that lets the bootstrap ship without a parser at all.
    """
    with _nothing_installed():
        _client()  # must not raise


def test_building_a_parser_without_any_adapter_raises_by_name() -> None:
    """The adapter-less install's failure mode: a named error, not ModuleNotFoundError."""
    _reset_adapter_cache()
    client = _client()

    with _nothing_installed(), pytest.raises(SpanPanelAdapterMissingError) as exc:
        client._build_adapter(MOCK_SCHEMA)

    assert exc.value.needed == DEFAULT_ADAPTER_KEY
    assert exc.value.available == []


def test_an_explicit_factory_bypasses_discovery_entirely() -> None:
    """Injection still wins — used by the factory's Tier 1 dispatch and by tests.

    Patched where the client looks it up rather than where it is defined: the
    module imports the name, so patching `adapters.resolve_adapter` rebinds a
    reference `_build_adapter` never reads, and the assertion could not fire.
    """
    _reset_adapter_cache()
    real_cls = resolve_adapter(DEFAULT_ADAPTER_KEY, "test")
    client = _client(adapter_factory=real_cls)

    with patch("span_panel_api.mqtt.client.resolve_adapter", side_effect=AssertionError("must not be consulted")):
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
        self.loads = 0

    def load(self) -> object:
        self.loads += 1
        return self._value


def _discover_with(*eps: _FakeEntryPoint) -> dict[str, object]:
    """Every entry point that survives vetting, resolved one key at a time.

    This is what the eager registry used to be, rebuilt by the test rather than
    by the module — discovery no longer produces such a map, because producing
    one is exactly the import-everything cost the split removed. The vetting
    rules below are unchanged and still deserve asserting individually, so the
    map is reconstructed here instead of rewriting each of them into a
    try/except around a single resolve.
    """
    _reset_adapter_cache()
    usable: dict[str, object] = {}
    with patch("span_panel_api.adapters.entry_points", return_value=list(eps)):
        for name in installed_adapter_keys():
            try:
                usable[name] = resolve_adapter(name, "test")
            except (SpanPanelAdapterMissingError, SpanPanelAdapterIncompatibleError):
                continue
    return usable


def test_resolving_one_key_leaves_the_others_unimported() -> None:
    """The property the split exists for: a flat panel must not import schema_1.

    That package pulls in the eBus SDK and jsonschema — two seconds on a cold
    import cache, and a dependency its own packaging confines to that
    distribution precisely so a flat install stays clear of it. Eager discovery
    imported it on every flat connection, and redispatch made installing both
    adapters the normal setup, so "installed" stopped implying "used".

    Asserted on `load()` rather than on `sys.modules`, which by this point in a
    test session has every adapter in it for unrelated reasons.
    """
    from span_panel_api_schema_0 import SchemaZeroAdapter

    wanted = _FakeEntryPoint("schema_0", SchemaZeroAdapter)
    other = _FakeEntryPoint("schema_9", SchemaZeroAdapter)

    _reset_adapter_cache()
    with patch("span_panel_api.adapters.entry_points", return_value=[wanted, other]):
        assert installed_adapter_keys() == ["schema_0", "schema_9"], "both must still be reported installed"
        resolve_adapter("schema_0", "test")

    assert wanted.loads == 1
    assert other.loads == 0, "resolving one key must not import the rest"


def _conforming_members(contract: object = ADAPTER_CONTRACT_VERSION) -> dict[str, object]:
    """Members for a class that passes discovery, derived from the protocol.

    Derived rather than listed so it stays honest as SchemaAdapter grows: a test
    that builds its fixture by hand starts passing for the wrong reason the day
    a member is added.

    ADAPTER_CONTRACT is the one member a callable will not do for, because it is
    checked for value and not only presence — which is the whole point of it.
    """
    from span_panel_api.adapters import _REQUIRED_MEMBERS

    members: dict[str, object] = {name: (lambda self, *args, **kwargs: None) for name in _REQUIRED_MEMBERS}
    members["ADAPTER_CONTRACT"] = contract
    return members


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
    complete = _conforming_members()
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


# ---------------------------------------------------------------------------
# Contract versioning — an adapter built against a different bootstrap must be
# rejected where the remedy can still be named, not at construction
# ---------------------------------------------------------------------------


def test_the_shipped_adapters_declare_the_contract_this_package_speaks() -> None:
    """The pairing that actually ships. Both adapters version independently of
    the bootstrap, so nothing but this check keeps their declared contract
    honest when the protocol moves."""
    from span_panel_api_schema_0 import SchemaZeroAdapter
    from span_panel_api_schema_1 import SchemaOneAdapter

    assert SchemaZeroAdapter.ADAPTER_CONTRACT == ADAPTER_CONTRACT_VERSION
    assert SchemaOneAdapter.ADAPTER_CONTRACT == ADAPTER_CONTRACT_VERSION


@pytest.mark.parametrize(
    ("label", "contract"),
    [
        ("older", ADAPTER_CONTRACT_VERSION - 1),
        ("newer", ADAPTER_CONTRACT_VERSION + 1),
    ],
)
def test_an_adapter_built_for_another_contract_is_rejected(label: str, contract: int) -> None:
    """Both directions, because either half can be the stale one: an old adapter
    against a new bootstrap, or an adapter from a future release against this."""
    members = _conforming_members(contract=contract)

    assert _discover_with(_FakeEntryPoint("schema_9", type("Mismatched", (), members))) == {}, label


def test_a_contract_that_is_not_an_integer_is_rejected() -> None:
    """`True == 1` is the trap: bool is a subclass of int, so a truthy marker
    would otherwise compare equal to contract 1 and be accepted."""
    assert _discover_with(_FakeEntryPoint("schema_9", type("Truthy", (), _conforming_members(contract=True)))) == {}
    assert _discover_with(_FakeEntryPoint("schema_9", type("Stringly", (), _conforming_members(contract="1")))) == {}


def test_an_adapter_predating_contract_versioning_is_rejected_by_age_not_by_shape() -> None:
    """The real regression this closes: schema-1 0.1.0b1 paired with a bootstrap
    whose adapters took `panel_size`. Such an adapter carries every other
    required name, so nothing but the contract member distinguishes it, and
    without one it reached construction and died on argument count."""
    members = _conforming_members()
    del members["ADAPTER_CONTRACT"]

    _reset_adapter_cache()
    with patch(
        "span_panel_api.adapters.entry_points",
        return_value=[_FakeEntryPoint("schema_9", type("Ancient", (), members))],
    ):
        with pytest.raises(SpanPanelAdapterIncompatibleError) as exc:
            resolve_adapter("schema_9", "test")

    assert "predates contract versioning" in str(exc.value)


def test_a_rejected_adapter_is_reported_as_unusable_not_as_missing() -> None:
    """Absent and rejected are opposite remedies. Reporting a stale adapter as
    missing sends someone to install a package they already have."""
    members = _conforming_members(contract=ADAPTER_CONTRACT_VERSION + 1)

    _reset_adapter_cache()
    with patch(
        "span_panel_api.adapters.entry_points",
        return_value=[_FakeEntryPoint("schema_9", type("FromTheFuture", (), members))],
    ):
        with pytest.raises(SpanPanelAdapterIncompatibleError) as exc:
            resolve_adapter("schema_9", "panel needs it")

    assert exc.value.needed == "schema_9"
    assert "contract" in exc.value.defect
    # Still the missing error when nothing registers the key at all, so the two
    # paths cannot quietly collapse into one message.
    assert not isinstance(exc.value, SpanPanelAdapterMissingError)


def test_a_rejected_adapter_does_not_make_a_working_one_unreachable() -> None:
    """The rejection is per entry point. A stale third-party adapter must not
    stop the panel whose own adapter is fine, which is why discovery logs rather
    than raises and only resolve_adapter turns it into an error."""
    from span_panel_api_schema_0 import SchemaZeroAdapter

    stale = type("Stale", (), _conforming_members(contract=ADAPTER_CONTRACT_VERSION + 1))
    registry = _discover_with(
        _FakeEntryPoint("schema_9", stale),
        _FakeEntryPoint("schema_0", SchemaZeroAdapter),
    )

    assert registry == {"schema_0": SchemaZeroAdapter}
