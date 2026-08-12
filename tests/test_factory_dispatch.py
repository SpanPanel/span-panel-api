from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api_schema_0 import SchemaZeroAdapter
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api.adapters import _reset_adapter_cache
from span_panel_api.exceptions import SpanPanelAdapterMissingError, SpanPanelSchemaVersionError
from span_panel_api.dispatch import select_adapter_key
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import MINIMAL_DESCRIPTION, SERIAL, TOPIC_PREFIX_SERIAL, flat_schema, parent_child_schema


def test_absent_data_model_version_selects_schema_zero() -> None:
    key, reason = select_adapter_key(None)
    assert key == "schema_0"
    assert "absent" in reason


@pytest.mark.parametrize("dmv", ["1.0", "1.4", "2.0", "1.0.3", "10.2"])
def test_present_data_model_version_requests_a_numbered_adapter(dmv: str) -> None:
    key, reason = select_adapter_key(dmv)
    assert key == f"schema_{dmv.split('.')[0]}"
    assert dmv in reason


@pytest.mark.parametrize("dmv", ["1", "1.0-beta", "1.0.3-rc2", "2_0"])
def test_non_canonical_but_unambiguous_versions_dispatch_on_their_major(dmv: str) -> None:
    """The major was read, not assumed, so dispatching on it is not a guess.

    Refusing these would take a panel offline over a formatting difference; the
    deviation is logged instead so a new firmware format is visible early.
    """
    key, reason = select_adapter_key(dmv)
    assert key == f"schema_{dmv[0]}"
    assert "non-canonical" in reason


@pytest.mark.parametrize("dmv", ["", "v1.0", "unknown", "beta", "-1", " 1.0"])
def test_unreadable_data_model_version_raises_instead_of_assuming_flat(dmv: str) -> None:
    """The regression this guards: a present-but-unreadable version must never
    reach the flat parser.

    Falling back to schema_0 does not fail — it silently produces plausible but
    wrong power and energy values in Home Assistant, which is strictly worse
    than an error the user can see and report.
    """
    with pytest.raises(SpanPanelSchemaVersionError) as exc:
        select_adapter_key(dmv)

    assert exc.value.data_model_version == dmv


def test_absence_is_still_a_supported_signal_not_an_error() -> None:
    """The flat schema predates the property, so absence must stay non-fatal —
    it is the single most common case in the field today."""
    key, _ = select_adapter_key(None)
    assert key == "schema_0"


def test_the_flat_key_is_the_one_the_transport_resolves() -> None:
    """Dispatch and the transport's default path must name the same adapter.

    They are the two callers of resolve_adapter, and a divergence between them
    is invisible in a dev workspace where every adapter is installed: it only
    appears as an unresolvable key in a real install.
    """
    from span_panel_api.adapters import DEFAULT_ADAPTER_KEY

    key, _ = select_adapter_key(None)
    assert key == DEFAULT_ADAPTER_KEY


def test_missing_adapter_raises_with_the_installed_list() -> None:
    """A panel whose schema outruns the install.

    Asks for a major nothing provides rather than `schema_1`, which this
    workspace now installs. The assertion is about the shape of the failure —
    named, with the installed set — not about which adapters happen to be
    absent today.
    """
    from span_panel_api.adapters import resolve_adapter

    _reset_adapter_cache()
    with pytest.raises(SpanPanelAdapterMissingError) as exc:
        resolve_adapter("schema_2", "data-model-version='2.0'")

    assert exc.value.needed == "schema_2"
    assert "schema_0" in exc.value.available
    assert "schema_1" in exc.value.available


# ---------------------------------------------------------------------------
# create_span_client — wiring the selected adapter class into SpanMqttClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_span_client_wires_schema_zero_adapter_and_diagnostics() -> None:
    """The factory must pass the resolved adapter *class* as adapter_factory,
    and assign the dispatch diagnostics onto the constructed client before
    connect() runs."""
    from span_panel_api.factory import create_span_client

    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")

    schema = flat_schema(32)
    with (
        patch("span_panel_api.factory.SpanMqttClient") as mock_cls,
        patch("span_panel_api.factory.get_homie_schema", return_value=schema) as mock_fetch,
    ):
        mock_client = mock_cls.return_value
        mock_client.connect = AsyncMock()

        result = await create_span_client(
            "192.168.1.1",
            mqtt_config=config,
            serial_number="test-serial",
        )

    # Dispatch happens before the client exists, so the schema is fetched by the
    # factory rather than by connect(). That ordering is the whole fix: the
    # adapter cannot be chosen from a value that has not been read yet.
    mock_fetch.assert_awaited_once()

    assert result is mock_client
    _, kwargs = mock_cls.call_args
    assert kwargs["adapter_factory"] is SchemaZeroAdapter
    # The fetched schema is handed to the client so connect() does not
    # re-request the same unauthenticated endpoint for a value that cannot
    # have changed between the two calls.
    assert kwargs["schema"] is schema
    mock_client.connect.assert_awaited_once()
    # Diagnostics travel through the constructor, so they are true before
    # connect() rather than patched onto private state afterwards. There is no
    # longer a window where a connected client reports a selected adapter next
    # to schema_dispatch_reason='not dispatched'.
    assert kwargs["data_model_version"] is None
    assert "absent" in kwargs["schema_dispatch_reason"]


# ---------------------------------------------------------------------------
# SpanMqttClient diagnostics properties — before and after connect()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_properties_before_and_after_connect(mqtt_client_mock: MagicMock) -> None:
    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

    # Before connect(): no adapter yet. Defaults describe a client built
    # directly, bypassing create_span_client.
    assert client.adapter is None
    assert client.schema_major is None
    assert client.data_model_version is None
    assert client.schema_dispatch_reason == "not dispatched"
    assert "schema_0" in client.installed_adapters

    # Simulate what create_span_client does after adapter selection, ahead of connect().
    client._data_model_version = None  # pylint: disable=protected-access
    client._schema_dispatch_reason = "data-model-version absent (flat schema)"  # pylint: disable=protected-access

    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0.05)
    client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
    client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
    await asyncio.wait_for(connect_task, timeout=5.0)

    assert isinstance(client.adapter, SchemaZeroAdapter)
    assert client.schema_major == "schema_0"
    assert client.data_model_version is None
    assert client.schema_dispatch_reason == "data-model-version absent (flat schema)"

    await client.close()


# ---------------------------------------------------------------------------
# Live dispatch — the version is now read, not assumed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_parent_child_panel_gets_the_parent_child_parser() -> None:
    """The bug Part A closed, now that the parser it asks for exists.

    Before, `create_span_client` hardcoded `data_model_version = None`, so a
    panel reporting `1.0` was handed to the flat parser regardless of what it
    said. What that cost: the flat parser reaches for
    `energy.ebus.device.circuit/space`, which a parent/child payload keeps
    under `deviceClasses`, and the run dies on

        ValueError: Schema missing 'energy.ebus.device.circuit/space' property

    — a message about a missing property, for a panel whose real problem was
    that nothing installed could parse it. Until `schema_1` registered, such a
    panel was refused by name; now the name resolves, and this pins that it
    resolves to the parent/child parser rather than quietly to the flat one.
    """
    from span_panel_api.factory import create_span_client

    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    schema = parent_child_schema()
    with (
        patch("span_panel_api.factory.SpanMqttClient") as mock_cls,
        patch("span_panel_api.factory.get_homie_schema", return_value=schema),
    ):
        mock_cls.return_value.connect = AsyncMock()
        await create_span_client("192.168.1.1", mqtt_config=config, serial_number="test-serial")

    _, kwargs = mock_cls.call_args
    assert kwargs["adapter_factory"] is SchemaOneAdapter
    assert kwargs["data_model_version"] == "1.0"
    assert "1.0" in kwargs["schema_dispatch_reason"]


@pytest.mark.asyncio
async def test_a_panel_newer_than_the_install_is_refused_rather_than_parsed_as_flat() -> None:
    """The other half of the same guarantee.

    A schema major nothing provides must be refused by name, not fall back to
    whichever parser happens to be installed — which is the failure the flat
    default used to produce, one major later.
    """
    from span_panel_api.factory import create_span_client

    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    with (
        patch("span_panel_api.factory.get_homie_schema", return_value=parent_child_schema("2.0")),
        pytest.raises(SpanPanelAdapterMissingError) as exc,
    ):
        await create_span_client("192.168.1.1", mqtt_config=config, serial_number="test-serial")

    assert exc.value.needed == "schema_2"
    assert "schema_1" in exc.value.available


@pytest.mark.asyncio
async def test_a_directly_constructed_client_dispatches_too() -> None:
    """Building a client directly must not bypass dispatch.

    `create_span_client` is not the only way to get a client — the README
    documents direct construction, and the integration uses it. Before, that
    path always resolved the flat adapter, so it carried exactly the bug the
    factory path just had fixed. Dispatch now happens wherever a parser is
    built, which is what makes handing this client a 1.x schema produce a
    parent/child parser rather than a flat one.
    """
    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    client = SpanMqttClient("192.168.1.1", SERIAL, config)

    client._build_adapter(parent_child_schema())

    assert isinstance(client.adapter, SchemaOneAdapter)
    assert client.schema_major == "schema_1"
    assert client.data_model_version == "1.0"


def test_dispatch_records_what_it_read_on_the_client() -> None:
    """Diagnostics for a directly-constructed client are filled in by dispatch
    rather than left saying 'not dispatched' after a parser exists."""
    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    client = SpanMqttClient("192.168.1.1", SERIAL, config)

    assert client.schema_dispatch_reason == "not dispatched"

    client._build_adapter(flat_schema(32))

    assert client.data_model_version is None
    assert "absent" in client.schema_dispatch_reason
    assert client.schema_major == "schema_0"


def test_an_unrecognised_enum_value_is_passed_through_not_raised() -> None:
    """The mirror image of the version rule, and deliberately so.

    v1.0 requires consumers not to raise on an unrecognised value in a
    `$format`-extended enum: SPAN may add enum members without a major bump, so
    raising would take a panel offline over a value the spec allows. Dispatch
    takes the opposite line on `data-model-version` because the blast radius
    differs — an unknown enum member affects one property, while an unknown
    schema version means every value in the tree may be misread.

    Pinned here because both rules live one import apart, and schema_1 inherits
    this one.
    """
    from span_panel_api_schema_0.accumulator import HomiePropertyAccumulator
    from span_panel_api_schema_0.consumer import HomieDeviceConsumer

    accumulator = HomiePropertyAccumulator(SERIAL)
    consumer = HomieDeviceConsumer(accumulator, panel_size=32)

    consumer.handle_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
    consumer.handle_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
    # A shed-priority value no released firmware emits today.
    consumer.handle_message(f"{TOPIC_PREFIX_SERIAL}/core/shed-priority", "SOME_FUTURE_PRIORITY")

    snapshot = consumer.build_snapshot()
    assert snapshot is not None
