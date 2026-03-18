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
from pathlib import Path
import ssl
import tempfile
from typing import TYPE_CHECKING

import paho.mqtt.client as paho
from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from ..auth import download_ca_cert
from ..exceptions import SpanPanelConnectionError, SpanPanelTimeoutError
from .async_client import AsyncMQTTClient
from .const import (
    MQTT_CONNECT_TIMEOUT_S,
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
        panel_http_port: int = 80,
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

        self._connected = False
        self._client: AsyncMQTTClient | None = None
        self._connect_event: asyncio.Event | None = None
        self._ca_cert_path: Path | None = None

        self._misc_timer: asyncio.TimerHandle | None = None
        self._should_reconnect = False
        self._initial_connect_done = False
        self._reconnect_task: asyncio.Task[None] | None = None

        self._message_callback: Callable[[str, str], None] | None = None
        self._connection_callback: Callable[[bool], None] | None = None

    def is_connected(self) -> bool:
        """Return whether the MQTT client is currently connected."""
        return self._connected

    def set_message_callback(self, callback: Callable[[str, str], None]) -> None:
        """Set callback for incoming messages: callback(topic, payload)."""
        self._message_callback = callback

    def set_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Set callback for connection state changes: callback(is_connected)."""
        self._connection_callback = callback

    async def connect(self) -> None:
        """Connect to the MQTT broker.

        Fetches the CA certificate from the panel, configures TLS,
        connects via executor (blocking I/O), and waits for CONNACK.

        Raises:
            SpanPanelConnectionError: Cannot connect to broker.
            SpanPanelTimeoutError: Connection timed out.
        """
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        self._connect_event = asyncio.Event()
        self._should_reconnect = True

        # Fetch CA cert from panel for TLS
        _LOGGER.debug("BRIDGE: Fetching CA cert from %s (use_tls=%s)", self._panel_host, self._use_tls)
        ca_cert_path: Path | None = None
        if self._use_tls:
            try:
                pem = await download_ca_cert(self._panel_host, port=self._panel_http_port)
            except Exception as exc:
                raise SpanPanelConnectionError(f"Failed to fetch CA certificate from {self._panel_host}") from exc

            # Write PEM to temp file for paho's tls_set()
            tmp = tempfile.NamedTemporaryFile(  # pylint: disable=consider-using-with  # noqa: SIM115
                mode="w", suffix=".pem", delete=False
            )
            tmp.write(pem)
            tmp.close()
            ca_cert_path = Path(tmp.name)

        try:
            self._client = AsyncMQTTClient(
                callback_api_version=CallbackAPIVersion.VERSION2,
                transport=self._transport,
                reconnect_on_failure=False,
            )
            self._client.setup()

            self._client.username_pw_set(self._username, self._password)

            # Wire socket callbacks (async versions by default)
            self._client.on_socket_close = self._async_on_socket_close
            self._client.on_socket_unregister_write = self._async_on_socket_unregister_write

            # Wire MQTT callbacks (run directly on event loop — no thread dispatch)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # TLS setup + connect in executor (blocking file I/O for
            # load_verify_locations, DNS, TCP, and TLS handshake).
            # During executor connect, socket callbacks bridge to the event
            # loop via call_soon_threadsafe.
            def _blocking_tls_and_connect() -> None:
                """Run TLS configuration and connect in executor thread."""
                if self._client is None:
                    raise RuntimeError("MQTT client not initialised before connect")
                if self._use_tls and ca_cert_path is not None:
                    self._client.tls_set(
                        ca_certs=str(ca_cert_path),
                        cert_reqs=ssl.CERT_REQUIRED,
                        tls_version=ssl.PROTOCOL_TLS_CLIENT,
                    )
                self._client.connect(
                    host=self._host,
                    port=self._port,
                    keepalive=MQTT_KEEPALIVE_S,
                )

            try:
                self._client.on_socket_open = self._on_socket_open_sync
                self._client.on_socket_register_write = self._on_socket_register_write_sync
                _LOGGER.debug("BRIDGE: Running TLS+connect in executor to %s:%s", self._host, self._port)
                try:
                    await self._loop.run_in_executor(None, _blocking_tls_and_connect)
                except OSError as exc:
                    raise SpanPanelConnectionError(
                        f"Cannot connect to MQTT broker at {self._host}:{self._port}: {exc}"
                    ) from exc
                _LOGGER.debug("BRIDGE: Executor connect returned, waiting for CONNACK...")
            finally:
                # Switch to async-only socket callbacks now that we are
                # back on the event loop thread.
                self._client.on_socket_open = self._async_on_socket_open
                self._client.on_socket_register_write = self._async_on_socket_register_write

            # Wait for CONNACK
            try:
                await asyncio.wait_for(self._connect_event.wait(), timeout=MQTT_CONNECT_TIMEOUT_S)
            except asyncio.TimeoutError as exc:
                await self.disconnect()
                raise SpanPanelTimeoutError(f"Timed out connecting to MQTT broker at {self._host}:{self._port}") from exc

            if not self._connected:
                raise SpanPanelConnectionError(f"MQTT connection failed to {self._host}:{self._port}")

            self._initial_connect_done = True
            # Keep cert alive until disconnect — paho may reference it
            self._ca_cert_path = ca_cert_path

        except Exception:
            # Clean up temp CA cert file on failure only
            if ca_cert_path is not None:
                try:
                    ca_cert_path.unlink()
                except OSError:
                    _LOGGER.debug("Failed to remove temp CA cert file: %s", ca_cert_path)
            raise

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

        client = self._client
        if client is not None:
            client.disconnect()
        self._connected = False
        self._client = None
        self._initial_connect_done = False

        if self._ca_cert_path is not None:
            try:
                self._ca_cert_path.unlink()
            except OSError:
                _LOGGER.debug("Failed to remove temp CA cert file: %s", self._ca_cert_path)
            self._ca_cert_path = None

    def subscribe(self, topic: str, qos: int = 0) -> None:
        """Subscribe to a topic. Must be called after connect()."""
        if self._client is not None:
            self._client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: str, qos: int = 1) -> None:
        """Publish a message. Must be called after connect()."""
        if self._client is not None:
            self._client.publish(topic, payload=payload, qos=qos)

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

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff."""
        delay = MQTT_RECONNECT_MIN_DELAY_S
        while self._should_reconnect:
            if not self._connected and self._client is not None:
                try:
                    if self._loop is None:
                        break
                    # Use sync socket callbacks for executor reconnect
                    self._client.on_socket_open = self._on_socket_open_sync
                    self._client.on_socket_register_write = self._on_socket_register_write_sync
                    await self._loop.run_in_executor(None, self._client.reconnect)
                except OSError:
                    _LOGGER.debug("Reconnect failed, retrying in %ss", delay)
                finally:
                    if self._client is not None:
                        self._client.on_socket_open = self._async_on_socket_open
                        self._client.on_socket_register_write = self._async_on_socket_register_write
            await asyncio.sleep(delay)
            delay = min(
                delay * MQTT_RECONNECT_BACKOFF_MULTIPLIER,
                MQTT_RECONNECT_MAX_DELAY_S,
            )
