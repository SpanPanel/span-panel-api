"""Writing to an adopted property, and the ways that write refuses.

The write exists so a control on a device nobody modelled is usable rather than
decorative. What matters here is the refusals: the write must not become a
generic one, because a generic write puts every curated setter one argument away
-- including the two that do real work on the way out, the islanding assertion
that translates its value and the charge ceiling that refuses one above what the
charger was commissioned for.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from span_panel_api.exceptions import SpanPanelServerError
from span_panel_api.models import AdoptedDevice, AdoptedProperty
from span_panel_api.mqtt import MqttClientConfig
from span_panel_api.mqtt.client import SpanMqttClient

SERIAL = "sp3-242424-001"
DEVICE = "generator-1"

CONTROL = AdoptedProperty(
    node_id="generator",
    property_id="mode",
    datatype="enum",
    format="AUTO,MANUAL,OFF",
    settable=True,
    value="AUTO",
    set_topic=f"ebus/5/{DEVICE}/generator/mode/set",
)

READING = AdoptedProperty(node_id="meter", property_id="active-power", datatype="float", unit="W", value="2400")


def _client(*properties: AdoptedProperty) -> tuple[SpanMqttClient, MagicMock]:
    """A client whose adapter reports one adopted device carrying `properties`."""
    config = MqttClientConfig(broker_host="h", username="u", password="p")
    client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

    adapter = MagicMock()
    adapter.build_snapshot.return_value = MagicMock(
        adopted_devices=(AdoptedDevice(device_id=DEVICE, device_type="energy.ebus.device.generator", properties=properties),)
    )
    client._adapter = adapter
    bridge = MagicMock()
    client._bridge = bridge
    return client, bridge


@pytest.mark.asyncio
async def test_a_settable_adopted_property_publishes_to_its_own_topic() -> None:
    """The value passes through unchanged, which is the honest thing to do.

    This library knows nothing about an adopted property beyond its declaration,
    so translating or bounding the value would be inventing a fact about somebody
    else's hardware. The caller constrains it to the declared format; the panel
    stays the authority on whether to accept it.
    """
    client, bridge = _client(CONTROL, READING)

    await client.set_adopted_property(DEVICE, "generator", "mode", "OFF")

    bridge.publish.assert_called_once_with(f"ebus/5/{DEVICE}/generator/mode/set", "OFF", qos=1)


@pytest.mark.asyncio
async def test_a_property_carrying_no_set_topic_is_refused() -> None:
    """A reading is not writable, and the absence of a topic is what says so."""
    client, bridge = _client(CONTROL, READING)

    with pytest.raises(SpanPanelServerError, match="No settable adopted property"):
        await client.set_adopted_property(DEVICE, "meter", "active-power", "0")

    bridge.publish.assert_not_called()


@pytest.mark.asyncio
async def test_a_property_no_adopted_device_declares_is_refused() -> None:
    """Arguments do not authorise the write; the snapshot does."""
    client, bridge = _client(CONTROL)

    with pytest.raises(SpanPanelServerError):
        await client.set_adopted_property(DEVICE, "generator", "invented", "OFF")

    bridge.publish.assert_not_called()


@pytest.mark.asyncio
async def test_a_device_the_adapter_models_cannot_be_addressed_through_this() -> None:
    """The whole reason the lookup is the authorisation.

    A circuit declares `switch/relay` settable and has a curated setter that owns
    it. Spelling the circuit's id here reaches no `AdoptedDevice`, so there is
    nothing to publish to -- not because a check rejected it, but because a
    modelled device produces no adopted record to find.
    """
    client, bridge = _client(CONTROL)

    with pytest.raises(SpanPanelServerError):
        await client.set_adopted_property("aabbccdd112233445566778899001122", "switch", "relay", "OPEN")

    bridge.publish.assert_not_called()


@pytest.mark.asyncio
async def test_a_device_that_has_left_the_tree_stops_being_writable() -> None:
    """Resolved against the current snapshot each time rather than cached.

    A control for a device that is no longer there must refuse rather than
    publish into a topic nothing subscribes to.
    """
    client, bridge = _client(CONTROL)
    client._adapter.build_snapshot.return_value = MagicMock(adopted_devices=())

    with pytest.raises(SpanPanelServerError):
        await client.set_adopted_property(DEVICE, "generator", "mode", "OFF")

    bridge.publish.assert_not_called()


def _two_generators() -> tuple[SpanMqttClient, MagicMock]:
    """Two adopted devices of one unmodelled type, each declaring the same control.

    The realistic shape, and the one the single-device fixtures above cannot
    exercise: nothing about adoption limits a panel to one generator, and two of a
    kind is exactly when a device id stops being decoration.
    """
    config = MqttClientConfig(broker_host="h", username="u", password="p")
    client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

    def control(device_id: str) -> AdoptedProperty:
        return AdoptedProperty(
            node_id="generator",
            property_id="mode",
            datatype="enum",
            format="AUTO,MANUAL,OFF",
            settable=True,
            value="AUTO",
            set_topic=f"ebus/5/{device_id}/generator/mode/set",
        )

    adapter = MagicMock()
    adapter.build_snapshot.return_value = MagicMock(
        adopted_devices=tuple(
            AdoptedDevice(
                device_id=device_id,
                device_type="energy.ebus.device.generator",
                properties=(control(device_id),),
            )
            for device_id in ("generator-1", "generator-2")
        )
    )
    client._adapter = adapter
    bridge = MagicMock()
    client._bridge = bridge
    return client, bridge


@pytest.mark.asyncio
async def test_the_write_reaches_the_device_that_was_named() -> None:
    """The device id is the authorization, not a label on it.

    The lookup returns the first device carrying the node and property asked for,
    so without the id filter a write aimed at the second generator publishes to
    the first one's topic -- the panel accepts it, and the wrong machine changes
    mode. Every other test here uses a single adopted device, where the filter
    cannot be wrong because there is nothing else to match.
    """
    client, bridge = _two_generators()

    await client.set_adopted_property("generator-2", "generator", "mode", "MANUAL")

    topic, payload = bridge.publish.call_args[0][:2]
    assert topic == "ebus/5/generator-2/generator/mode/set"
    assert payload == "MANUAL"


@pytest.mark.asyncio
async def test_a_device_that_is_not_adopted_is_refused_even_when_a_sibling_declares_the_property() -> None:
    """The property existing somewhere is not the property existing here."""
    client, bridge = _two_generators()

    with pytest.raises(SpanPanelServerError):
        await client.set_adopted_property("generator-3", "generator", "mode", "MANUAL")

    bridge.publish.assert_not_called()
