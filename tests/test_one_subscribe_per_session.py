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
from collections.abc import AsyncIterator, Callable, Iterator
import contextlib
import gc
from dataclasses import dataclass, field
import logging
import socket
import ssl
import threading
from typing import Protocol
from unittest.mock import patch

from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage, topic_matches_sub
from paho.mqtt.enums import CallbackAPIVersion, MQTTErrorCode
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
import pytest

from span_panel_api.adapters import resolve_adapter
from span_panel_api.exceptions import SpanPanelConnectionError
from span_panel_api.models import V2HomieSchema
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.models import MqttClientConfig, MqttTransport
from span_panel_api.protocol import SchemaAdapter
from span_panel_api_schema_0.adapter import SchemaZeroAdapter
from span_panel_api_schema_0.const import WILDCARD_TOPIC_FMT

from conftest import (
    DESCRIPTION_WITH_CIRCUIT,
    MINIMAL_DESCRIPTION,
    SERIAL,
    TOPIC_PREFIX_SERIAL,
    flat_schema,
    parent_child_schema,
)

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
    qos: dict[str, int] = field(default_factory=dict)
    """The QoS each filter was last subscribed at."""
    open: bool = True


ConnackHandler = Callable[["_FakePaho", object, ConnectFlags, ReasonCode, Properties | None], None]
DisconnectHandler = Callable[["_FakePaho", object, DisconnectFlags, ReasonCode, Properties | None], None]
MessageHandler = Callable[["_FakePaho", object, MQTTMessage], None]
SocketHandler = Callable[["_FakePaho", object, "_FakeSocket"], None]


class _HasFileno(Protocol):
    """What the event loop's reader and writer methods take besides a bare descriptor number."""

    def fileno(self) -> int: ...


class _FakeSocket:
    """What paho hands its socket callbacks.

    No descriptor by default, so the bridge registers none. With `real`, one end
    of a socket pair, so the loop's reader and writer are really registered and
    a test can make the socket readable from the other end.
    """

    def __init__(self, real: bool) -> None:
        self._pair = socket.socketpair() if real else None
        self.bytes_read = 0

    def fileno(self) -> int:
        return self._pair[0].fileno() if self._pair is not None else -1

    def make_readable(self) -> None:
        assert self._pair is not None, "only a real socket can be made readable"
        self._pair[1].send(b"\x00")

    def drain(self) -> None:
        """Read what the other end sent, as paho's `loop_read` would."""
        if self._pair is not None:
            self._pair[0].setblocking(False)
            try:
                self.bytes_read += len(self._pair[0].recv(4096))
            except BlockingIOError:
                pass

    def close(self) -> None:
        if self._pair is not None:
            for end in self._pair:
                end.close()


class _Broker:
    """The retained tree, the sessions opened against it, and the clients that opened them."""

    def __init__(self, loop: asyncio.AbstractEventLoop, retained: dict[str, str]) -> None:
        self.loop = loop
        self.retained = dict(retained)
        self.sessions: list[_Session] = []
        self.clients: list[_FakePaho] = []
        self.refusing = False
        """While set, every connect and reconnect is refused, as a broker that is down refuses them."""
        self.holding_connacks = False
        """While set, a session opens without its CONNACK, which waits in `held` for `release_connacks`."""
        self.held: list[tuple[_FakePaho, _Session]] = []
        self.connecting = threading.Event()
        """Set when a client's blocking connect starts, in the executor thread."""
        self.connect_gate: threading.Event | None = None
        """While set to an unset event, a blocking connect waits on it before opening its socket."""
        self.reconnecting = threading.Event()
        """Set when a client's blocking reconnect starts, in the executor thread."""
        self.reconnect_gate: threading.Event | None = None
        """As `connect_gate`, for a blocking reconnect."""
        self.calls_while_held: list[str] = []
        """Every call that reached a client from another thread while a blocking connect or reconnect was inside it.

        paho runs with its locks removed, so the thread holding a client is the
        only one that may call into it; anything listed here came from another.
        """
        self.loop_thread = threading.current_thread()
        self.loop_only_off_loop: list[str] = []
        """Every one of the bridge's loop-only socket handlers (`_async_*`) that paho had to call off the loop.

        paho calls a socket callback on whichever thread it is running on, the
        executor's inside a blocking call; a loop-only handler there would touch
        the event loop from the wrong thread.
        """
        self.reconnect_fails_before_closing: Exception | None = None
        """When set, the next blocking reconnect raises this before closing the socket it holds, as paho does on a bad host."""
        self.after_closing_in_reconnect: Callable[[], None] | None = None
        """Called on the thread once a blocking reconnect has closed the old socket and before it opens the next."""
        self.connect_fails_after_opening: OSError | None = None
        """When set, the next blocking connect opens its socket and then raises this, leaving the socket open."""
        self.real_sockets = False
        """While set, clients open sockets the loop really registers. See `_FakeSocket`."""
        self.holding_closes = False
        """While set, a DISCONNECT that `disconnect()` queued waits in `held_closes` for `release_closes`."""
        self.held_closes: list[Callable[[], None]] = []
        self.pending = 0
        """Deliveries and DISCONNECT writes scheduled and not yet made."""

    def defer(self, action: Callable[[], None]) -> None:
        """Run `action` on the loop later, as paho writes a queued packet from the loop's write callback."""
        if self.holding_closes:
            self.held_closes.append(action)
            return
        self.pending += 1

        def run() -> None:
            self.pending -= 1
            action()

        self.loop.call_soon_threadsafe(run)

    def release_closes(self) -> None:
        self.holding_closes = False
        held, self.held_closes = self.held_closes, []
        for action in held:
            self.defer(action)

    def release_connacks(self) -> None:
        self.holding_connacks = False
        held, self.held = self.held, []
        for client, session in held:
            self.loop.call_soon_threadsafe(client.connack, session)

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

    def subscribe(self, client: _FakePaho, session: _Session, filters: list[tuple[str, int]]) -> None:
        for topic_filter, qos in filters:
            session.subscribed.append(topic_filter)
            session.qos[topic_filter] = qos
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
    """The slice of paho the bridge drives, opening sessions on a `_Broker` instead of a socket.

    Socket callbacks are made where paho makes them: on the executor thread
    inside a blocking `connect()` or `reconnect()`, on the loop for a dropped
    connection or a written DISCONNECT.
    """

    def __init__(self, broker: _Broker) -> None:
        self._broker = broker
        self.session: _Session | None = None
        self.next_reconnect_error: OSError | ssl.SSLError | None = None
        self.disconnect_calls = 0
        self.loop_read_calls = 0
        self.loop_misc_calls = 0
        self.loop_misc_without_socket = 0
        self._holder: threading.Thread | None = None
        self._sock: _FakeSocket | None = None
        self._write_registered = False
        self.on_connect: ConnackHandler | None = None
        self.on_disconnect: DisconnectHandler | None = None
        self.on_message: MessageHandler | None = None
        self.on_socket_open: SocketHandler | None = None
        self.on_socket_close: SocketHandler | None = None
        self.on_socket_register_write: SocketHandler | None = None
        self.on_socket_unregister_write: SocketHandler | None = None
        # Assigned by the bridge and never called: nothing here publishes.
        self.on_publish: object = None

    @property
    def held_by_a_thread(self) -> bool:
        return self._holder is not None

    @property
    def wants_to_write(self) -> bool:
        """Whether paho has asked the loop to watch this client's socket for writing and not yet withdrawn it."""
        return self._write_registered

    def setup(self) -> None:
        """The lock swap `AsyncMQTTClient` does; nothing to swap here."""

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        """Credentials are not checked by this broker."""

    def tls_set_context(self, context: ssl.SSLContext | None = None) -> None:
        """TLS is not negotiated with this broker."""

    def socket(self) -> _FakeSocket | None:
        return self._sock

    def connect(self, host: str, port: int = 1883, keepalive: int = 60) -> MQTTErrorCode:
        """paho's `connect()` is its `reconnect()` with the address recorded first."""
        assert host and port > 0 and keepalive > 0
        self._holder = threading.current_thread()
        try:
            self._broker.connecting.set()
            gate = self._broker.connect_gate
            if gate is not None:
                assert gate.wait(timeout=5), "the test never released the blocking connect"
            self._sock_close()
            session = self._open()
            failure = self._broker.connect_fails_after_opening
            self._broker.connect_fails_after_opening = None
            if failure is not None:
                raise failure
        finally:
            self._holder = None
        self._answer(session)
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def reconnect(self) -> MQTTErrorCode:
        """paho closes the previous socket without reporting a disconnect, then opens another."""
        self._holder = threading.current_thread()
        try:
            self._broker.reconnecting.set()
            gate = self._broker.reconnect_gate
            if gate is not None:
                assert gate.wait(timeout=5), "the test never released the blocking reconnect"
            early = self._broker.reconnect_fails_before_closing
            self._broker.reconnect_fails_before_closing = None
            if early is not None:
                raise early
            error = self.next_reconnect_error
            self.next_reconnect_error = None
            self._sock_close()
            after_closing = self._broker.after_closing_in_reconnect
            if after_closing is not None:
                after_closing()
            if error is not None:
                raise error
            session = self._open()
        finally:
            self._holder = None
        self._answer(session)
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def _record_if_held(self, call: str) -> None:
        holder = self._holder
        if holder is not None and threading.current_thread() is not holder:
            self._broker.calls_while_held.append(call)

    def _call_socket(self, callback: SocketHandler | None, sock: _FakeSocket) -> None:
        """Make a socket callback on the calling thread, as paho does, noting a loop-only handler off the loop."""
        if callback is None:
            return
        name = getattr(callback, "__name__", "")
        if name.startswith("_async_") and threading.current_thread() is not self._broker.loop_thread:
            self._broker.loop_only_off_loop.append(name)
        callback(self, None, sock)

    def subscribe(self, topic: str | list[tuple[str, int]], qos: int = 0) -> tuple[MQTTErrorCode, int | None]:
        self._record_if_held("subscribe")
        session = self.session
        if self._sock is None or session is None or not session.open:
            return (MQTTErrorCode.MQTT_ERR_NO_CONN, None)
        self._broker.subscribe(self, session, [(topic, qos)] if isinstance(topic, str) else list(topic))
        return (MQTTErrorCode.MQTT_ERR_SUCCESS, 1)

    def unsubscribe(self, topic: str | list[str]) -> tuple[MQTTErrorCode, int | None]:
        self._record_if_held("unsubscribe")
        session = self.session
        if self._sock is None or session is None or not session.open:
            return (MQTTErrorCode.MQTT_ERR_NO_CONN, None)
        self._broker.unsubscribe(session, [topic] if isinstance(topic, str) else list(topic))
        return (MQTTErrorCode.MQTT_ERR_SUCCESS, 1)

    def loop_read(self) -> MQTTErrorCode:
        self._record_if_held("loop_read")
        self.loop_read_calls += 1
        if self._sock is not None:
            self._sock.drain()
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def loop_write(self) -> MQTTErrorCode:
        """Write what is queued; with nothing left, paho stops asking to write."""
        self._record_if_held("loop_write")
        sock = self._sock
        if sock is not None and self._write_registered:
            self._write_registered = False
            self._call_socket(self.on_socket_unregister_write, sock)
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def loop_misc(self) -> MQTTErrorCode:
        """paho answers MQTT_ERR_NO_CONN with no socket, and the bridge's misc timer then stops."""
        self._record_if_held("loop_misc")
        if self._sock is None:
            self.loop_misc_without_socket += 1
            return MQTTErrorCode.MQTT_ERR_NO_CONN
        self.loop_misc_calls += 1
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def disconnect(self) -> MQTTErrorCode:
        """Queue a DISCONNECT, which the loop writes later; then paho reports the disconnect and closes the socket."""
        self._record_if_held("disconnect")
        self.disconnect_calls += 1
        if self._sock is None:
            return MQTTErrorCode.MQTT_ERR_NO_CONN
        self._register_write()
        self._broker.defer(self._disconnect_written)
        return MQTTErrorCode.MQTT_ERR_SUCCESS

    def drop(self) -> None:
        """The network goes away under an open session: paho closes the socket, then reports it."""
        self._sock_close()
        self._report_disconnect(ReasonCode(PacketTypes.DISCONNECT, "Unspecified error"))

    def _disconnect_written(self) -> None:
        if self._sock is None:
            return
        self._report_disconnect(ReasonCode(PacketTypes.DISCONNECT, "Normal disconnection"))
        self._sock_close()

    def _report_disconnect(self, reason: ReasonCode) -> None:
        if self.on_disconnect is not None:
            self.on_disconnect(self, None, DisconnectFlags(is_disconnect_packet_from_server=False), reason, None)

    def _open(self) -> _Session:
        if self._broker.refusing:
            raise ConnectionRefusedError(61, "Connection refused")
        session = self._broker.open_session()
        self.session = session
        sock = _FakeSocket(self._broker.real_sockets)
        self._sock = sock
        self._call_socket(self.on_socket_open, sock)
        self._register_write()  # the CONNECT, queued
        return session

    def _register_write(self) -> None:
        sock = self._sock
        if sock is None or self._write_registered:
            return
        self._write_registered = True
        self._call_socket(self.on_socket_register_write, sock)

    def _sock_close(self) -> None:
        """paho's `_sock_close`: unregister the write if registered, then report the close, on the calling thread."""
        sock = self._sock
        if sock is None:
            return
        self._sock = None
        if self.session is not None:
            self.session.open = False
        if self._write_registered:
            self._write_registered = False
            self._call_socket(self.on_socket_unregister_write, sock)
        self._call_socket(self.on_socket_close, sock)
        sock.close()

    def _answer(self, session: _Session) -> None:
        """Schedule the CONNACK for `session`, or hold it.

        Only once the blocking call has returned: paho writes the CONNECT from the
        loop's write callback after that, so no CONNACK can come sooner.
        """
        if self._broker.holding_connacks:
            self._broker.held.append((self, session))
            return
        self._broker.loop.call_soon_threadsafe(self.connack, session)

    def connack(self, session: _Session) -> None:
        """The broker's answer to the CONNECT that opened `session`, if that session is still this client's."""
        if session is not self.session or not session.open or self.on_connect is None:
            return
        self.on_connect(self, None, ConnectFlags(session_present=False), ReasonCode(PacketTypes.CONNACK, "Success"), None)


async def _let_the_loop_run() -> None:
    """Run what the last call scheduled, while a gated executor call is still held.

    Released at once, a gated call could finish before anything the loop had
    yet to run, and a call that reached the held client would go unrecorded.
    """
    for _ in range(5):
        await asyncio.sleep(0)


async def _until(condition: Callable[[], bool], what: str, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


@dataclass
class _Panel:
    client: SpanMqttClient
    broker: _Broker

    async def until(self, condition: Callable[[], bool], what: str, timeout: float = 10.0) -> None:
        await _until(condition, what, timeout)

    async def quiesce(self) -> None:
        """Wait until every delivery the broker scheduled has been made and handled."""
        await self.until(lambda: self.broker.pending == 0, "the broker's deliveries")
        for _ in range(5):
            await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def _serving(tree: dict[str, str], schema: V2HomieSchema | None, use_tls: bool = False) -> AsyncIterator[_Panel]:
    """A client, connected on demand, on a broker serving `tree`.

    With no `schema` the client fetches one at connect, as a directly constructed
    client does. With `use_tls` it has no pinned CA, so its bridge fetches one
    from the panel on every connect.
    """
    broker = _Broker(asyncio.get_running_loop(), tree)
    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p", use_tls=use_tls),
        schema=schema,
    )
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        try:
            yield _Panel(client, broker)
        finally:
            await client.close()
    assert broker.loop_only_off_loop == [], "paho called a loop-only socket handler on the executor thread"


def _bridge() -> AsyncMqttBridge:
    """A bare bridge for the broker `AsyncMQTTClient` is patched to."""
    return AsyncMqttBridge(
        host="broker.local",
        port=1883,
        username="u",
        password="p",
        panel_host="192.168.1.1",
        serial_number=SERIAL,
        use_tls=False,
    )


_LOOP_OPERATIONS = (
    "add_reader",
    "remove_reader",
    "add_writer",
    "remove_writer",
    "call_soon",
    "call_at",
    "call_later",
    # What `TimerHandle.cancel()` calls: cancelling the misc timer off the loop.
    "_timer_handle_cancelled",
)


@pytest.fixture(autouse=True)
async def _the_loop_is_driven_only_from_its_own_thread() -> AsyncIterator[None]:
    """Every event-loop operation in these tests is made on the loop's thread.

    The thread-safe way in is `call_soon_threadsafe`, which is left alone. A
    loop operation from the executor thread -- a socket callback acting there
    rather than being recorded for the hand-back, whatever it is called -- is a
    failure of the test that made it.
    """
    loop = asyncio.get_running_loop()
    loop_thread = threading.current_thread()
    off_loop: list[str] = []
    with contextlib.ExitStack() as patches:
        for name in _LOOP_OPERATIONS:
            original: Callable[..., object] = getattr(loop, name)

            def guarded(
                *args: object, _name: str = name, _original: Callable[..., object] = original, **kwargs: object
            ) -> object:
                if threading.current_thread() is not loop_thread:
                    off_loop.append(_name)
                return _original(*args, **kwargs)

            patches.enter_context(patch.object(loop, name, guarded))
        yield
    assert off_loop == [], f"event-loop operations made off the loop's thread: {off_loop}"


@pytest.fixture(autouse=True)
def _quick_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short enough that a refused reconnect is retried inside a test's patience."""
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)


@pytest.fixture
async def panel() -> AsyncIterator[_Panel]:
    """A connected-on-demand client on a plaintext broker serving `_FLAT_TREE`."""
    async with _serving(_FLAT_TREE, flat_schema()) as served:
        yield served


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


@pytest.mark.asyncio
async def test_a_swap_between_connect_and_connack_is_subscribed_once_by_the_connack(
    panel: _Panel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parser installed after a CONNECT and before its CONNACK waits for the CONNACK.

    paho sends a SUBSCRIBE on any open socket, including one still waiting for its
    CONNACK, and the CONNACK then subscribes the session again. So a session is
    live, for the bridge, only once its CONNACK has arrived.
    """
    # One reconnect attempt and then a long pause, so no second CONNECT overtakes the held CONNACK.
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 5.0)
    await _connected(panel)
    [paho] = panel.broker.clients
    release = asyncio.Event()

    async def schema_once_released(*_args: object, **_kwargs: object) -> V2HomieSchema:
        await release.wait()
        return parent_child_schema()

    with patch("span_panel_api.mqtt.client.get_homie_schema", side_effect=schema_once_released):
        panel.broker.publish(_DATA_MODEL_VERSION_TOPIC, "1.0")
        await panel.quiesce()
        panel.broker.holding_connacks = True
        paho.drop()
        await panel.until(lambda: len(panel.broker.sessions) == 2, "the reconnect's CONNECT")
        awaiting = panel.broker.sessions[1]
        release.set()
        await panel.until(lambda: panel.client.data_model_version == "1.0", "the parser swap")
        assert awaiting.subscribed == [], "nothing may be subscribed before the CONNACK"
        panel.broker.release_connacks()
        await panel.until(lambda: bool(awaiting.subscribed), "the CONNACK's SUBSCRIBE")
        await panel.quiesce()

    assert awaiting.subscribed == [_PARENT_CHILD_FILTER]


@pytest.mark.asyncio
async def test_one_string_is_refused_rather_than_read_as_one_filter_per_character() -> None:
    """A `str` is a `Sequence[str]` to the type checker, and would subscribe `e`, `b`, ..., `#`."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])

        with pytest.raises(TypeError, match="not one string"):
            bridge.set_subscriptions(_PARENT_CHILD_FILTER)

        await bridge.connect()
        await bridge.disconnect()

    assert broker.sessions[0].subscribed == [_FLAT_FILTER], "a refused call changes nothing"


@pytest.mark.parametrize(
    "topic_filter",
    [
        pytest.param("", id="empty"),
        pytest.param("ebus/5/#/core", id="hash-not-last"),
        pytest.param("ebus/5#", id="hash-inside-a-level"),
        pytest.param("ebus/+5/core", id="plus-inside-a-level"),
        pytest.param("ebus/5/\x00", id="null-character"),
        pytest.param("e" * 65536, id="longer-than-mqtt-allows"),
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_filter_is_refused_where_it_is_set(topic_filter: str) -> None:
    """Refused in the caller's frame, before anything is recorded.

    Accepted, it would reach paho only at the next CONNACK, inside `_on_connect`,
    where paho's own refusal escapes the read callback and the connect it belongs
    to waits out its timeout instead.
    """
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])

        with pytest.raises(ValueError, match="(?i)topic filter"):
            bridge.set_subscriptions([_PARENT_CHILD_FILTER, topic_filter])

        await bridge.connect()
        await bridge.disconnect()

    assert broker.sessions[0].subscribed == [_FLAT_FILTER], "a refused call changes nothing"


@pytest.mark.asyncio
async def test_every_well_formed_filter_is_accepted() -> None:
    """The check refuses what MQTT refuses, and nothing more."""
    filters = ["#", "+", "+/+/#", "ebus/5/+/$state", "/", "ebus//5"]
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions(filters)
        await bridge.connect()
        await bridge.disconnect()

    assert broker.sessions[0].subscribed == filters


async def _close_while_connect_waits(panel: _Panel, waiting: Callable[[], bool], resume: Callable[[], None]) -> None:
    """Start connect(), close the client once `waiting` says connect() is suspended, then let it resume.

    However far connect() had got, it must not report a connection the caller
    has already closed.
    """
    connecting = asyncio.create_task(panel.client.connect())
    await panel.until(waiting, "connect() to suspend")
    await panel.client.close()
    await _let_the_loop_run()
    resume()
    with pytest.raises(SpanPanelConnectionError, match="Closed while connecting"):
        await asyncio.wait_for(connecting, timeout=5)
    await panel.quiesce()


@pytest.mark.asyncio
async def test_close_while_connect_fetches_the_schema_opens_no_session() -> None:
    """The window the review found: close() lets go of the bridge, and connect() went on to connect it.

    The bridge it connected had no subscription -- the parser was built after
    close() had dropped the client's reference -- so the broker saw a CONNECT and
    no SUBSCRIBE, connect() timed out waiting for a tree that never came, and the
    bridge stayed connected with its reconnect loop armed and nothing to close it.
    """
    fetching = asyncio.Event()
    fetched = asyncio.Event()

    async def schema_once_released(*_args: object, **_kwargs: object) -> V2HomieSchema:
        fetching.set()
        await fetched.wait()
        return flat_schema()

    async with _serving(_FLAT_TREE, None) as panel:
        with patch("span_panel_api.mqtt.client.get_homie_schema", side_effect=schema_once_released):
            await _close_while_connect_waits(panel, fetching.is_set, fetched.set)

        assert panel.broker.clients == [], "no paho client is built for a closed client"
        assert panel.broker.sessions == []
        assert panel.client.adapter is None, "nor a parser"


@pytest.mark.asyncio
async def test_close_while_connect_preloads_the_parser_opens_no_session() -> None:
    """The same window one await later: the parser's import, which runs in a thread."""
    loading = threading.Event()
    loaded = threading.Event()
    resolved: list[str] = []

    def resolve_once_released(key: str, reason: str) -> type[SchemaAdapter]:
        resolved.append(key)
        if len(resolved) == 1:
            loading.set()
            assert loaded.wait(timeout=5), "the test never released the preload"
        return resolve_adapter(key, reason)

    async with _serving(_FLAT_TREE, flat_schema()) as panel:
        with patch("span_panel_api.mqtt.client.resolve_adapter", resolve_once_released):
            await _close_while_connect_waits(panel, loading.is_set, loaded.set)

        assert panel.broker.clients == []
        assert panel.broker.sessions == []
        assert panel.client.adapter is None


@pytest.mark.asyncio
async def test_close_while_connect_reads_package_metadata_opens_no_session() -> None:
    """After the parser is built, so this bridge did have the topics: connected, it would have been read by nobody."""
    reading = threading.Event()
    read = threading.Event()

    def metadata_once_released() -> tuple[list[str], str]:
        reading.set()
        assert read.wait(timeout=5), "the test never released the metadata read"
        return ["schema_0"], "0.0.0"

    async with _serving(_FLAT_TREE, flat_schema()) as panel:
        with patch("span_panel_api.mqtt.client._metadata_for_the_log", metadata_once_released):
            await _close_while_connect_waits(panel, reading.is_set, read.set)

        assert panel.broker.clients == []
        assert panel.broker.sessions == []


@pytest.mark.asyncio
async def test_close_while_the_bridge_awaits_its_connack_reports_the_close_at_once() -> None:
    """The bridge's wait for its CONNACK ends when close() disconnects it, rather than running out its 15 s."""
    async with _serving(_FLAT_TREE, flat_schema()) as panel:
        panel.broker.holding_connacks = True
        await _close_while_connect_waits(panel, lambda: bool(panel.broker.held), lambda: None)

        [session] = panel.broker.sessions
        assert not session.open
        assert session.subscribed == []


@pytest.mark.asyncio
async def test_close_during_the_bridges_blocking_connect_reports_the_close() -> None:
    """The executor connect returned to a bridge close() had disconnected: in 3.8.0 an `AttributeError`."""
    async with _serving(_FLAT_TREE, flat_schema()) as panel:
        gate = threading.Event()
        panel.broker.connect_gate = gate
        await _close_while_connect_waits(panel, panel.broker.connecting.is_set, gate.set)

        assert panel.broker.calls_while_held == [], "close() reached into a client the executor held"
        assert all(not session.open for session in panel.broker.sessions)
        assert all(session.subscribed == [] for session in panel.broker.sessions)


@pytest.mark.asyncio
async def test_close_while_the_bridge_fetches_an_unpinned_ca_opens_no_session() -> None:
    """Unpinned, the bridge fetches the panel's CA before it has a paho client, so close() finds nothing to disconnect.

    The bridge notices the disconnect() when the fetch returns and builds no
    client, where it used to go on to connect and subscribe.
    """
    fetching = asyncio.Event()
    fetched = asyncio.Event()

    async def ca_once_released(*_args: object, **_kwargs: object) -> str:
        fetching.set()
        await fetched.wait()
        return "PEM"

    async with _serving(_FLAT_TREE, flat_schema(), use_tls=True) as panel:
        with (
            patch("span_panel_api.mqtt.connection.download_ca_cert", side_effect=ca_once_released),
            patch("span_panel_api.mqtt.connection.build_panel_ssl_context", return_value=ssl.create_default_context()),
        ):
            await _close_while_connect_waits(panel, fetching.is_set, fetched.set)

        assert panel.broker.clients == []
        assert panel.broker.sessions == []


@pytest.mark.asyncio
async def test_close_while_connect_waits_for_ready_reports_the_close_at_once() -> None:
    """Connected and subscribed, then closed: the wait for a ready tree ends there, rather than running out its 30 s."""
    never_ready = {f"{TOPIC_PREFIX_SERIAL}/$description": MINIMAL_DESCRIPTION}

    async with _serving(never_ready, flat_schema()) as panel:
        await _close_while_connect_waits(
            panel, lambda: bool(panel.broker.sessions) and bool(panel.broker.sessions[0].subscribed), lambda: None
        )

        [session] = panel.broker.sessions
        assert not session.open


@pytest.mark.asyncio
async def test_close_while_connect_waits_for_labels_reports_the_close() -> None:
    """The tree is ready and its circuit's name is still to come when close() lands: connect() does not return as if connected."""
    unnamed = {f"{TOPIC_PREFIX_SERIAL}/$description": DESCRIPTION_WITH_CIRCUIT, f"{TOPIC_PREFIX_SERIAL}/$state": "ready"}

    async with _serving(unnamed, flat_schema()) as panel:

        def settling() -> bool:
            parser = panel.client.adapter
            return parser is not None and parser.is_ready()

        await _close_while_connect_waits(panel, settling, lambda: None)

        [session] = panel.broker.sessions
        assert not session.open


class _MalformedTopics(SchemaZeroAdapter):
    """A flat parser naming a filter MQTT refuses."""

    def topics_to_subscribe(self) -> list[str]:
        return ["ebus/#/core"]


@pytest.mark.asyncio
async def test_a_parser_whose_topics_are_refused_is_not_installed() -> None:
    """The refusal reaches connect()'s caller, and leaves no parser that no session could ever feed."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p", use_tls=False),
        adapter_factory=_MalformedTopics,
        schema=flat_schema(),
    )
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        try:
            with pytest.raises(ValueError, match="(?i)topic filter"):
                await client.connect()
        finally:
            await client.close()

    assert client.adapter is None
    assert broker.sessions == []


_CONSUMER_TOPIC = "homeassistant/status"
"""A topic a consumer of the bridge subscribes to itself, outside any parser's set."""


async def _reconnected(broker: _Broker) -> _Session:
    """Drop the latest session and return the one the bridge's reconnect opens."""
    opened = len(broker.sessions)
    broker.clients[-1].drop()
    await _until(lambda: len(broker.sessions) == opened + 1, "the reconnected session")
    return broker.sessions[-1]


@pytest.mark.asyncio
async def test_subscribe_adds_a_topic_once_and_every_later_session_renews_it() -> None:
    """The 3.8.0 method, kept: one SUBSCRIBE for a topic not yet held, none for one that is, and each renewed once."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        [first] = broker.sessions

        bridge.subscribe(_CONSUMER_TOPIC)
        assert first.subscribed == [_FLAT_FILTER, _CONSUMER_TOPIC]
        bridge.subscribe(_CONSUMER_TOPIC)
        bridge.subscribe(_FLAT_FILTER)
        assert first.subscribed == [_FLAT_FILTER, _CONSUMER_TOPIC], "a topic already held sends nothing"

        second = await _reconnected(broker)
        await bridge.disconnect()

    assert sorted(second.subscribed) == sorted([_FLAT_FILTER, _CONSUMER_TOPIC])


@pytest.mark.asyncio
async def test_subscribe_before_connect_is_sent_by_the_connack() -> None:
    """In 3.8.0 this was dropped: with no paho client yet there was nothing to send it on, and nothing kept it."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.subscribe(_CONSUMER_TOPIC)
        await bridge.connect()
        await bridge.disconnect()

    assert broker.sessions[0].subscribed == [_CONSUMER_TOPIC]


@pytest.mark.asyncio
async def test_replacing_the_set_never_drops_what_subscribe_added() -> None:
    """A parser swap replaces its own topics; a consumer's are not its to drop, even one both held."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        [first] = broker.sessions
        bridge.subscribe(_CONSUMER_TOPIC)
        bridge.subscribe(_FLAT_FILTER)

        bridge.set_subscriptions([_PARENT_CHILD_FILTER])

        assert first.unsubscribed == []
        assert sorted(first.filters) == sorted([_FLAT_FILTER, _CONSUMER_TOPIC, _PARENT_CHILD_FILTER])

        bridge.set_subscriptions([])
        assert first.unsubscribed == [_PARENT_CHILD_FILTER], "the set's own topic goes when the set lets it go"

        second = await _reconnected(broker)
        await bridge.disconnect()

    assert sorted(second.subscribed) == sorted([_FLAT_FILTER, _CONSUMER_TOPIC])


@pytest.mark.asyncio
async def test_subscribe_honours_its_qos_and_a_topic_is_held_at_the_highest_asked() -> None:
    """3.8.0 passed `qos` to paho. A topic asked for at a higher QoS than it is held at is subscribed again, at that QoS; never lower."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        [first] = broker.sessions

        bridge.subscribe(_CONSUMER_TOPIC, qos=1)
        bridge.subscribe(_FLAT_FILTER, qos=1)
        bridge.subscribe(_FLAT_FILTER, qos=0)
        assert first.subscribed == [_FLAT_FILTER, _CONSUMER_TOPIC, _FLAT_FILTER]
        assert first.qos == {_FLAT_FILTER: 1, _CONSUMER_TOPIC: 1}

        bridge.set_subscriptions([_FLAT_FILTER])
        assert first.qos[_FLAT_FILTER] == 1, "a new consumer of the set does not lower what another asked for"

        second = await _reconnected(broker)
        await bridge.disconnect()

    assert second.qos == {_FLAT_FILTER: 1, _CONSUMER_TOPIC: 1}


@pytest.mark.parametrize(
    ("topic", "qos"),
    [
        pytest.param("", 0, id="empty-filter"),
        pytest.param("ebus/#/core", 0, id="malformed-filter"),
        pytest.param("a/b", 3, id="no-such-qos"),
        pytest.param("a/b", 1.0, id="float-qos"),
        pytest.param("a/b", True, id="bool-qos"),
    ],
)
@pytest.mark.asyncio
async def test_subscribe_refuses_what_mqtt_would_where_it_is_called(topic: str, qos: int) -> None:
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()

        with pytest.raises(ValueError, match="(?i)topic filter|qos"):
            bridge.subscribe(topic, qos=qos)

        await bridge.connect()
        await bridge.disconnect()

    assert broker.sessions[0].subscribed == [], "a refused call records nothing"


async def _disconnect_while_bridge_connects(
    broker: _Broker, bridge: AsyncMqttBridge, waiting: Callable[[], bool], resume: Callable[[], None]
) -> None:
    """Start the bridge's connect(), disconnect it once `waiting` says connect() is suspended, then let it resume."""
    connecting = asyncio.create_task(bridge.connect())
    await _until(waiting, "the bridge's connect() to suspend")
    await bridge.disconnect()
    await _let_the_loop_run()
    resume()
    with pytest.raises(SpanPanelConnectionError, match="disconnect"):
        await asyncio.wait_for(connecting, timeout=5)
    await _until(lambda: broker.pending == 0, "the broker's deliveries")
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_the_bridge_notices_a_disconnect_during_its_blocking_connect() -> None:
    """A SpanPanelError, not 3.8.0's `AttributeError`, and the socket the executor opened is closed and never subscribed.

    The CONNACK for that socket can still arrive after `disconnect()`. It belongs
    to a client the bridge has let go of, so it changes nothing: the bridge is
    not connected again by it and the session is not subscribed.
    """
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        gate = threading.Event()
        broker.connect_gate = gate

        await _disconnect_while_bridge_connects(broker, bridge, broker.connecting.is_set, gate.set)

    assert broker.calls_while_held == [], "disconnect() reached into a client the executor held"
    assert not bridge.is_connected()
    [session] = broker.sessions
    assert not session.open
    assert session.subscribed == []


@pytest.mark.asyncio
async def test_the_bridge_ends_its_connack_wait_on_disconnect() -> None:
    """Rather than waiting out MQTT_CONNECT_TIMEOUT_S for a CONNACK nobody will read."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        broker.holding_connacks = True

        await _disconnect_while_bridge_connects(broker, bridge, lambda: bool(broker.held), lambda: None)

    assert not bridge.is_connected()
    assert [session.open for session in broker.sessions] == [False]


@pytest.mark.asyncio
async def test_the_bridge_builds_no_client_after_a_disconnect_during_its_ca_fetch() -> None:
    """Unpinned, the CA is fetched before there is a paho client for disconnect() to reach."""
    fetching = asyncio.Event()
    fetched = asyncio.Event()

    async def ca_once_released(*_args: object, **_kwargs: object) -> str:
        fetching.set()
        await fetched.wait()
        return "PEM"

    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with (
        patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client),
        patch("span_panel_api.mqtt.connection.download_ca_cert", side_effect=ca_once_released),
        patch("span_panel_api.mqtt.connection.build_panel_ssl_context", return_value=ssl.create_default_context()),
    ):
        bridge = AsyncMqttBridge(
            host="broker.local", port=8883, username="u", password="p", panel_host="192.168.1.1", serial_number=SERIAL
        )
        await _disconnect_while_bridge_connects(broker, bridge, fetching.is_set, fetched.set)

    assert broker.clients == []
    assert not bridge.is_connected()


@pytest.mark.asyncio
async def test_a_replaced_clients_late_disconnect_does_not_end_the_rebuilt_session(panel: _Panel) -> None:
    """The rebuild's new client is connected; the client it replaced reporting its own end changes nothing.

    Acted on, it marked the bridge disconnected under a live session, told every
    consumer the panel was lost, and started a reconnect that tore down the
    session the rebuild had just opened and replayed the tree into another.
    """
    edges: list[bool] = []
    panel.client.register_connection_callback(edges.append)
    await _connected(panel)
    [old] = panel.broker.clients
    old.next_reconnect_error = ssl.SSLError("handshake failed")
    old.drop()
    await panel.until(lambda: len(panel.broker.sessions) == 2, "the rebuilt client's session")
    await panel.quiesce()
    assert edges == [True, False, True], "precondition: rebuilt and announced"

    old.drop()
    await asyncio.sleep(0.1)
    await panel.quiesce()

    assert edges == [True, False, True]
    assert len(panel.broker.sessions) == 2, "no reconnect replaces the rebuilt session"
    assert panel.broker.sessions[1].open
    assert await panel.client.ping() is True


async def _rebuilt(broker: _Broker) -> _Session:
    """Drop the bridge's session with a TLS failure, so the reconnect rebuilds paho, and return the rebuilt client's session."""
    opened = len(broker.sessions)
    old = broker.clients[-1]
    old.next_reconnect_error = ssl.SSLError("handshake failed")
    old.drop()
    await _until(lambda: len(broker.sessions) == opened + 1, "the rebuilt client's session")
    return broker.sessions[-1]


@pytest.mark.asyncio
async def test_after_a_rebuild_the_bridge_acts_through_the_client_it_rebuilt() -> None:
    """A subscription made on the rebuilt session reaches that session, not the client it replaced."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        rebuilt = await _rebuilt(broker)

        bridge.subscribe(_CONSUMER_TOPIC)
        await bridge.disconnect()

    assert rebuilt.subscribed == [_FLAT_FILTER, _CONSUMER_TOPIC]


@pytest.mark.asyncio
async def test_a_rebuilt_clients_own_drop_is_noticed_and_reconnected() -> None:
    """Ignoring a let-go client's disconnect must not mean ignoring the live one's: that would leave readings stale with no error."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        await _rebuilt(broker)

        reconnected = await _reconnected(broker)
        await bridge.disconnect()

    assert reconnected.subscribed == [_FLAT_FILTER]


@contextlib.contextmanager
def _unobserved_loop_errors() -> Iterator[list[dict[str, object]]]:
    """Collect what the loop's exception handler is handed: an error no awaiter retrieved."""
    loop = asyncio.get_running_loop()
    seen: list[dict[str, object]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: seen.append(context))
    try:
        yield seen
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.asyncio
async def test_disconnect_during_a_blocking_reconnect_waits_for_the_thread_then_closes_its_socket() -> None:
    """disconnect() leaves the client to the thread inside paho's reconnect(), and the socket it opens is closed after.

    Before, disconnect() called paho's disconnect() on the loop while the thread
    was inside reconnect() on the same client, with paho's locks removed, and
    the socket that reconnect() went on to open was never closed.
    """
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client), _unobserved_loop_errors() as errors:
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        gate = threading.Event()
        broker.reconnect_gate = gate
        broker.clients[-1].drop()
        await _until(broker.reconnecting.is_set, "the blocking reconnect")

        await bridge.disconnect()
        await _let_the_loop_run()
        gate.set()
        await _until(lambda: not bridge._executor_clients, "the hand-back")
        await _until(lambda: broker.pending == 0, "the let-go client's DISCONNECT write")

    assert broker.calls_while_held == []
    assert [session.open for session in broker.sessions] == [False, False]
    assert not bridge.is_connected()
    assert errors == []


@pytest.mark.asyncio
async def test_disconnect_during_a_rebuilds_blocking_connect_waits_for_the_thread_then_closes_its_socket() -> None:
    """The rebuild path, where nothing closed the new client's socket at all once disconnect() had let it go."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client), _unobserved_loop_errors() as errors:
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        gate = threading.Event()
        broker.connecting.clear()
        broker.connect_gate = gate
        [old] = broker.clients
        old.next_reconnect_error = ssl.SSLError("handshake failed")
        old.drop()
        await _until(broker.connecting.is_set, "the rebuilt client's blocking connect")

        await bridge.disconnect()
        await _let_the_loop_run()
        gate.set()
        await _until(lambda: not bridge._executor_clients, "the hand-back")
        await _until(lambda: broker.pending == 0, "the let-go client's DISCONNECT write")

    assert len(broker.clients) == 2, "precondition: the rebuild built a client"
    assert broker.calls_while_held == []
    assert [session.open for session in broker.sessions] == [False, False]
    assert all(session.subscribed == [_FLAT_FILTER] for session in broker.sessions[:1])
    assert broker.sessions[1].subscribed == []
    assert not bridge.is_connected()
    assert errors == []


@pytest.mark.asyncio
async def test_a_blocking_reconnect_that_fails_after_disconnect_is_not_reported_as_unretrieved() -> None:
    """Its caller was cancelled, so nothing awaits the failure; the bridge takes it, rather than the loop reporting it on collection."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client), _unobserved_loop_errors() as errors:
        bridge = _bridge()
        await bridge.connect()
        gate = threading.Event()
        broker.reconnect_gate = gate
        held = broker.clients[-1]
        held.next_reconnect_error = ConnectionRefusedError(61, "Connection refused")
        held.drop()
        await _until(broker.reconnecting.is_set, "the blocking reconnect")

        await bridge.disconnect()
        await _let_the_loop_run()
        gate.set()
        await _until(lambda: not bridge._executor_clients, "the hand-back")
        del held
        gc.collect()

    assert errors == []


@pytest.mark.asyncio
async def test_a_reconnect_replacing_a_socket_still_awaiting_its_connack_leaves_the_loop_out_of_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """paho closes the old socket from the executor thread, and the loop must not reach into the client meanwhile.

    The close callbacks run on the thread, so they must be the thread-safe
    forms; and the misc timer, ticking each second, must not call into the
    client while the thread is inside it. Held past one tick to show that.
    """
    # Long enough between attempts to gate the second one before it starts.
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.3)
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        await bridge.connect()
        broker.holding_connacks = True
        broker.clients[-1].drop()
        await _until(lambda: len(broker.sessions) == 2, "the first reconnect's CONNECT")
        awaiting = broker.sessions[1]
        gate = threading.Event()
        broker.reconnecting.clear()
        broker.reconnect_gate = gate
        await _until(broker.reconnecting.is_set, "the second reconnect, replacing the socket awaiting its CONNACK")

        await asyncio.sleep(1.2)
        gate.set()
        await _until(lambda: len(broker.sessions) == 3, "the second reconnect's CONNECT")
        broker.reconnect_gate = None
        broker.release_connacks()
        await _until(bridge.is_connected, "the CONNACK")
        await _until(lambda: not bridge._executor_clients, "the hand-back")
        replaced, live = broker.sessions[1], broker.sessions[2]
        await bridge.disconnect()

    assert broker.loop_only_off_loop == [], "a loop-only socket handler ran on the executor thread"
    assert broker.calls_while_held == [], "the loop called into a client the executor thread was inside"
    assert awaiting is replaced and not replaced.open
    assert replaced.subscribed == []
    assert live.subscribed == [_FLAT_FILTER]


@pytest.mark.asyncio
async def test_a_blocking_call_the_executor_refuses_leaves_the_client_to_the_loop() -> None:
    """The client is the thread's only once the executor has taken the call; refused, it stays the loop's.

    Held regardless, it would be marked as inside a call that never ran, and
    disconnect() would leave it alone for a hand-back that never comes.
    """
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    loop = asyncio.get_running_loop()
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        with (
            patch.object(loop, "run_in_executor", side_effect=RuntimeError("cannot schedule new futures after shutdown")),
            pytest.raises(SpanPanelConnectionError),
        ):
            await bridge.connect()
        [client] = broker.clients

        await bridge.disconnect()

    assert client.disconnect_calls == 1, "disconnect() treated a client no thread holds as held"


@pytest.mark.asyncio
async def test_the_loop_stops_reading_a_clients_socket_while_a_thread_is_inside_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data on the socket a reconnect is about to replace must not have the loop read the client meanwhile."""
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.3)
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    broker.real_sockets = True
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        broker.holding_connacks = True
        [client] = broker.clients
        client.drop()
        await _until(lambda: len(broker.sessions) == 2, "the first reconnect's CONNECT")
        awaiting = client.socket()
        assert awaiting is not None
        gate = threading.Event()
        broker.reconnecting.clear()
        broker.reconnect_gate = gate
        await _until(broker.reconnecting.is_set, "the second reconnect")

        awaiting.make_readable()
        await asyncio.sleep(0.05)
        gate.set()
        await _until(lambda: not bridge._executor_clients, "the hand-back")
        broker.reconnect_gate = None
        await bridge.disconnect()

    assert broker.calls_while_held == [], "the loop read a client the executor thread was inside"


@pytest.mark.asyncio
async def test_the_loop_reads_the_socket_a_blocking_connect_opened() -> None:
    """The socket the executor opened is registered on the loop once the call returns, or no CONNACK is ever read."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    broker.real_sockets = True
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        [client] = broker.clients
        sock = client.socket()
        assert sock is not None
        reads = client.loop_read_calls

        sock.make_readable()
        await _until(lambda: client.loop_read_calls > reads, "the loop to read the socket")
        await bridge.disconnect()


@pytest.mark.asyncio
async def test_a_let_go_clients_socket_closing_late_leaves_the_bridges_next_connect_alone() -> None:
    """An abandoned connect's socket closes after the bridge has started connecting again; that close is not the new one's.

    Acted on, it ended the new connect's wait for its CONNACK as a failure --
    `MQTT connection failed` while the broker was accepting -- and cancelled the
    misc timer the new socket had started, leaving that session without
    keepalives.
    """
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_subscriptions([_FLAT_FILTER])
        gate = threading.Event()
        broker.connect_gate = gate
        broker.holding_closes = True
        await _disconnect_while_bridge_connects(broker, bridge, broker.connecting.is_set, gate.set)
        assert broker.held_closes, "precondition: the abandoned client's DISCONNECT is still unwritten"
        broker.connect_gate = None
        broker.holding_connacks = True
        connecting = asyncio.create_task(bridge.connect())
        await _until(lambda: bool(broker.held), "the second connect to await its CONNACK")

        broker.release_closes()
        await _until(lambda: broker.pending == 0, "the abandoned client's DISCONNECT write")
        broker.release_connacks()
        await asyncio.wait_for(connecting, timeout=5)
        live = broker.clients[-1]
        misc = live.loop_misc_calls
        await asyncio.sleep(1.2)
        ticked = live.loop_misc_calls > misc
        await bridge.disconnect()
        await _until(lambda: broker.pending == 0, "the live client's DISCONNECT write")

    assert [session.open for session in broker.sessions] == [False, False]
    assert broker.sessions[1].subscribed == [_FLAT_FILTER]
    assert ticked, "the late close cancelled the live session's misc timer"


@pytest.mark.asyncio
async def test_a_bridges_own_disconnect_tells_its_user_once() -> None:
    """As in 3.8.0, a direct user of the bridge hears connected=False after its own disconnect(), and only once.

    paho's own report of that disconnect now comes from a client the bridge has
    let go of, and is ignored, so the bridge says it itself.
    """
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    edges: list[bool] = []
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        bridge.set_connection_callback(edges.append)
        await bridge.connect()

        await bridge.disconnect()
        assert edges == [True, False]
        await _until(lambda: broker.pending == 0, "paho's own report of the disconnect")
        await bridge.disconnect()

    assert edges == [True, False]


@pytest.mark.asyncio
async def test_closing_the_client_does_not_tell_its_consumers_the_panel_was_lost(panel: _Panel) -> None:
    """As in 3.8.0: close() is the consumer's own act, not an outage to be told about."""
    edges: list[bool] = []
    panel.client.register_connection_callback(edges.append)
    await _connected(panel)

    await panel.client.close()
    await panel.quiesce()

    assert edges == [True]


@pytest.mark.asyncio
async def test_a_topic_subscribed_between_connect_and_connack_is_sent_once_by_the_connack() -> None:
    """As for `set_subscriptions`: paho would send it on the socket awaiting its CONNACK, and the CONNACK would send it again."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        broker.holding_connacks = True
        connecting = asyncio.create_task(bridge.connect())
        await _until(lambda: bool(broker.held), "the CONNECT")
        [awaiting] = broker.sessions

        bridge.subscribe(_CONSUMER_TOPIC)
        assert awaiting.subscribed == [], "nothing may be subscribed before the CONNACK"
        broker.release_connacks()
        await asyncio.wait_for(connecting, timeout=5)
        await bridge.disconnect()

    assert awaiting.subscribed == [_CONSUMER_TOPIC]


@pytest.mark.asyncio
async def test_cancelling_connect_during_the_wait_for_ready_ends_that_wait() -> None:
    """The ready wait runs as a task of its own; a cancelled connect() must not leave it behind."""
    never_ready = {f"{TOPIC_PREFIX_SERIAL}/$description": MINIMAL_DESCRIPTION}

    def ready_waits() -> list[asyncio.Task[object]]:
        return [task for task in asyncio.all_tasks() if task.get_name() == "span_mqtt_wait_ready" and not task.done()]

    async with _serving(never_ready, flat_schema()) as panel:
        connecting = asyncio.create_task(panel.client.connect())
        await panel.until(lambda: bool(ready_waits()), "connect() to wait for a ready tree")

        connecting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await connecting
        await asyncio.sleep(0)

        assert ready_waits() == []


@pytest.mark.asyncio
async def test_a_rebuild_whose_connect_fails_closes_what_that_connect_opened() -> None:
    """The loop goes on with the old client; the new one, whose socket the failed connect left open, is closed."""
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        [old] = broker.clients
        broker.connect_fails_after_opening = ConnectionResetError(54, "Connection reset by peer")
        old.next_reconnect_error = ssl.SSLError("handshake failed")

        old.drop()
        await _until(lambda: len(broker.clients) == 2, "the rebuild's client")
        await _until(lambda: len(broker.sessions) == 3, "the old client's next reconnect")
        await _until(lambda: broker.pending == 0, "the failed client's DISCONNECT write")
        await bridge.disconnect()

    failed = broker.sessions[1]
    assert broker.clients[1].socket() is None and not failed.open, "the failed connect's socket was left open"


def _parked_after_closing(
    broker: _Broker,
) -> tuple[threading.Event, threading.Event, list[tuple[socket.socket, socket.socket]]]:
    """Park the next blocking reconnect after it closes the old socket, having opened a socket pair of its own meanwhile.

    The pair stands in for anything else in the process: the lowest free
    descriptors are the ones the close just freed, so it takes them.
    """
    closed = threading.Event()
    resume = threading.Event()
    taken: list[tuple[socket.socket, socket.socket]] = []

    def after_closing() -> None:
        taken.append(socket.socketpair())
        closed.set()
        assert resume.wait(timeout=5), "the test never resumed the reconnect"

    broker.after_closing_in_reconnect = after_closing
    return closed, resume, taken


@pytest.mark.asyncio
async def test_a_hand_back_never_unwatches_a_descriptor_someone_else_took_meanwhile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reconnect closed the old socket on the thread; its number went to another socket before the hand-back.

    Unwatching by the remembered number would stop the loop reading that other
    socket -- another panel's bridge, Home Assistant's own MQTT, a resolver --
    which would then never hear from its peer again.
    """
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.3)
    loop = asyncio.get_running_loop()
    broker = _Broker(loop, _FLAT_TREE)
    broker.real_sockets = True
    heard: list[bytes] = []
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        broker.holding_connacks = True
        [client] = broker.clients
        client.drop()
        await _until(lambda: len(broker.sessions) == 2, "the first reconnect's CONNECT")
        awaiting = client.socket()
        assert awaiting is not None
        freed = awaiting.fileno()
        closed, resume, taken = _parked_after_closing(broker)
        await _until(closed.is_set, "the second reconnect to close the awaiting socket")
        [(other, peer)] = taken
        assert other.fileno() == freed or peer.fileno() == freed, "precondition: the freed number was taken"
        mine = other if other.fileno() == freed else peer
        theirs = peer if mine is other else other
        loop.add_reader(mine, lambda: heard.append(mine.recv(16)))

        resume.set()
        await _until(lambda: not bridge._executor_clients, "the hand-back")
        theirs.send(b"still here")
        await _until(lambda: bool(heard), "the other socket to be read")
        loop.remove_reader(mine)
        broker.after_closing_in_reconnect = None
        await bridge.disconnect()
    other.close()
    peer.close()

    assert heard == [b"still here"]


@pytest.mark.asyncio
async def test_a_blocking_reconnect_that_never_closes_its_socket_leaves_it_watched(monkeypatch: pytest.MonkeyPatch) -> None:
    """paho refuses a bad host before closing the socket it holds; the hand-back gives the loop that socket back."""
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.3)
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    broker.real_sockets = True
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        broker.holding_connacks = True
        [client] = broker.clients
        client.drop()
        await _until(lambda: len(broker.sessions) == 2, "the first reconnect's CONNECT")
        awaiting = client.socket()
        assert awaiting is not None
        broker.reconnecting.clear()
        broker.reconnect_fails_before_closing = ValueError("Invalid host.")
        await _until(broker.reconnecting.is_set, "the second reconnect")
        await _until(lambda: not bridge._executor_clients, "its hand-back")
        assert client.socket() is awaiting, "precondition: the failed reconnect kept its socket"

        awaiting.make_readable()
        await _until(lambda: awaiting.bytes_read > 0, "the loop to read that socket again")
        await bridge.disconnect()


@pytest.mark.asyncio
async def test_a_reconnect_the_executor_refuses_gives_the_loop_its_socket_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop stopped watching the client's open socket to hand it to a thread; refused, the call never ran, so it is watched again."""
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.3)
    loop = asyncio.get_running_loop()
    broker = _Broker(loop, _FLAT_TREE)
    broker.real_sockets = True
    real_run_in_executor = loop.run_in_executor
    refusals: list[int] = []

    def refuse_once(executor: None, call: Callable[[], object]) -> asyncio.Future[object]:
        if not refusals:
            refusals.append(1)
            raise RuntimeError("cannot schedule new futures after shutdown")
        return real_run_in_executor(executor, call)

    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        broker.holding_connacks = True
        [client] = broker.clients
        client.drop()
        await _until(lambda: len(broker.sessions) == 2, "the first reconnect's CONNECT")
        await _until(lambda: not bridge._executor_clients, "its hand-back")
        awaiting = client.socket()
        assert awaiting is not None
        with patch.object(loop, "run_in_executor", side_effect=refuse_once):
            await _until(lambda: bool(refusals), "the refused reconnect")
            awaiting.make_readable()
            await _until(lambda: awaiting.bytes_read > 0, "the loop to read that socket again")
        await bridge.disconnect()


@pytest.mark.asyncio
async def test_a_socket_paho_closes_on_the_loop_is_no_longer_watched() -> None:
    """A dropped connection's socket leaves no watcher behind for whatever takes its number next."""
    loop = asyncio.get_running_loop()
    broker = _Broker(loop, _FLAT_TREE)
    broker.real_sockets = True
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        [client] = broker.clients
        sock = client.socket()
        assert sock is not None
        number = sock.fileno()

        client.drop()
        # Checked at once, before anything else can run and take the number.
        still_watched = loop.remove_reader(number)
        await bridge.disconnect()

    assert not still_watched, "the closed socket's descriptor was left watched"


async def _ticks(client: _FakePaho, what: str) -> None:
    """Wait for `client`'s keepalive: the misc timer calling paho's loop_misc on it, once a second."""
    ticks = client.loop_misc_calls
    await _until(lambda: client.loop_misc_calls > ticks, what, timeout=3.0)


@pytest.mark.asyncio
async def test_a_bridge_reused_after_an_abandoned_connect_keeps_its_live_session_alive() -> None:
    """More than a second after the abandoned connect, the next session still gets its keepalives.

    The abandoned client's socket started the misc timer; with nobody to tick for
    it stopped, and a handle left set read as a timer still running, so the next
    socket started none and the broker dropped the session for its keepalive.
    """
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        gate = threading.Event()
        broker.connect_gate = gate
        await _disconnect_while_bridge_connects(broker, bridge, broker.connecting.is_set, gate.set)
        broker.connect_gate = None
        await asyncio.sleep(1.2)

        await bridge.connect()
        await _ticks(broker.clients[-1], "the live session's keepalive")
        await bridge.disconnect()


@pytest.mark.asyncio
async def test_a_failed_rebuild_leaves_the_old_client_its_keepalive_once_it_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failed rebuild's client started the misc timer; once the old client reconnects, the timer ticks for it.

    The reconnect comes more than a second later, so the timer has ticked once
    for the old client while it had no socket and stopped.
    """
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 1.3)
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        [old] = broker.clients
        broker.connect_fails_after_opening = ConnectionResetError(54, "Connection reset by peer")
        old.next_reconnect_error = ssl.SSLError("handshake failed")
        old.drop()
        await _until(lambda: len(broker.sessions) == 3 and bridge.is_connected(), "the old client's reconnect")
        assert len(broker.clients) == 2, "precondition: the rebuild built a client"
        assert old.session is broker.sessions[-1], "precondition: the failed rebuild left the old client in place"
        assert old.loop_misc_without_socket > 0, "precondition: the timer ticked while the old client had no socket"

        await _ticks(old, "the old client's keepalive")
        await bridge.disconnect()


@pytest.mark.asyncio
async def test_a_socket_close_whose_unwatching_raises_still_reports_the_disconnect() -> None:
    """paho reports the disconnect only after the socket-close callback returns; a removal that raised must not stop it."""
    loop = asyncio.get_running_loop()
    broker = _Broker(loop, _FLAT_TREE)
    broker.real_sockets = True
    real_remove_reader = loop.remove_reader
    raised: list[int] = []

    def remove_reader_raising_once(fd: int | _HasFileno) -> bool:
        if not raised:
            raised.append(1)
            raise RuntimeError("File descriptor is used by transport")
        return real_remove_reader(fd)

    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        [client] = broker.clients
        with patch.object(loop, "remove_reader", side_effect=remove_reader_raising_once):
            client.drop()
        await _until(lambda: len(broker.sessions) == 2 and bridge.is_connected(), "the reconnect")
        await bridge.disconnect()

    assert raised == [1], "precondition: the unwatching raised"


@pytest.mark.asyncio
async def test_keepalive_survives_a_reconnect_that_replaced_a_socket_awaiting_its_connack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replaced socket's close and the new socket's open are taken back in the order paho made them.

    Taken in reverse, the close would stop the misc timer the open had just
    left running, and the new session would get no keepalives.
    """
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.3)
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        broker.holding_connacks = True
        [client] = broker.clients
        client.drop()
        await _until(lambda: len(broker.sessions) == 2, "the first reconnect's CONNECT")
        await _until(lambda: len(broker.sessions) == 3, "the reconnect replacing the socket awaiting its CONNACK")
        broker.release_connacks()
        await _until(bridge.is_connected, "the CONNACK")

        await _ticks(client, "the live session's keepalive")
        await bridge.disconnect()


@pytest.mark.asyncio
async def test_an_idle_connected_socket_is_not_watched_for_writing() -> None:
    """Once the CONNECT is written paho asks the loop to stop watching for writing, and the loop must hear it.

    Left watched, a socket that is always writable wakes the loop on every
    turn: an idle connection spinning a core.
    """
    loop = asyncio.get_running_loop()
    broker = _Broker(loop, _FLAT_TREE)
    broker.real_sockets = True
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        [client] = broker.clients
        sock = client.socket()
        assert sock is not None
        await _until(lambda: not client.wants_to_write, "the CONNECT to be written")

        watched_for_writing = loop.remove_writer(sock)
        await bridge.disconnect()

    assert not watched_for_writing, "an idle socket was left watched for writing"


@pytest.mark.asyncio
async def test_the_misc_timer_does_not_tick_through_an_outage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With the socket gone the timer stops, rather than calling into a client that has nothing to keep alive.

    The window opens once the first refused reconnect has been handed back
    and logged. Nothing holds the client from then until the next attempt,
    half a minute away, so a tick still pending would call paho rather than
    look again later. Every tick is scheduled one second ahead, and the loop
    runs timers in the order they fall due, so the sleep after it ends only
    once any tick pending when the window opened has run.
    """
    monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 30.0)
    caplog.set_level(logging.WARNING, logger="span_panel_api.mqtt.connection")
    broker = _Broker(asyncio.get_running_loop(), _FLAT_TREE)
    with patch("span_panel_api.mqtt.connection.AsyncMQTTClient", broker.client):
        bridge = _bridge()
        await bridge.connect()
        [client] = broker.clients
        await _ticks(client, "the keepalive before the outage")
        broker.refusing = True
        client.drop()
        await _until(
            lambda: any(record.getMessage().startswith("Reconnect failed") for record in caplog.records),
            "the first refused reconnect, handed back",
        )
        await asyncio.sleep(1.01)
        ticks_without_a_socket = client.loop_misc_without_socket
        await bridge.disconnect()

    assert broker.clients == [client], "precondition: no rebuild inside the window"
    assert ticks_without_a_socket == 0, "the misc timer ticked into a client with no socket"
