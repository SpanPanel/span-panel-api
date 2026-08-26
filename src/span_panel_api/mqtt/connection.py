"""Async MQTT bridge — event-loop-driven paho-mqtt wrapper.

Follows Home Assistant core's async MQTT pattern:
- AsyncMQTTClient replaces paho's internal threading locks with NullLock
- Socket I/O driven by event loop's add_reader/add_writer
- loop_read()/loop_write()/loop_misc() called directly from event loop
- No background threads, no threading.Lock
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
import logging
import ssl
from typing import TYPE_CHECKING

import paho.mqtt.client as paho
from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from .._ssl import build_panel_ssl_context, ca_fingerprint
from ..auth import download_ca_cert
from ..exceptions import (
    SpanPanelAPIError,
    SpanPanelCAChangedError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)
from .async_client import AsyncMQTTClient
from .const import (
    MQTT_CONNECT_TIMEOUT_S,
    MQTT_FULL_REBUILD_AFTER_FAILURES,
    MQTT_KEEPALIVE_S,
    MQTT_RECONNECT_BACKOFF_MULTIPLIER,
    MQTT_RECONNECT_MAX_DELAY_S,
    MQTT_RECONNECT_MIN_DELAY_S,
)
from .models import MqttTransport

if TYPE_CHECKING:
    from paho.mqtt.client import SocketLike

_LOGGER = logging.getLogger(__name__)


class AsyncMqttBridge:
    """Event-loop-driven paho-mqtt wrapper with async callback dispatch.

    All paho I/O is driven by the asyncio event loop: socket reads/writes
    are registered via add_reader/add_writer, and loop_misc() runs on a
    periodic timer. No background threads are used.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        panel_host: str,
        serial_number: str,
        transport: MqttTransport = "tcp",
        use_tls: bool = True,
        loop: asyncio.AbstractEventLoop | None = None,
        panel_http_port: int | None = None,
        ca_pem: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._panel_host = panel_host
        self._serial_number = serial_number
        self._transport: MqttTransport = transport
        self._use_tls = use_tls
        self._loop = loop
        self._panel_http_port = panel_http_port
        # The pin. See `_trust_anchor_pem` for what supplying it changes and
        # `MqttClientConfig.ca_pem` for why `None` is still allowed.
        self._ca_pem = ca_pem
        self._warned_unpinned = False

        # Terminal state. Set only for a failure that retrying cannot fix, which
        # today means exactly one thing: the panel's advertised CA no longer
        # matches the pin. Everything else this bridge encounters is retried, so
        # this staying `None` is the normal condition even during a long outage.
        self._fatal_error: SpanPanelError | None = None
        self._fatal_error_callback: Callable[[SpanPanelError], None] | None = None

        # QoS-1 publishes awaiting a PUBACK, keyed by paho message id. Emptied
        # by `_on_publish` one at a time, and wholesale by
        # `_resolve_pending_publishes` when the outbound queue ceases to exist.
        self._pending_publishes: dict[int, asyncio.Future[bool]] = {}

        self._connected = False
        self._client: AsyncMQTTClient | None = None
        self._connect_event: asyncio.Event | None = None

        self._misc_timer: asyncio.TimerHandle | None = None
        self._should_reconnect = False
        self._initial_connect_done = False
        self._reconnect_task: asyncio.Task[None] | None = None

        self._message_callback: Callable[[str, str], None] | None = None
        self._connection_callback: Callable[[bool], None] | None = None
        self._pre_rebuild_callback: Callable[[], None] | None = None

    def is_connected(self) -> bool:
        """Return whether the MQTT client is currently connected."""
        return self._connected

    def set_message_callback(self, callback: Callable[[str, str], None]) -> None:
        """Set callback for incoming messages: callback(topic, payload)."""
        self._message_callback = callback

    def set_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Set callback for connection state changes: callback(is_connected)."""
        self._connection_callback = callback

    def set_fatal_error_callback(self, callback: Callable[[SpanPanelError], None]) -> None:
        """Set the callback fired when this bridge stops for good.

        The reconnect loop is created fire-and-forget, so nothing awaits it and
        nothing reads its exception. Raising inside it kills the task invisibly:
        the consumer sees a bridge that is merely disconnected and waits for a
        reconnect that will never be attempted. This is the channel that exists
        so it cannot.

        Fires at most once per bridge, from the event loop, with the same error
        `fatal_error` then holds. A subscriber that raises is logged and
        otherwise ignored -- there is nothing left to protect at that point, but
        an exception escaping here would still swallow the notification for a
        second subscriber added later.
        """
        self._fatal_error_callback = callback

    @property
    def fatal_error(self) -> SpanPanelError | None:
        """The failure this bridge stopped for, or None while it is still trying.

        Readable so a caller that registered no callback still cannot mistake a
        dead bridge for a disconnected one. `SpanMqttClient.ping()` and
        `get_snapshot()` consult it for exactly that reason.
        """
        return self._fatal_error

    def _enter_terminal_state(self, error: SpanPanelError) -> None:
        """Stop reconnecting, record why, and tell whoever asked to be told."""
        self._fatal_error = error
        # Stops `_reconnect_loop`'s while-condition and prevents `_on_disconnect`
        # from starting a replacement loop.
        self._should_reconnect = False
        _LOGGER.error("MQTT bridge to %s:%s has stopped permanently: %s", self._host, self._port, error)
        if self._fatal_error_callback is not None:
            try:
                self._fatal_error_callback(error)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Fatal-error callback raised", exc_info=True)

    def set_pre_rebuild_callback(self, callback: Callable[[], None]) -> None:
        """Set callback invoked just before the bridge rebuilds its paho client.

        Used by SpanMqttClient to reset its Homie accumulator so any stale
        in-memory state (e.g. cached `$state=disconnected`) is discarded
        before the new client subscribes and retained messages flow in.

        Callback runs synchronously inside `_rebuild_client` before the old
        paho client is torn down. Exceptions are caught and logged so a
        misbehaving subscriber cannot prevent the rebuild.
        """
        self._pre_rebuild_callback = callback

    async def _trust_anchor_pem(self) -> str:
        """The CA this connection is verified against.

        **With `ca_pem` supplied this makes no network call, on any path.** That
        is the whole of the pin, and it is the single most important line in this
        class. Before it, `_rebuild_client` refetched the CA on every reconnect
        and rebuilt the context from whatever came back — so a panel presenting a
        certificate from a *different* CA was silently re-anchored to it on the
        next reconnect. Automatic recovery from CA rotation and automatic
        acceptance of an interception are the same code path; there is no way to
        keep one without the other, and this chooses to keep neither.

        Without `ca_pem` the old behaviour stands, because requiring a pin would
        break every install on upgrade. It warns once per bridge rather than per
        connect: the fetch happens on every reconnect, and a warning per reconnect
        during a day-long outage is a log the user stops reading.

        Raises:
            SpanPanelConnectionError: unpinned, and the panel could not be reached.
            SpanPanelTimeoutError: unpinned, and the CA request timed out.
            SpanPanelAPIError: unpinned, and the panel did not answer with a PEM.
        """
        pinned = self._ca_pem
        if pinned is not None:
            return pinned
        if not self._warned_unpinned:
            self._warned_unpinned = True
            _LOGGER.warning(
                "MQTT trust anchor for %s was obtained unauthenticated: no ca_pem was configured, "
                "so the CA is fetched over plaintext HTTP from the panel on every connect and "
                "whatever answers is trusted. Pin the CA by setting MqttClientConfig.ca_pem.",
                self._panel_host,
            )
        return await download_ca_cert(self._panel_host, port=self._panel_http_port)

    async def _diagnose_ca_change(self) -> SpanPanelCAChangedError | None:
        """Decide whether a certificate-verification failure means the CA rotated.

        It usually does not, and the failure carries no evidence either way. A
        valid pinned CA still produces `SSLCertVerificationError` when the leaf
        has expired — a panel whose clock reset after a power outage, which for
        an electrical panel is not a corner case — or when the hostname no longer
        matches after the panel's address changed. And `ssl` exposes no peer
        chain on a verification failure, so the certificate that was actually
        offered cannot be read from the exception at all.

        So the observed fingerprint has to come from somewhere else: a separate,
        unauthenticated fetch of the panel's advertised CA. That fetch is
        **diagnostic only and is never used to re-anchor** — it is exactly the
        request an attacker would answer, and treating its result as a new trust
        anchor is the re-anchoring this class exists to stop.

        Three outcomes, and only one of them escalates:

        - Fingerprint matches the pin — the CA did not change. Some other TLS
          problem; the caller keeps retrying.
        - Fingerprint differs — the panel is advertising a different anchor.
          Returns the error to raise.
        - The fetch failed, or returned something that is not a certificate —
          returns None. **Never escalate on missing evidence.** A panel that is
          reachable on 8883 and not on its HTTP port is a panel mid-reboot, and
          declaring its CA changed on that basis would convert a transient into a
          permanent outage.

        Returns None immediately when nothing is pinned: with no anchor recorded
        there is nothing to compare against, and the unpinned path already
        re-anchors by design.
        """
        pinned = self._ca_pem
        if pinned is None:
            return None
        try:
            # Plaintext deliberately: if the CA really has rotated, a fetch
            # verified against the old pin would fail and tell us nothing.
            advertised = await download_ca_cert(self._panel_host, port=self._panel_http_port)
        except (OSError, SpanPanelError) as exc:
            _LOGGER.warning(
                "TLS verification failed for %s and the panel's CA could not be re-read to say why (%s). "
                "Treating this as transient and continuing to retry.",
                self._panel_host,
                exc,
            )
            return None
        try:
            expected = ca_fingerprint(pinned)
            observed = ca_fingerprint(advertised)
        except SpanPanelValidationError as exc:
            _LOGGER.warning("Could not fingerprint a CA certificate while diagnosing a TLS failure: %s", exc)
            return None
        if expected == observed:
            _LOGGER.warning(
                "TLS verification failed for %s, but the panel still advertises the pinned CA "
                "(SHA-256 %s). An expired certificate or a changed hostname would both look like "
                "this. Continuing to retry.",
                self._panel_host,
                expected,
            )
            return None
        return SpanPanelCAChangedError(expected, observed)

    def _make_paho_client(self, ssl_context: ssl.SSLContext | None) -> AsyncMQTTClient:
        """Build and wire a fresh paho client.

        Shared by connect() (initial connect) and _rebuild_client() (in-loop
        rebuild). Keeps the callback wiring in one place so a rebuild is
        provably symmetric with initial connect.
        """
        client = AsyncMQTTClient(
            callback_api_version=CallbackAPIVersion.VERSION2,
            transport=self._transport,
            reconnect_on_failure=False,
        )
        client.setup()
        client.username_pw_set(self._username, self._password)
        # Wire socket callbacks (async versions by default)
        client.on_socket_close = self._async_on_socket_close
        client.on_socket_unregister_write = self._async_on_socket_unregister_write
        # Wire MQTT callbacks (run directly on event loop — no thread dispatch)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_publish = self._on_publish
        if ssl_context is not None:
            client.tls_set_context(ssl_context)
        return client

    async def connect(self) -> None:
        """Connect to the MQTT broker.

        Resolves the TLS trust anchor — the configured pin, or a fetch from the
        panel when there is none — configures TLS, connects via executor
        (blocking I/O), and waits for CONNACK.

        Raises:
            SpanPanelConnectionError: Cannot connect to broker.
            SpanPanelTimeoutError: Connection timed out.
            SpanPanelCAChangedError: The panel is pinned and now advertises a
                different CA. If the CA rotated while the consumer was down, the
                pinned handshake fails here rather than in the reconnect loop,
                and wrapping it as a connection error would leave the consumer
                retrying setup forever with nothing to act on.
        """
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        self._connect_event = asyncio.Event()
        self._should_reconnect = True

        _LOGGER.debug(
            "BRIDGE: Resolving CA for %s (use_tls=%s, pinned=%s)", self._panel_host, self._use_tls, self._ca_pem is not None
        )
        ssl_context: ssl.SSLContext | None = None
        if self._use_tls:
            try:
                ca_pem = await self._trust_anchor_pem()
            except (OSError, SpanPanelConnectionError, SpanPanelTimeoutError) as exc:
                raise SpanPanelConnectionError(f"Failed to fetch CA certificate from {self._panel_host}") from exc
            # Build the SSLContext from PEM data in memory — no temp file.
            # A malformed PEM raises ssl.SSLError or ValueError; wrap both
            # so callers only see the documented SpanPanelConnectionError.
            try:
                ssl_context = build_panel_ssl_context(ca_pem)
            except (ssl.SSLError, ValueError) as exc:
                raise SpanPanelConnectionError(f"Failed to build SSL context for {self._panel_host}") from exc

        self._client = self._make_paho_client(ssl_context)

        # Connect in executor (blocking: DNS, TCP, TLS handshake).
        # During executor connect, socket callbacks bridge to the event
        # loop via call_soon_threadsafe.
        def _blocking_connect() -> None:
            if self._client is None:
                raise RuntimeError("MQTT client not initialised before connect")
            self._client.connect(
                host=self._host,
                port=self._port,
                keepalive=MQTT_KEEPALIVE_S,
            )

        try:
            self._client.on_socket_open = self._on_socket_open_sync
            self._client.on_socket_register_write = self._on_socket_register_write_sync
            _LOGGER.debug("BRIDGE: Running connect in executor to %s:%s", self._host, self._port)
            try:
                await self._loop.run_in_executor(None, _blocking_connect)
            except ssl.SSLCertVerificationError as exc:
                # The pinned handshake failed on the very first attempt, which is
                # what a CA rotated while the consumer was shut down looks like.
                # Wrapped as a connection error this became a setup-retry loop
                # with nothing for the user to act on, forever. `_diagnose_ca_change`
                # is what distinguishes it from an expired leaf or a moved host,
                # and returns None for both of those so they keep their old
                # retryable shape.
                fatal = await self._diagnose_ca_change()
                if fatal is not None:
                    raise fatal from exc
                raise SpanPanelConnectionError(f"Cannot connect to MQTT broker at {self._host}:{self._port}: {exc}") from exc
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # paho raises OSError for TCP failures and transport-specific
                # errors (e.g. WebsocketConnectionError) that do not inherit
                # from OSError. Wrap all of them uniformly so callers only
                # see the documented SpanPanelConnectionError.
                raise SpanPanelConnectionError(f"Cannot connect to MQTT broker at {self._host}:{self._port}: {exc}") from exc
            _LOGGER.debug("BRIDGE: Executor connect returned, waiting for CONNACK...")
        finally:
            # Switch to async-only socket callbacks now that we are
            # back on the event loop thread.
            self._client.on_socket_open = self._async_on_socket_open
            self._client.on_socket_register_write = self._async_on_socket_register_write

        # Wait for CONNACK
        try:
            await asyncio.wait_for(self._connect_event.wait(), timeout=MQTT_CONNECT_TIMEOUT_S)
        except TimeoutError as exc:
            await self.disconnect()
            raise SpanPanelTimeoutError(f"Timed out connecting to MQTT broker at {self._host}:{self._port}") from exc

        if not self._connected:
            raise SpanPanelConnectionError(f"MQTT connection failed to {self._host}:{self._port}")

        self._initial_connect_done = True

    async def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        self._should_reconnect = False

        # Cancel reconnect task
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        # Cancel misc timer
        if self._misc_timer is not None:
            self._misc_timer.cancel()
            self._misc_timer = None

        self._resolve_pending_publishes(False, "bridge disconnected")

        client = self._client
        if client is not None:
            client.disconnect()
        self._connected = False
        self._client = None
        self._initial_connect_done = False

    def subscribe(self, topic: str, qos: int = 0) -> None:
        """Subscribe to a topic. Must be called after connect()."""
        if self._client is not None:
            self._client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: str) -> asyncio.Future[bool] | None:
        """Hand one QoS-1 control message to paho, or refuse to hand it over.

        Returns ``None`` when the message was **not** handed over -- no client,
        or not connected. That is the only condition under which a caller may
        say the command failed, and the reason the check is here rather than on
        paho's return code afterwards.

        **The gate is only as fresh as paho's disconnect detection**, which is
        socket close or the keepalive at ``MQTT_KEEPALIVE_S``. A broker that
        stops answering without closing its socket -- a silent partition, a
        dropped route -- leaves ``is_connected()`` true for up to a keepalive
        interval and a half, and a publish in that window is handed over and
        queued after all, then re-sent as DUP on the next reconnect of this same
        client. Nothing here lies as a result: such a caller is told
        ``UNCONFIRMED``, which promises nothing about delivery either way, and
        ``FAILED``'s promise is unaffected because this path never produces it.
        What is bounded is the refusal, not the queueing: it catches every
        disconnect the transport knows about, and knows about a silent one only
        once the keepalive expires. Closing that window means rebuilding rather
        than reconnecting whenever un-PUBACKed publishes are in flight, which is
        a larger change than this one.

        **paho queues a QoS-1 publish across a disconnect.** On
        ``MQTT_ERR_NO_CONN`` it keeps the message in ``_out_messages`` with
        ``state = mqtt_ms_publish`` -- its own comment reads "remove from
        inflight messages so it will be send after a connection is made" -- and
        the reconnect path reuses the same client object. So a relay command
        published while the broker is down is not discarded: it fires whenever
        the broker returns, which on a firmware upgrade is minutes later and
        unannounced. Reading paho's return code and calling it a failure would
        tell a user their breaker command failed while the command was still
        pending delivery, and a user told that acts on it. The message must not
        reach paho at all.

        Otherwise returns a future that resolves:

        - ``True`` when the broker PUBACKs, and
        - ``False`` when a transport rebuild discards paho's outbound queue
          before that happens.

        ``False`` is genuinely ambiguous and the caller must treat it as such: a
        rebuilt client drops the message from this side, but the original may
        already have reached the broker and been acted on. It resolves the
        future rather than leaving it pending so nothing waits on a message no
        longer in flight -- it does not license reporting a failure.
        """
        client = self._client
        if client is None or not self._connected:
            return None

        info = client.publish(topic, payload=payload, qos=1)

        loop = self._loop
        if loop is None:
            loop = asyncio.get_running_loop()
            self._loop = loop
        acknowledged: asyncio.Future[bool] = loop.create_future()
        # Registered after `publish()` returns, which is safe only because paho's
        # callbacks here are driven by the event loop's reader callback: no
        # `loop_read` can run between the line above and this one, so the PUBACK
        # for this mid cannot arrive before there is a future to resolve. It
        # would be a real race under paho's own threaded loop.
        self._pending_publishes[info.mid] = acknowledged
        # Cleans up when the caller's deadline cancels the future, which is the
        # ordinary end for a message the broker never answers. Without it the
        # entry outlives every waiter and the map grows for the life of the
        # bridge. Guarded by identity in `_forget_publish` because paho's message
        # ids wrap.
        acknowledged.add_done_callback(partial(self._forget_publish, info.mid))
        return acknowledged

    def _forget_publish(self, mid: int, future: asyncio.Future[bool]) -> None:
        """Drop a settled publish, but only if it is still the one we recorded."""
        if self._pending_publishes.get(mid) is future:
            del self._pending_publishes[mid]

    def _resolve_pending_publishes(self, acknowledged: bool, reason: str) -> None:
        """Settle every publish still awaiting a PUBACK.

        Called when the outbound queue stops existing -- a rebuilt paho client,
        or teardown. Without this a caller waits out its whole deadline for an
        acknowledgement that is now impossible, and an audit trail shows a
        command in limbo with no terminal state.

        Settling the future is what *lets* a waiter stop early; it does not by
        itself stop one. A caller waiting on something else -- the property
        transition, in this client's case -- has to watch this future too, and
        `_discard_verification` is where that is wired up. The distinction is
        worth keeping straight: this method's job ends at making the evidence
        available.
        """
        if not self._pending_publishes:
            return
        _LOGGER.debug(
            "Settling %d in-flight publish(es) as acknowledged=%s: %s", len(self._pending_publishes), acknowledged, reason
        )
        for future in list(self._pending_publishes.values()):
            if not future.done():
                future.set_result(acknowledged)
        self._pending_publishes.clear()

    def _on_publish(
        self,
        _client: paho.Client,
        _userdata: object,
        mid: int,
        _reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        """Handle PUBACK — resolve whoever is waiting on this message id."""
        future = self._pending_publishes.get(mid)
        if future is not None and not future.done():
            future.set_result(True)

    # -- Socket callbacks (event-loop-driven I/O) ---------------------------

    def _async_reader_callback(self, client: paho.Client) -> None:
        """Handle reading data from the socket."""
        if (status := client.loop_read()) != 0:
            self._async_handle_loop_error(status)

    def _async_writer_callback(self, client: paho.Client) -> None:
        """Handle writing data to the socket."""
        if (status := client.loop_write()) != 0:
            self._async_handle_loop_error(status)

    def _async_handle_loop_error(self, status: int) -> None:
        """Handle a paho loop error."""
        _LOGGER.debug("MQTT loop error: %s", paho.error_string(status))

    def _async_start_misc_periodic(self) -> None:
        """Start the periodic loop_misc() timer (1-second interval)."""
        if self._loop is None:
            return
        loop = self._loop

        def _async_misc() -> None:
            if self._client is not None and self._client.loop_misc() == paho.MQTT_ERR_SUCCESS:
                self._misc_timer = loop.call_at(loop.time() + 1, _async_misc)

        self._misc_timer = loop.call_at(loop.time() + 1, _async_misc)

    # -- Socket open/close (sync bridges for executor, async for event loop) --

    def _on_socket_open_sync(self, client: paho.Client, userdata: object, sock: SocketLike) -> None:
        """Handle socket open during executor connect — bridge to event loop."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._async_on_socket_open, client, userdata, sock)

    def _async_on_socket_open(self, client: paho.Client, _userdata: object, sock: SocketLike) -> None:
        """Handle socket open on the event loop."""
        if self._loop is None:
            return
        fileno = sock.fileno()
        if fileno > -1:
            self._loop.add_reader(sock, partial(self._async_reader_callback, client))
        if not self._misc_timer:
            self._async_start_misc_periodic()
        # Drain initial buffer immediately
        self._async_reader_callback(client)

    def _async_on_socket_close(self, _client: paho.Client, _userdata: object, sock: SocketLike) -> None:
        """Handle socket close — remove reader, cancel misc timer."""
        if self._loop is None:
            return
        # Ensure connect event is signaled if socket closes early
        if self._connect_event is not None and not self._connect_event.is_set():
            self._connected = False
            self._connect_event.set()
        fileno = sock.fileno()
        if fileno > -1:
            self._loop.remove_reader(sock)
        if self._misc_timer is not None:
            self._misc_timer.cancel()
            self._misc_timer = None

    # -- Socket write registration ------------------------------------------

    def _on_socket_register_write_sync(self, client: paho.Client, userdata: object, sock: SocketLike) -> None:
        """Register socket for writing during executor connect."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._async_on_socket_register_write, client, userdata, sock)

    def _async_on_socket_register_write(self, client: paho.Client, _userdata: object, sock: SocketLike) -> None:
        """Register the socket for writing on the event loop."""
        if self._loop is None:
            return
        fileno = sock.fileno()
        if fileno > -1:
            self._loop.add_writer(sock, partial(self._async_writer_callback, client))

    def _async_on_socket_unregister_write(self, _client: paho.Client, _userdata: object, sock: SocketLike) -> None:
        """Unregister the socket for writing."""
        if self._loop is None:
            return
        fileno = sock.fileno()
        if fileno > -1:
            self._loop.remove_writer(sock)

    # -- MQTT callbacks (run directly on event loop) ------------------------

    def _on_connect(
        self,
        _client: paho.Client,
        _userdata: object,
        _flags: ConnectFlags,
        reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        """Handle CONNACK from broker."""
        connected = not reason_code.is_failure
        self._connected = connected

        if connected:
            _LOGGER.debug("MQTT connected to %s:%s", self._host, self._port)
            # Cancel reconnect loop on successful connection
            if self._reconnect_task is not None:
                self._reconnect_task.cancel()
                self._reconnect_task = None
        else:
            _LOGGER.warning("MQTT connection refused: %s", reason_code)

        # Signal the asyncio connect() waiter
        if self._connect_event is not None:
            self._connect_event.set()

        # Notify connection callback
        if self._connection_callback is not None:
            self._connection_callback(connected)

    def _on_disconnect(
        self,
        _client: paho.Client,
        _userdata: object,
        _flags: DisconnectFlags,
        reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        """Handle disconnect from broker."""
        self._connected = False
        if reason_code.is_failure:
            _LOGGER.warning("MQTT disconnected abnormally: %s", reason_code)
        else:
            _LOGGER.debug("MQTT disconnected: %s", reason_code)

        # Signal connect event if still waiting (socket closed before CONNACK)
        if self._connect_event is not None and not self._connect_event.is_set():
            self._connect_event.set()

        # Notify connection callback
        if self._connection_callback is not None:
            self._connection_callback(False)

        # Start reconnect loop (only after initial connect succeeded)
        if self._initial_connect_done and self._should_reconnect and self._reconnect_task is None and self._loop is not None:
            self._reconnect_task = self._loop.create_task(self._reconnect_loop(), name="span_mqtt_reconnect")

    def _on_message(
        self,
        _client: paho.Client,
        _userdata: object,
        msg: MQTTMessage,
    ) -> None:
        """Handle incoming MQTT message — dispatch directly on event loop."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace")

        if self._message_callback is not None:
            self._message_callback(topic, payload)

    # -- Reconnection -------------------------------------------------------

    async def _rebuild_client(self) -> bool:
        """Tear down the paho client and rebuild it from scratch.

        Replicates what a manual integration reload does without going
        through HA's config_entry teardown. Re-fetches the panel CA,
        builds a fresh paho client with the same callbacks, fires the
        pre-rebuild callback so SpanMqttClient can reset its accumulator,
        and submits an initial connect via the executor.

        Returns True when the new client was built and the initial connect
        was successfully submitted. Returns False on any failure (panel
        unreachable, CA endpoint down, executor connect raised) — the
        previous client is left in place and the reconnect loop continues
        retrying with it.

        Recovery target: CA rotation (firmware upgrade), stale paho client
        internal state, stuck Homie accumulator. See the design doc at
        SpanPanel_Docs/span-panel-api/2026-05-17-mqtt-ca-refresh-on-reconnect-design.md.
        """
        if self._loop is None:
            return False

        old_client = self._client

        # Resolve the trust anchor (TLS bridges only). With `ca_pem` configured
        # this is a pure read: a rebuild must not be a route back onto an anchor
        # the panel is currently offering, which is precisely what re-fetching
        # here used to make it. Unpinned, the fetch is the recovery it always
        # was, and a failure is non-fatal — the old client stays in place and the
        # loop retries on the next tick.
        ssl_context: ssl.SSLContext | None = None
        if self._use_tls:
            try:
                ca_pem = await self._trust_anchor_pem()
                ssl_context = build_panel_ssl_context(ca_pem)
            except (
                OSError,
                SpanPanelConnectionError,
                SpanPanelTimeoutError,
                SpanPanelAPIError,
                ssl.SSLError,
                ValueError,
            ) as exc:
                _LOGGER.warning("Client rebuild — CA fetch failed: %s", exc)
                return False

        # The new paho client has an empty outbound queue, so anything the old
        # one was still holding is gone. Settle those waiters now rather than
        # letting each burn its full deadline on an acknowledgement that can no
        # longer arrive. See `publish` for why `False` is not a failure.
        self._resolve_pending_publishes(False, "transport rebuild discarded the outbound queue")

        # Fire pre-rebuild hook before we touch any state. SpanMqttClient
        # uses this to discard its stale Homie accumulator so retained
        # messages on the new subscription start from a clean slate.
        if self._pre_rebuild_callback is not None:
            try:
                self._pre_rebuild_callback()
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Pre-rebuild callback raised", exc_info=True)

        # Everything past this point is wrapped in a broad catch so that
        # unexpected failures (paho construction errors, etc.) cannot kill
        # the reconnect task. The whole point of self-heal is that the
        # loop survives — we never want the recovery path itself to be a
        # source of unrecoverable failure.
        try:
            # Best-effort teardown of the old paho client. paho's disconnect()
            # is synchronous and only severs the socket; the object itself is
            # no longer used.
            if old_client is not None:
                try:
                    old_client.disconnect()
                except Exception:  # pylint: disable=broad-exception-caught
                    _LOGGER.debug("Old paho client disconnect raised", exc_info=True)

            # Build fresh client and assign it BEFORE the executor await so
            # that a CONNACK arriving during the await sees the right client.
            # Without this, the _on_connect → re-subscribe path would route
            # through self._client which would still be the (disconnected)
            # old_client, and the new client's subscription would never run.
            new_client = self._make_paho_client(ssl_context)
            new_client.on_socket_open = self._on_socket_open_sync
            new_client.on_socket_register_write = self._on_socket_register_write_sync
            self._client = new_client

            def _blocking_connect() -> None:
                new_client.connect(
                    host=self._host,
                    port=self._port,
                    keepalive=MQTT_KEEPALIVE_S,
                )

            try:
                await self._loop.run_in_executor(None, _blocking_connect)
            except asyncio.CancelledError:
                # Bridge teardown or _on_connect cancelled us mid-rebuild.
                # Restore the previous client reference so post-teardown
                # state stays consistent, then re-raise — CancelledError
                # must propagate to keep the loop's cancel semantics intact.
                self._client = old_client
                raise
            except Exception as exc:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Client rebuild — initial connect failed: %s", exc)
                # Restore the previous client so the loop keeps retrying
                # with what it had. The new client's socket was never opened.
                self._client = old_client
                return False
            finally:
                new_client.on_socket_open = self._async_on_socket_open
                new_client.on_socket_register_write = self._async_on_socket_register_write

            _LOGGER.info("MQTT client rebuilt for reconnect (TLS=%s)", self._use_tls)
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # _make_paho_client raised, or some other unforeseen failure
            # after the CA was fetched. Reconnect loop MUST survive — log
            # with traceback for triage and leave whatever client reference
            # is current in place. CancelledError is BaseException in 3.8+
            # so it bypasses this clause and propagates naturally.
            _LOGGER.warning("Client rebuild — unexpected error: %s", exc, exc_info=True)
            return False

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff.

        Every MQTT_FULL_REBUILD_AFTER_FAILURES consecutive non-SSL failures
        (or on any ssl.SSLError), rebuild the paho client from scratch —
        resetting any stale in-memory state, and re-fetching the panel CA when
        no pin is configured. Mirrors what a manual integration reload does
        without going through HA's config_entry teardown. The counter resets
        after every rebuild attempt (success or fail) and on
        `_connected == True`, so the cadence holds throughout extended outages.

        The loop ends on exactly one thing other than `disconnect()`: a
        confirmed CA change, which `_enter_terminal_state` records and announces
        before the `while` condition drops it out. Every other failure is
        retried, because every other failure is one the panel can recover from
        on its own.
        """
        delay = MQTT_RECONNECT_MIN_DELAY_S
        failures_since_rebuild_attempt = 0
        while self._should_reconnect:
            if not self._connected and self._client is not None:
                try:
                    if self._loop is None:
                        break
                    # Use sync socket callbacks for executor reconnect
                    self._client.on_socket_open = self._on_socket_open_sync
                    self._client.on_socket_register_write = self._on_socket_register_write_sync
                    await self._loop.run_in_executor(None, self._client.reconnect)
                except ssl.SSLCertVerificationError as exc:
                    # Certificate verification, specifically -- caught ahead of
                    # `ssl.SSLError` because it is a subclass, and separated from
                    # it because they mean different things. This one is the
                    # *only* shape a CA change can take. `SSLEOFError`, which the
                    # broad clause below still handles, is what a broker restart
                    # looks like mid-handshake: the ordinary shape of a firmware
                    # upgrade, and reading "CA changed, stop forever" into it
                    # would turn a four-minute reboot into a permanent outage.
                    if self._ca_pem is None:
                        # Unpinned: unchanged from 3.0.1. The refetch inside the
                        # rebuild is the recovery, because with nothing pinned
                        # the anchor is by definition whatever the panel last
                        # served.
                        _LOGGER.warning("Reconnect TLS verification failure (%s), rebuilding client", exc)
                        await self._rebuild_client()
                        failures_since_rebuild_attempt = 0
                    else:
                        fatal = await self._diagnose_ca_change()
                        if fatal is not None:
                            self._enter_terminal_state(fatal)
                            return
                        # Not the CA. An expired leaf or a moved host, both of
                        # which the panel or the network can still fix, so this
                        # is an ordinary failure. No immediate rebuild: with a
                        # pin, a rebuild changes nothing about trust and only
                        # discards whatever paho was still holding.
                        failures_since_rebuild_attempt += 1
                        _LOGGER.warning("Reconnect TLS verification failed (%s), retrying in %ss", exc, delay)
                except ssl.SSLError as exc:
                    # Every other TLS failure. Kept on the rebuild path it has
                    # always been on: a fresh paho client is a reasonable answer
                    # to a handshake that went wrong for a reason we cannot name.
                    _LOGGER.warning("Reconnect TLS failure (%s), rebuilding client", exc)
                    await self._rebuild_client()
                    failures_since_rebuild_attempt = 0
                except (OSError, TimeoutError) as exc:
                    # Expected transient failures — panel offline, DNS miss,
                    # socket timeout, refused connection. paho also wraps
                    # some TLS handshake errors as generic OSError on the
                    # executor connect path; the rebuild after threshold
                    # catches those.
                    failures_since_rebuild_attempt += 1
                    _LOGGER.warning("Reconnect failed (%s), retrying in %ss", exc, delay)
                    if failures_since_rebuild_attempt >= MQTT_FULL_REBUILD_AFTER_FAILURES:
                        await self._rebuild_client()
                        failures_since_rebuild_attempt = 0
                except Exception:  # pylint: disable=broad-exception-caught
                    # Unknown territory — keep the traceback so support tickets
                    # are actionable. Never let the reconnect loop die. No
                    # rebuild here — unknown errors should not be masked
                    # behind a recovery action whose effect we cannot predict.
                    failures_since_rebuild_attempt += 1
                    _LOGGER.warning("Reconnect failed, retrying in %ss", delay, exc_info=True)
                finally:
                    if self._client is not None:
                        self._client.on_socket_open = self._async_on_socket_open
                        self._client.on_socket_register_write = self._async_on_socket_register_write
            else:
                failures_since_rebuild_attempt = 0
            await asyncio.sleep(delay)
            delay = min(
                delay * MQTT_RECONNECT_BACKOFF_MULTIPLIER,
                MQTT_RECONNECT_MAX_DELAY_S,
            )
