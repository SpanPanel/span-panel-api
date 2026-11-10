"""Locking and unlocking a charger's connector, and the ways that refuses.

The write is authorised by the snapshot: the charger must carry
`lock_control`, which the adapter sets only where `switch/lock-state` is
declared settable, and the value must be one the declaration's `$format` lists.
The values themselves are the ones the panel publishes, pinned here to a
reference capture's declaration rather than assumed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from conftest import FAST_CONTROL_DEADLINES, acking_bridge
from reference_payloads.captures import CAPTURES, capture_tree
from span_panel_api import EvseLockControlProtocol
from span_panel_api.exceptions import SpanPanelServerError
from span_panel_api.models import ControlTarget, SpanEvseSnapshot
from span_panel_api.mqtt import MqttClientConfig
from span_panel_api.mqtt.client import LOCK_STATE_LOCKED, LOCK_STATE_UNLOCKED, SpanMqttClient

SERIAL = "panel-a"
CHARGER = "evse-a-serial"
TARGET = ControlTarget(
    topic="ebus/5/evse-a/switch/lock-state/set", device_id="evse-a", node_id="switch", property_id="lock-state"
)


def _client(charger: SpanEvseSnapshot) -> tuple[SpanMqttClient, MagicMock]:
    """A client whose adapter reports one charger, under the key `CHARGER`."""
    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="h", username="u", password="p"),
        control_deadlines=FAST_CONTROL_DEADLINES,
    )
    adapter = MagicMock()
    adapter.build_snapshot.return_value = MagicMock(evse={CHARGER: charger})
    client._adapter = adapter
    bridge = acking_bridge()
    client._bridge = bridge
    return client, bridge


def _charger(
    *, target: ControlTarget | None = TARGET, options: tuple[str, ...] | None = ("UNLOCKED", "LOCKED")
) -> SpanEvseSnapshot:
    return SpanEvseSnapshot(node_id=CHARGER, feed_circuit_id="c-1", lock_control=target, lock_state_options=options)


def _declared_lock_formats() -> set[str]:
    formats: set[str] = set()
    for stem in CAPTURES:
        tree = capture_tree(stem)
        for device in tree.devices.values():
            declaration = json.loads(device.description)["nodes"].get("switch", {}).get("properties", {}).get("lock-state")
            if declaration is not None:
                formats.add(declaration["format"])
    return formats


def test_the_payloads_are_the_values_the_panel_declares() -> None:
    formats = _declared_lock_formats()

    assert formats, "no capture declares a charger lock"
    for declared in formats:
        assert {LOCK_STATE_LOCKED, LOCK_STATE_UNLOCKED} <= set(declared.split(","))


def test_the_client_satisfies_the_protocol() -> None:
    assert issubclass(SpanMqttClient, EvseLockControlProtocol)


@pytest.mark.asyncio
@pytest.mark.parametrize(("locked", "payload"), [(True, LOCK_STATE_LOCKED), (False, LOCK_STATE_UNLOCKED)])
async def test_a_settable_lock_publishes_to_its_target(locked: bool, payload: str) -> None:
    client, bridge = _client(_charger())

    await client.set_evse_lock(CHARGER, locked)

    bridge.publish.assert_called_once_with(TARGET.topic, payload)


@pytest.mark.asyncio
async def test_a_lock_without_a_target_is_refused() -> None:
    client, bridge = _client(_charger(target=None))

    with pytest.raises(SpanPanelServerError, match="No settable lock"):
        await client.set_evse_lock(CHARGER, True)
    bridge.publish.assert_not_called()


@pytest.mark.asyncio
async def test_an_unknown_charger_is_refused() -> None:
    client, bridge = _client(_charger())

    with pytest.raises(SpanPanelServerError, match="No settable lock"):
        await client.set_evse_lock("another-charger", True)
    bridge.publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [("UNLOCKED",), None], ids=["not-in-format", "no-format"])
async def test_a_value_the_declaration_does_not_list_is_refused(options: tuple[str, ...] | None) -> None:
    client, bridge = _client(_charger(options=options))

    with pytest.raises(SpanPanelServerError, match="does not declare"):
        await client.set_evse_lock(CHARGER, True)
    bridge.publish.assert_not_called()
