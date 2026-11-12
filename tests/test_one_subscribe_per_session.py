"""Every broker session is subscribed exactly once, whichever path opened it.

Under MQTT a SUBSCRIBE makes the broker deliver every retained message on its
filter, so each one is a replay of the panel's whole tree. A broker logging every
packet showed the initial connection send two identical SUBSCRIBEs and receive a
1,527-topic tree twice: one from the connection callback, one from `connect()`.

The connection is clean-session, so the broker holds nothing from an earlier
session and every new one must be subscribed again -- once. These tests drive the
real `SpanMqttClient` and `AsyncMqttBridge` against a fake paho client and a fake
broker that behaves as mosquitto does: a SUBSCRIBE replays every retained message
on each of its filters, a publish is delivered once per matching filter, and a
subscribe or unsubscribe on a closed socket reaches nobody (paho answers it with
`MQTT_ERR_NO_CONN`). What they count is what the broker received on each session
and what it sent back, which is what the packet log measured.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
import ssl
from unittest.mock import patch

from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage, topic_matches_sub
from paho.mqtt.enums import CallbackAPIVersion, MQTTErrorCode
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
import pytest

from span_panel_api.models import V2HomieSchema
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig, MqttTransport
from span_panel_api_schema_0.const import WILDCARD_TOPIC_FMT

from conftest import MINIMAL_DESCRIPTION, SERIAL, TOPIC_PREFIX_SERIAL, flat_schema, parent_child_schema

_FLAT_FILTER = WILDCARD_TOPIC_FMT.format(serial=SERIAL)
"""What the flat parser subscribes to: the panel's own device."""

_PARENT_CHILD_FILTER = "ebus/5/#"
"""What the parent/child parser subscribes to: every device, since children are the panel's peers."""

_DATA_MODEL_VERSION_TOPIC = f"{TOPIC_PREFIX_SERIAL}/info/data-model-version"

_FLAT_TREE: dict[str, str] = {
    f"{TOPIC_PREFIX_SERIAL}/$description": MINIMAL_DESCRIPTION,
    f"{TOPIC_PREFIX_SERIAL}/$state": "ready",
    **{f"{TOPIC_PREFIX_SERIAL}/core/property-{index}": str(index) for index in range(8)},
}
"""A retained tree the flat parser reads as ready: described first, then `$state`."""


@dataclass
class _Session:
    """One network connection, as the broker saw it."""

    subscribed: list[str] = field(default_factory=list)
    """Each topic filter of each SUBSCRIBE received, in order."""
    unsubscribed: list[str] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    """The topic of every PUBLISH the broker sent on this session."""
    filters: list[str] = field(default_factory=list)
    """The filters currently held for this session."""
    open: bool = True


ConnackHandler = Callable[["_FakePaho", object, ConnectFlags, ReasonCode, Properties | None], None]
DisconnectHandler = Callable[["_FakePaho", object, DisconnectFlags, ReasonCode, Properties | None], None]
MessageHandler = Callable[["_FakePaho", object, MQTTMessage], None]


class _Broker:
    """The retained tree, the sessions opened against it, and the clients that opened them."""

    def __init__(self, loop: asyncio.AbstractEventLoop, retained: dict[str, str]) -> None:
        self.loop = loop
        self.retained = dict(retained)
        self.sessions: list[_Session] = []
        self.clients: list[_FakePaho] = []
        self.refusing = False
        """While set, every connect and reconnect is refused, as a broker that is down refuses them."""
        self.pending = 0
        """Deliveries scheduled and not yet made."""

    def client(
        self, *, callback_api_version: CallbackAPIVersion, transport: MqttTransport, reconnect_on_failure: bool
    ) -> _FakePaho:
        """Stands in for `AsyncMQTTClient(...)`: the bridge builds one per connect and per rebuild."""
        assert callback_api_version is CallbackAPIVersion.VERSION2
        assert transport == "tcp"
        assert reconnect_on_failure is False
        built = _FakePaho(self)
        self.clients.append(built)
        return built

    def open_session(self) -> _Session:
        session = _Session()
        self.sessions.append(session)
        return session

    def subscribe(self, client: _FakePaho, session: _Session, filters: list[str]) -> None:
        for topic_filter in filters:
            session.subscribed.append(topic_filter)
            if topic_filter not in session.filters:
                session.filters.append(topic_filter)
            for topic, payload in self.retained.items():
                if topic_matches_sub(topic_filter, topic):
                    self._schedule(client, session, topic, payload)

    def unsubscribe(self, session: _Session, filters: list[str]) -> None:
        for topic_filter in filters:
            session.unsubscribed.append(topic_filter)
            if topic_filter in session.filters:
                session.filters.remove(topic_filter)

    def publish(self, topic: str, payload: str) -> None:
        """A retained publish from the panel, delivered once per matching filter of every open session."""
        self.retained[topic] = payload
        for client in self.clients:
            session = client.session
            if session is None or not session.open:
                continue
            for topic_filter in session.filters:
                if topic_matches_sub(topic_filter, topic):
                    self._schedule(client, session, topic, payload)

    def _schedule(self, client: _FakePaho, session: _Session, topic: str, payload: str) -> None:
        self.pending += 1
        self.loop.call_soon_threadsafe(self._deliver, client, session, topic, payload)

    def _deliver(self, client: _FakePaho, session: _Session, topic: str, payload: str) -> None:
        self.pending -= 1
        if not session.open or client.on_message is None:
            return
        session.delivered.append(topic)
        message = MQTTMessage(topic=topic.encode("utf-8"))
        message.payload = payload.encode("utf-8")
        client.on_message(client, None, message)


class _FakePaho:
    """The slice of paho the bridge drives, opening sessions on a `_Broker` instead of a socket."""

    def __init__(self, broker: _Broker) -> None:
        self._broker = broker
        self.session: _Session | None = None
        self.next_reconnect_error: OSError | ssl.SSLError | None = None
        self.on_connect: ConnackHandler | None = None
        self.on_disconnect: DisconnectHandler | None = None
        self.on_message: MessageHandler | None = None
        # Assigned by the bridge and never called: no real socket is opened.
        self.on_publish: object = None
        self.on_socket_open: object = None
        self.on_socket_close: object = None
        self.on_socket_register_write: object = None
        self.on_socket_unregister_write: object = None

    def setup(self) -> None:
        """The lock swap `AsyncMQTTClient` does; nothing to swap here."""

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        """Credentials are not checked by this broker."""

    def connect(self, host: str, port: int = 1883, keepalive: int = 60) -> MQTTErrorCode:
        """Open a session and schedule its CONNACK, as paho's executor connect does."""
        assert host and port > 0 and keepalive > 0
        self._open()
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def reconnect(self) -> MQTTErrorCode:
        """paho closes the previous socket without reporting a disconnect, then opens another."""
        error = self.next_reconnect_error
        self.next_reconnect_error = None
        self._close()
        if error is not None:
            raise error
        self._open()
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def subscribe(self, topic: str | list[tuple[str, int]], qos: int = 0) -> tuple[MQTTErrorCode, int | None]:
        assert qos == 0
        session = self.session
        if session is None or not session.open:
            return (MQTTErrorCode.MQTT_ERR_NO_CONN, None)
        self._broker.subscribe(self, session, [topic] if isinstance(topic, str) else [name for name, _ in topic])
        return (MQTTErrorCode.MQTT_ERR_SUCCESS, 1)

    def unsubscribe(self, topic: str | list[str]) -> tuple[MQTTErrorCode, int | None]:
        session = self.session
        if session is None or not session.open:
            return (MQTTErrorCode.MQTT_ERR_NO_CONN, None)
        self._broker.unsubscribe(session, [topic] if isinstance(topic, str) else list(topic))
        return (MQTTErrorCode.MQTT_ERR_SUCCESS, 1)

    def disconnect(self) -> MQTTErrorCode:
        self._close()
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def drop(self) -> None:
        """The network goes away under an open session: paho closes the socket and reports it."""
        self._close()
        if self.on_disconnect is not None:
            self.on_disconnect(
                self,
                None,
                DisconnectFlags(is_disconnect_packet_from_server=False),
                ReasonCode(PacketTypes.DISCONNECT, "Unspecified error"),
                None,
            )

    def _open(self) -> None:
        if self._broker.refusing:
            raise ConnectionRefusedError(61, "Connection refused")
        session = self._broker.open_session()
        self.session = session
        self._broker.loop.call_soon_threadsafe(self._connack, session)

    def _connack(self, session: _Session) -> None:
        if session is not self.session or not session.open or self.on_connect is None:
            return
        self.on_connect(self, None, ConnectFlags(session_present=False), ReasonCode(PacketTypes.CONNACK, "Success"), None)

    def _close(self) -> None:
        if self.session is not None:
            self.session.open = False


@dataclass
class _Panel:
    client: SpanMqttClient
    broker: _Broker

    async def until(self, condition: Callable[[], bool], what: str, timeout: float = 10.0) -> None:
        deadline = self.broker.loop.time() + timeout
        while not condition():
            if self.broker.loop.time() > deadline:
                raise AssertionError(f"timed out waiting for {what}")
            await asyncio.sleep(0.01)

    async def quiesce(self) -> None:
        """Wait until every delivery the broker scheduled has been made and handled."""
        await self.until(lambda: self.broker.pending == 0, "the broker's deliveries")
        for _ in range(5):
            await asyncio.sleep(0)


@pytest.fixture
async def panel(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Panel]:
    """A connected-on-demand client on a plaintext broker serving `_FLAT_TREE`."""
    # Short enough that a refused reconnect is retried inside a test's patience.
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p", use_tls=False),
        schema=flat_schema(),
    )
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        yield _Panel(client, broker)
        await client.close()


async def _connected(panel: _Panel) -> _Session:
    await panel.client.connect()
    await panel.quiesce()
    [session] = panel.broker.sessions
    return session


@pytest.mark.asyncio
async def test_connect_subscribes_once_and_receives_the_tree_once(panel: _Panel) -> None:
    """The defect as measured: the first connection asked for the tree twice and got it twice."""
    session = await _connected(panel)

    assert session.subscribed == [_FLAT_FILTER]
    assert sorted(session.delivered) == sorted(_FLAT_TREE)


@pytest.mark.asyncio
async def test_a_repeated_connection_notification_does_not_resubscribe(panel: _Panel) -> None:
    """Being told again that the broker is connected is not a new session.

    Subscription belongs to the CONNACK that opened the session, so a connection
    notification on its own -- the edge a consumer is told about -- asks the broker
    for nothing.
    """
    session = await _connected(panel)
    subscribed = list(session.subscribed)
    delivered = len(session.delivered)

    panel.client._on_connection_change(True)
    await panel.quiesce()

    assert session.subscribed == subscribed
    assert len(session.delivered) == delivered


@pytest.mark.asyncio
async def test_a_session_paho_replaces_without_a_disconnect_is_subscribed_once(panel: _Panel) -> None:
    """A CONNACK with no disconnect before it is still a new session, and needs the subscription.

    paho's `reconnect()` closes the socket it had without reporting a disconnect,
    so two connected events in a row can each be a session of their own. The clean
    session holds nothing over, so suppressing the second would leave the
    connection live and silent.
    """
    await _connected(panel)
    [paho] = panel.broker.clients

    paho.reconnect()
    await panel.until(lambda: len(panel.broker.sessions) == 2, "the replacement session")
    await panel.quiesce()

    second = panel.broker.sessions[1]
    assert second.subscribed == [_FLAT_FILTER]
    assert sorted(second.delivered) == sorted(_FLAT_TREE)


@pytest.mark.asyncio
async def test_a_reconnect_subscribes_its_session_once(panel: _Panel) -> None:
    """A dropped connection comes back subscribed, once, and serving again."""
    await _connected(panel)
    [paho] = panel.broker.clients

    paho.drop()
    await panel.until(lambda: len(panel.broker.sessions) == 2, "the reconnected session")
    await panel.quiesce()

    reconnected = panel.broker.sessions[1]
    assert reconnected.subscribed == [_FLAT_FILTER]
    assert sorted(reconnected.delivered) == sorted(_FLAT_TREE)
    assert await panel.client.ping() is True


@pytest.mark.asyncio
async def test_a_rebuilt_client_subscribes_its_session_once(panel: _Panel) -> None:
    """The rebuild path replaces paho and the parser; the new session is subscribed once and the new parser is fed."""
    await _connected(panel)
    [old] = panel.broker.clients
    old_parser = panel.client.adapter
    old.next_reconnect_error = ssl.SSLError("handshake failed")

    old.drop()
    await panel.until(lambda: len(panel.broker.sessions) == 2, "the rebuilt client's session")
    await panel.quiesce()

    rebuilt = panel.broker.sessions[1]
    assert [built.session for built in panel.broker.clients] == [panel.broker.sessions[0], rebuilt]
    assert rebuilt.subscribed == [_FLAT_FILTER]
    assert sorted(rebuilt.delivered) == sorted(_FLAT_TREE)
    parser = panel.client.adapter
    assert parser is not None and parser is not old_parser
    assert parser.is_ready()


@pytest.mark.asyncio
async def test_a_generation_swap_moves_the_live_session_to_the_new_parsers_filter(panel: _Panel) -> None:
    """The new parser has seen none of the tree, so it is subscribed for it once, and the old filter is dropped.

    Left subscribed, the flat parser's filter overlaps the parent/child one, and the
    broker delivers every message under the panel's device once per matching
    filter: every reading twice until the next reconnect.
    """
    session = await _connected(panel)
    subscribed = len(session.subscribed)

    with patch("span_panel_api.mqtt.client.get_homie_schema", return_value=parent_child_schema()):
        panel.broker.publish(_DATA_MODEL_VERSION_TOPIC, "1.0")
        await panel.until(lambda: panel.client.data_model_version == "1.0", "the parser swap")
        await panel.quiesce()

    assert session.subscribed[subscribed:] == [_PARENT_CHILD_FILTER]
    assert session.unsubscribed == [_FLAT_FILTER]
    assert session.filters == [_PARENT_CHILD_FILTER]

    delivered = len(session.delivered)
    panel.broker.publish(f"{TOPIC_PREFIX_SERIAL}/core/property-0", "changed")
    await panel.quiesce()
    assert session.delivered[delivered:] == [f"{TOPIC_PREFIX_SERIAL}/core/property-0"]


@pytest.mark.asyncio
async def test_a_swap_finished_while_disconnected_is_subscribed_by_the_next_session(panel: _Panel) -> None:
    """Nothing is lost when the swap lands between sessions: the next one subscribes the new parser's filter, once."""
    await _connected(panel)
    [paho] = panel.broker.clients
    release = asyncio.Event()

    async def schema_once_released(*_args: object, **_kwargs: object) -> V2HomieSchema:
        await release.wait()
        return parent_child_schema()

    with patch("span_panel_api.mqtt.client.get_homie_schema", side_effect=schema_once_released):
        panel.broker.publish(_DATA_MODEL_VERSION_TOPIC, "1.0")
        await panel.quiesce()
        panel.broker.refusing = True
        paho.drop()
        release.set()
        await panel.until(lambda: panel.client.data_model_version == "1.0", "the parser swap")
        assert len(panel.broker.sessions) == 1
        panel.broker.refusing = False
        await panel.until(lambda: len(panel.broker.sessions) == 2, "the reconnected session")
        await panel.quiesce()

    reconnected = panel.broker.sessions[1]
    assert reconnected.subscribed == [_PARENT_CHILD_FILTER]
