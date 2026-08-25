"""One veto-and-observe point that every control command passes through.

The contract has four edges that each exist for a reason a test can state:
a veto's exception reaches the caller untranslated (the consumer raises a
framework error carrying a translated message and needs it intact); a refusal
still produces an audit record (an audit that omits refusals is worse than
none); the observation half is fired as a task (a sink that merely hangs must
not stall control); and the interceptor sees the refusals and the no-op, not
only the commands that reached the wire.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from span_panel_api.models import ControlTarget
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.control import ControlCommand, ControlDeadlines, ControlInterceptor, PublishOutcome, PublishState
from span_panel_api.mqtt.models import MqttClientConfig
from span_panel_api.protocol import ControlInterceptionProtocol

from conftest import SERIAL

CIRCUIT = "aabbccdd112233445566778899001122"
RELAY_TOPIC = f"ebus/5/{SERIAL}/{CIRCUIT}/relay/set"


class _Recorder:
    """An interceptor that records, and optionally refuses."""

    def __init__(self, veto: Exception | None = None, hang: bool = False) -> None:
        self.veto = veto
        self.hang = hang
        self.before: list[ControlCommand] = []
        self.after: list[tuple[ControlCommand, PublishOutcome]] = []

    async def before_publish(self, command: ControlCommand) -> None:
        self.before.append(command)
        if self.veto is not None:
            raise self.veto

    async def after_publish(self, command: ControlCommand, outcome: PublishOutcome) -> None:
        self.after.append((command, outcome))
        if self.hang:
            await asyncio.Event().wait()


class _Refusal(Exception):
    """Stands in for the consumer's own framework-specific error."""


def _client(*, connected: bool = True, deadline: float = 0.05) -> SpanMqttClient:
    client = SpanMqttClient(
        host="panel.invalid",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        control_deadlines=ControlDeadlines(relay=deadline),
    )
    adapter = MagicMock()
    adapter.set_circuit_relay_target.return_value = ControlTarget(
        topic=RELAY_TOPIC, device_id=SERIAL, node_id=CIRCUIT, property_id="relay"
    )
    adapter.register_property_callback.side_effect = lambda cb: lambda: None
    client._adapter = adapter
    client._observe(adapter)

    paho_client = MagicMock()
    paho_client.publish.return_value = MagicMock(rc=0, mid=11)
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
    client._bridge = bridge
    return client


async def _settle() -> None:
    """Let the fire-and-forget `after_publish` task run."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class TestRegistration:
    def test_the_client_satisfies_the_protocol(self) -> None:
        """The consumer codes against the protocol, never against the class."""
        client = SpanMqttClient(
            host="panel.invalid",
            serial_number=SERIAL,
            broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        )
        assert isinstance(client, ControlInterceptionProtocol)

    @pytest.mark.asyncio
    async def test_one_at_a_time_and_removable(self) -> None:
        client = _client()
        first, second = _Recorder(), _Recorder()

        client.set_control_interceptor(first)
        client.set_control_interceptor(second)
        await client.set_circuit_relay(CIRCUIT, "OPEN")

        assert first.before == []
        assert len(second.before) == 1

        client.set_control_interceptor(None)
        await client.set_circuit_relay(CIRCUIT, "CLOSED")
        assert len(second.before) == 1


class TestVeto:
    @pytest.mark.asyncio
    async def test_the_exception_reaches_the_caller_untranslated(self) -> None:
        client = _client()
        client.set_control_interceptor(_Recorder(veto=_Refusal("only admins may do that")))

        with pytest.raises(_Refusal, match="only admins may do that"):
            await client.set_circuit_relay(CIRCUIT, "OPEN")

    @pytest.mark.asyncio
    async def test_nothing_is_published(self) -> None:
        client = _client()
        client.set_control_interceptor(_Recorder(veto=_Refusal("no")))
        assert client._bridge is not None

        with pytest.raises(_Refusal):
            await client.set_circuit_relay(CIRCUIT, "OPEN")

        client._bridge._client.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_refusal_still_produces_an_audit_record(self) -> None:
        """An audit that silently omits refusals is worse than no audit."""
        client = _client()
        recorder = _Recorder(veto=_Refusal("no"))
        client.set_control_interceptor(recorder)

        with pytest.raises(_Refusal):
            await client.set_circuit_relay(CIRCUIT, "OPEN")
        await _settle()

        assert len(recorder.after) == 1
        _command, outcome = recorder.after[0]
        assert outcome.state is PublishState.FAILED
        assert outcome.detail == "vetoed"


class TestObservation:
    @pytest.mark.asyncio
    async def test_the_command_carries_the_wire_address_and_the_translated_value(self) -> None:
        client = _client()
        recorder = _Recorder()
        client.set_control_interceptor(recorder)

        await client.set_circuit_relay(CIRCUIT, "OPEN")

        command = recorder.before[0]
        assert command == ControlCommand(
            device_id=SERIAL,
            node_id=CIRCUIT,
            property_id="relay",
            value="OPEN",
            topic=RELAY_TOPIC,
        )

    @pytest.mark.asyncio
    async def test_a_refused_publish_is_seen_too(self) -> None:
        """Not only the commands that reached the wire -- the interesting ones do not."""
        client = _client(connected=False)
        recorder = _Recorder()
        client.set_control_interceptor(recorder)

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")
        await _settle()

        assert outcome.state is PublishState.FAILED
        assert len(recorder.before) == 1
        assert recorder.after[0][1].state is PublishState.FAILED

    @pytest.mark.asyncio
    async def test_a_no_op_is_seen_too(self) -> None:
        client = _client()
        recorder = _Recorder()
        client.set_control_interceptor(recorder)
        client._on_property_value(SERIAL, CIRCUIT, "relay", "OPEN")

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")
        await _settle()

        assert outcome.no_op is True
        assert len(recorder.before) == 1
        assert recorder.after[0][1].no_op is True

    @pytest.mark.asyncio
    async def test_a_hanging_sink_does_not_stall_control(self) -> None:
        """Awaiting `after_publish` would make a slow event bus a control outage."""
        client = _client()
        client.set_control_interceptor(_Recorder(hang=True))

        await asyncio.wait_for(client.set_circuit_relay(CIRCUIT, "OPEN"), timeout=1.0)

        # The hung task is tracked, so it is cancelled with the client rather
        # than garbage-collected mid-await.
        assert client._background_tasks
        await client.close()

    @pytest.mark.asyncio
    async def test_a_raising_sink_does_not_reach_the_caller(self, caplog: pytest.LogCaptureFixture) -> None:
        class _Exploding:
            async def before_publish(self, command: ControlCommand) -> None:
                return None

            async def after_publish(self, command: ControlCommand, outcome: PublishOutcome) -> None:
                raise RuntimeError("the audit sink is broken")

        client = _client()
        client.set_control_interceptor(_Exploding())

        outcome = await client.set_circuit_relay(CIRCUIT, "OPEN")
        await _settle()

        assert outcome.state is not PublishState.FAILED

    def test_a_conforming_object_satisfies_the_protocol(self) -> None:
        assert isinstance(_Recorder(), ControlInterceptor)
