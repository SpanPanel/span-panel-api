"""Control commands report what happened to them, and `FAILED` is a promise.

Three paths used to return `None` having published nothing, and a fourth --
publishing while the broker was down -- looked like a discard and was not: paho
queues a QoS-1 message across a disconnect and sends it when the connection
returns. That last one is why the refusal has to happen before paho sees the
message. Reading paho's return code afterwards would report a failure for a
breaker command that fires four minutes later, and a user told "failed" acts on
it.

So the assertions that matter here are as much about what is *not* claimed --
nothing is `FAILED` once it has been handed over, and nothing is queued when it
has not -- as about what is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import pytest

from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.control import ControlDeadlines, PublishOutcome, PublishState
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import SERIAL

CIRCUIT = "aabbccdd112233445566778899001122"
FAST = ControlDeadlines(relay=0.05, priority=0.05, dominant_power_source=0.05, evse_charge_limit=0.05, adopted_property=0.05)


def _client(*, deadlines: ControlDeadlines | None = None) -> SpanMqttClient:
    client = SpanMqttClient(
        host="panel.invalid",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        control_deadlines=deadlines or FAST,
    )
    adapter = MagicMock()
    adapter.set_circuit_relay_topic.return_value = f"ebus/5/{SERIAL}/{CIRCUIT}/relay/set"
    adapter.set_circuit_priority_topic.return_value = f"ebus/5/{SERIAL}/{CIRCUIT}/shed-priority/set"
    adapter.set_dominant_power_source_topic.return_value = f"ebus/5/{SERIAL}/core/dominant-power-source/set"
    adapter.dominant_power_source_payload.return_value = "BATTERY"
    adapter.set_evse_charge_limit_topic.return_value = "ebus/5/evse-1/config/user-max-charge-current/set"
    adapter.evse_charge_limit_payload.return_value = "24"
    client._adapter = adapter
    return client


def _bridge(*, connected: bool, paho_client: MagicMock | None) -> AsyncMqttBridge:
    bridge = AsyncMqttBridge(
        host="broker.local",
        port=8883,
        username="u",
        password="p",
        panel_host="panel.invalid",
        serial_number=SERIAL,
    )
    bridge._connected = connected
    bridge._client = paho_client
    bridge._loop = asyncio.get_event_loop()
    return bridge


def _paho() -> MagicMock:
    client = MagicMock()
    client.publish.return_value = MagicMock(rc=0, mid=7)
    return client


# ---------------------------------------------------------------------------
# The two refusals
# ---------------------------------------------------------------------------


class TestFailedMeansNeverDelivered:
    @pytest.mark.asyncio
    async def test_no_bridge_is_failed_not_a_silent_return(self) -> None:
        """`close()` clears the bridge and leaves the adapter, so this path passed
        `_require_adapter()` and returned `None` having done nothing."""
        client = _client()
        client._bridge = None

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")

        assert outcome.state is PublishState.FAILED
        assert outcome.value == "OPEN"
        assert outcome.detail is not None

    @pytest.mark.asyncio
    async def test_no_paho_client_is_failed(self) -> None:
        client = _client()
        client._bridge = _bridge(connected=False, paho_client=None)

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")

        assert outcome.state is PublishState.FAILED

    @pytest.mark.asyncio
    async def test_disconnected_is_failed_and_nothing_is_queued_in_paho(self) -> None:
        """The substantive fix. paho would have queued this and sent it later."""
        paho_client = _paho()
        client = _client()
        client._bridge = _bridge(connected=False, paho_client=paho_client)

        outcome = await client.set_circuit_relay(CIRCUIT, "CLOSED")

        assert outcome.state is PublishState.FAILED
        paho_client.publish.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.set_circuit_relay(CIRCUIT, "OPEN"),
            lambda c: c.set_circuit_priority(CIRCUIT, "NEVER"),
            lambda c: c.set_dominant_power_source("BATTERY"),
            lambda c: c.set_evse_charge_limit("evse-1", 24),
        ],
        ids=["relay", "priority", "dps", "evse"],
    )
    async def test_every_setter_refuses_while_down(
        self, call: Callable[[SpanMqttClient], Awaitable[PublishOutcome]]
    ) -> None:
        paho_client = _paho()
        client = _client()
        client._bridge = _bridge(connected=False, paho_client=paho_client)

        outcome = await call(client)

        assert outcome.state is PublishState.FAILED
        paho_client.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Everything past the gate
# ---------------------------------------------------------------------------


class TestHandedOver:
    @pytest.mark.asyncio
    async def test_puback_yields_accepted(self) -> None:
        paho_client = _paho()
        client = _client()
        bridge = _bridge(connected=True, paho_client=paho_client)
        client._bridge = bridge

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)  # let the publish register its waiter
        bridge._on_publish(paho_client, None, 7, MagicMock(), None)
        outcome = await task

        assert outcome.state is PublishState.ACCEPTED
        assert outcome.no_op is False
        paho_client.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_deadline_expiry_yields_unconfirmed_and_does_not_raise(self) -> None:
        """An unacknowledged write is not an error; it is most often a no-op write."""
        client = _client()
        client._bridge = _bridge(connected=True, paho_client=_paho())

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")

        assert outcome.state is PublishState.UNCONFIRMED
        assert outcome.detail is not None and "0.05" in outcome.detail

    @pytest.mark.asyncio
    async def test_rebuild_settles_in_flight_publishes_without_calling_them_failed(self) -> None:
        """A rebuilt paho client drops the outbound queue, but the original may
        already have reached the broker -- so this resolves, and does not claim
        the command will never be delivered."""
        client = _client(deadlines=ControlDeadlines(relay=5.0))
        bridge = _bridge(connected=True, paho_client=_paho())
        client._bridge = bridge

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        bridge._resolve_pending_publishes(False, "test rebuild")
        outcome = await task

        assert outcome.state is PublishState.UNCONFIRMED
        assert outcome.state is not PublishState.FAILED
        assert outcome.detail is not None and "rebuilt" in outcome.detail

    @pytest.mark.asyncio
    async def test_a_settled_publish_is_forgotten(self) -> None:
        """The pending map must not grow for the life of the bridge."""
        paho_client = _paho()
        bridge = _bridge(connected=True, paho_client=paho_client)

        acknowledged = bridge.publish("some/topic/set", "OPEN")
        assert acknowledged is not None
        assert bridge._pending_publishes == {7: acknowledged}

        bridge._on_publish(paho_client, None, 7, MagicMock(), None)
        await asyncio.sleep(0)
        assert bridge._pending_publishes == {}

    @pytest.mark.asyncio
    async def test_a_cancelled_waiter_is_forgotten(self) -> None:
        """The ordinary end for a message the broker never answers."""
        bridge = _bridge(connected=True, paho_client=_paho())

        acknowledged = bridge.publish("some/topic/set", "OPEN")
        assert acknowledged is not None
        acknowledged.cancel()
        await asyncio.sleep(0)

        assert bridge._pending_publishes == {}

    @pytest.mark.asyncio
    async def test_a_late_puback_after_the_deadline_does_not_explode(self) -> None:
        """`asyncio.wait_for` cancels the future; the PUBACK still arrives."""
        paho_client = _paho()
        client = _client()
        bridge = _bridge(connected=True, paho_client=paho_client)
        client._bridge = bridge

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")
        assert outcome.state is PublishState.UNCONFIRMED

        # Whatever paho does next must not raise out of the callback.
        bridge._on_publish(paho_client, None, 7, MagicMock(), None)


class TestDisconnectSettlesWaiters:
    @pytest.mark.asyncio
    async def test_teardown_does_not_leave_a_caller_waiting(self) -> None:
        client = _client(deadlines=ControlDeadlines(relay=5.0))
        bridge = _bridge(connected=True, paho_client=_paho())
        client._bridge = bridge

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        await bridge.disconnect()
        outcome = await task

        assert outcome.state is PublishState.UNCONFIRMED
