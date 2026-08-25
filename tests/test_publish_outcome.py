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

from span_panel_api.models import ControlTarget
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
    adapter.set_circuit_relay_target.return_value = ControlTarget(
        topic=f"ebus/5/{SERIAL}/{CIRCUIT}/relay/set", device_id=SERIAL, node_id=CIRCUIT, property_id="relay"
    )
    adapter.set_circuit_priority_target.return_value = ControlTarget(
        topic=f"ebus/5/{SERIAL}/{CIRCUIT}/shed-priority/set", device_id=SERIAL, node_id=CIRCUIT, property_id="shed-priority"
    )
    adapter.set_dominant_power_source_target.return_value = ControlTarget(
        topic=f"ebus/5/{SERIAL}/core/dominant-power-source/set",
        device_id=SERIAL,
        node_id="core",
        property_id="dominant-power-source",
    )
    adapter.dominant_power_source_payload.return_value = "BATTERY"
    adapter.set_evse_charge_limit_target.return_value = ControlTarget(
        topic="ebus/5/evse-1/config/user-max-charge-current/set",
        device_id="evse-1",
        node_id="config",
        property_id="user-max-charge-current",
    )
    adapter.evse_charge_limit_payload.return_value = "24"
    # One observer is registered per adapter in `_build_adapter`; these clients
    # never connect, so wire it here or nothing feeds the no-op check.
    adapter.register_property_callback.side_effect = lambda cb: lambda: None
    client._adapter = adapter
    client._observe(adapter)
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

        started = asyncio.get_running_loop().time()
        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)  # let the publish register its waiter
        bridge._on_publish(paho_client, None, 7, MagicMock(), None)
        outcome = await task

        assert outcome.state is PublishState.ACCEPTED
        assert outcome.no_op is False
        paho_client.publish.assert_called_once()
        # A PUBACK must NOT end the wait. The broker taking the message says
        # nothing about the panel acting on it, so the deadline still has to run
        # its course -- otherwise every write would report ACCEPTED the instant
        # the broker answered and a transition arriving later would go unseen.
        assert asyncio.get_running_loop().time() - started >= FAST.relay

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
        the command will never be delivered.

        The deadline here is a realistic relay deadline rather than a fast one,
        because the point is that it is never reached: a discarded message ends
        the wait immediately. Waiting it out would be five seconds spent on a
        transport that had already thrown the message away.
        """
        client = _client(deadlines=ControlDeadlines(relay=5.0))
        bridge = _bridge(connected=True, paho_client=_paho())
        client._bridge = bridge

        started = asyncio.get_running_loop().time()
        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        bridge._resolve_pending_publishes(False, "test rebuild")
        outcome = await task
        elapsed = asyncio.get_running_loop().time() - started

        assert outcome.state is PublishState.UNCONFIRMED
        assert outcome.state is not PublishState.FAILED
        assert outcome.detail is not None and "rebuilt" in outcome.detail
        # Generous enough not to be flaky on a loaded machine, and still two
        # orders of magnitude below the deadline it would otherwise have burnt.
        assert elapsed < 1.0

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
        client = _client(deadlines=ControlDeadlines(relay=0.2))
        bridge = _bridge(connected=True, paho_client=_paho())
        client._bridge = bridge

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        await bridge.disconnect()
        outcome = await task

        assert outcome.state is PublishState.UNCONFIRMED


# ---------------------------------------------------------------------------
# Write-then-verify
# ---------------------------------------------------------------------------


class TestWriteThenVerify:
    """The panel reporting the value back is the only thing that says it landed."""

    @staticmethod
    def _observe(client: SpanMqttClient, target: ControlTarget, value: str) -> None:
        """Feed the observation stream as the adapter would."""
        client._on_property_value(target.device_id, target.node_id, target.property_id, value)

    @pytest.mark.asyncio
    async def test_a_transition_yields_confirmed(self) -> None:
        paho_client = _paho()
        client = _client(deadlines=ControlDeadlines(relay=1.0))
        bridge = _bridge(connected=True, paho_client=paho_client)
        client._bridge = bridge
        target = client._adapter.set_circuit_relay_target(CIRCUIT)

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        self._observe(client, target, "OPEN")
        outcome = await task

        assert outcome.state is PublishState.CONFIRMED
        assert outcome.no_op is False

    @pytest.mark.asyncio
    async def test_a_transition_to_some_other_value_is_not_a_confirmation(self) -> None:
        """A racing external change is not evidence that this write landed."""
        client = _client()
        client._bridge = _bridge(connected=True, paho_client=_paho())
        target = client._adapter.set_circuit_relay_target(CIRCUIT)

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        self._observe(client, target, "CLOSED")
        outcome = await task

        assert outcome.state is not PublishState.CONFIRMED

    @pytest.mark.asyncio
    async def test_puback_without_a_transition_is_accepted(self) -> None:
        paho_client = _paho()
        client = _client()
        bridge = _bridge(connected=True, paho_client=paho_client)
        client._bridge = bridge

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        bridge._on_publish(paho_client, None, 7, MagicMock(), None)
        outcome = await task

        assert outcome.state is PublishState.ACCEPTED
        assert outcome.detail is not None and "no transition" in outcome.detail

    @pytest.mark.asyncio
    async def test_nothing_at_all_is_unconfirmed(self) -> None:
        client = _client()
        client._bridge = _bridge(connected=True, paho_client=_paho())

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")

        assert outcome.state is PublishState.UNCONFIRMED
        assert outcome.no_op is False

    @pytest.mark.asyncio
    async def test_a_no_op_short_circuits_without_publishing(self) -> None:
        """A write whose value is already current would burn the whole deadline."""
        paho_client = _paho()
        client = _client(deadlines=ControlDeadlines(relay=30.0))
        client._bridge = _bridge(connected=True, paho_client=paho_client)
        target = client._adapter.set_circuit_relay_target(CIRCUIT)
        self._observe(client, target, "OPEN")

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")

        assert outcome.state is PublishState.UNCONFIRMED
        assert outcome.no_op is True
        paho_client.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_no_op_check_compares_wire_vocabulary(self) -> None:
        """The caller says BATTERY; the wire says OFF_GRID under v1.0.

        Comparing the caller's string against the observed one would compare two
        different vocabularies, never match, and burn a deadline on every
        repeated write.
        """
        paho_client = _paho()
        client = _client(deadlines=ControlDeadlines(dominant_power_source=30.0))
        client._bridge = _bridge(connected=True, paho_client=paho_client)
        client._adapter.dominant_power_source_payload.return_value = "OFF_GRID"
        target = client._adapter.set_dominant_power_source_target()
        self._observe(client, target, "OFF_GRID")

        outcome = await client.set_dominant_power_source("BATTERY")

        assert outcome.no_op is True
        assert outcome.value == "OFF_GRID"
        paho_client.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_different_property_does_not_confirm_this_one(self) -> None:
        client = _client()
        client._bridge = _bridge(connected=True, paho_client=_paho())

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        client._on_property_value(SERIAL, "some-other-circuit", "relay", "OPEN")
        outcome = await task

        assert outcome.state is not PublishState.CONFIRMED

    @pytest.mark.asyncio
    async def test_a_confirmed_write_leaves_nothing_pending(self) -> None:
        """Neither the verification list nor the bridge's publish map may grow."""
        paho_client = _paho()
        client = _client(deadlines=ControlDeadlines(relay=1.0))
        bridge = _bridge(connected=True, paho_client=paho_client)
        client._bridge = bridge
        target = client._adapter.set_circuit_relay_target(CIRCUIT)

        task = asyncio.ensure_future(client.set_circuit_relay(CIRCUIT, "OPEN"))
        await asyncio.sleep(0)
        self._observe(client, target, "OPEN")
        assert (await task).state is PublishState.CONFIRMED
        await asyncio.sleep(0)

        assert client._verifications == []
        assert bridge._pending_publishes == {}

    @pytest.mark.asyncio
    async def test_swapping_the_adapter_drops_stale_observations(self) -> None:
        """The values describe a tree that is being replaced."""
        client = _client()
        target = client._adapter.set_circuit_relay_target(CIRCUIT)
        self._observe(client, target, "OPEN")
        assert client._observed_values

        replacement = MagicMock()
        replacement.register_property_callback.side_effect = lambda cb: lambda: None
        client._observe(replacement)

        assert client._observed_values == {}
