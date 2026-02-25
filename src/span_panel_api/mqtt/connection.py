"""Async MQTT bridge wrapping paho-mqtt v2.

Thread-safe paho-mqtt wrapper that bridges paho's threaded callbacks
to an asyncio event loop via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path
import ssl
import tempfile
import threading

import paho.mqtt.client as paho
from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from ..auth import download_ca_cert
from ..exceptions import SpanPanelConnectionError, SpanPanelTimeoutError
from .const import (
    MQTT_CONNECT_TIMEOUT_S,
    MQTT_KEEPALIVE_S,
    MQTT_RECONNECT_MAX_DELAY_S,
    MQTT_RECONNECT_MIN_DELAY_S,
    STATE_TOPIC_FMT,
)
from .models import MqttTransport

_LOGGER = logging.getLogger(__name__)


class AsyncMqttBridge:
    """Thread-safe paho-mqtt wrapper with async callback dispatch.

    The threading.Lock protects shared state accessed from both paho's
    network thread (_on_connect, _on_disconnect, _on_message) and the
    asyncio event loop thread (is_connected, subscribe, publish,
    disconnect). Lock holds are sub-microsecond (single attribute
    reads/writes) and do not meaningfully block the event loop.
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

        self._lock = threading.Lock()
        self._connected = False
        self._client: paho.Client | None = None
        self._connect_event: asyncio.Event | None = None
        self._ca_cert_path: Path | None = None

        self._message_callback: Callable[[str, str], None] | None = None
        self._connection_callback: Callable[[bool], None] | None = None

    def is_connected(self) -> bool:
        """Return whether the MQTT client is currently connected."""
        with self._lock:
            return self._connected

    def set_message_callback(self, callback: Callable[[str, str], None]) -> None:
        """Set callback for incoming messages: callback(topic, payload)."""
        with self._lock:
            self._message_callback = callback

    def set_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Set callback for connection state changes: callback(is_connected)."""
        with self._lock:
            self._connection_callback = callback

    async def connect(self) -> None:
        """Connect to the MQTT broker.

        Fetches the CA certificate from the panel, configures TLS,
        and waits for the CONNACK.

        Raises:
            SpanPanelConnectionError: Cannot connect to broker.
            SpanPanelTimeoutError: Connection timed out.
        """
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        self._connect_event = asyncio.Event()

        # Fetch CA cert from panel for TLS
        ca_cert_path: Path | None = None
        if self._use_tls:
            try:
                pem = await download_ca_cert(self._panel_host)
            except Exception as exc:
                raise SpanPanelConnectionError(f"Failed to fetch CA certificate from {self._panel_host}") from exc

            # Write PEM to temp file for paho's tls_set()
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)  # noqa: SIM115
            tmp.write(pem)
            tmp.close()
            ca_cert_path = Path(tmp.name)

        try:
            self._client = paho.Client(
                callback_api_version=CallbackAPIVersion.VERSION2,
                transport=self._transport,
            )
            self._client.username_pw_set(self._username, self._password)

            if self._use_tls and ca_cert_path is not None:
                self._client.tls_set(
                    ca_certs=str(ca_cert_path),
                    cert_reqs=ssl.CERT_REQUIRED,
                    tls_version=ssl.PROTOCOL_TLS_CLIENT,
                )

            # Last Will and Testament
            lwt_topic = STATE_TOPIC_FMT.format(serial=self._serial_number)
            self._client.will_set(lwt_topic, payload="lost", qos=1, retain=True)

            # Reconnect backoff
            self._client.reconnect_delay_set(
                min_delay=int(MQTT_RECONNECT_MIN_DELAY_S),
                max_delay=int(MQTT_RECONNECT_MAX_DELAY_S),
            )

            # Wire paho callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Start background network loop
            self._client.loop_start()

            # Initiate connection (non-blocking with loop_start)
            self._client.connect_async(self._host, self._port, keepalive=MQTT_KEEPALIVE_S)

            # Wait for CONNACK
            try:
                await asyncio.wait_for(self._connect_event.wait(), timeout=MQTT_CONNECT_TIMEOUT_S)
            except asyncio.TimeoutError as exc:
                await self.disconnect()
                raise SpanPanelTimeoutError(f"Timed out connecting to MQTT broker at {self._host}:{self._port}") from exc

            if not self._connected:
                raise SpanPanelConnectionError(f"MQTT connection failed to {self._host}:{self._port}")

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
        """Disconnect from the MQTT broker and stop the network loop."""
        client = self._client
        if client is not None:
            client.disconnect()
            client.loop_stop()
        with self._lock:
            self._connected = False
            self._client = None
        if self._ca_cert_path is not None:
            try:
                self._ca_cert_path.unlink()
            except OSError:
                _LOGGER.debug("Failed to remove temp CA cert file: %s", self._ca_cert_path)
            self._ca_cert_path = None

    def subscribe(self, topic: str, qos: int = 0) -> None:
        """Subscribe to a topic. Must be called after connect()."""
        with self._lock:
            client = self._client
        if client is not None:
            client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: str, qos: int = 1) -> None:
        """Publish a message. Must be called after connect()."""
        with self._lock:
            client = self._client
        if client is not None:
            client.publish(topic, payload=payload, qos=qos)

    # -- paho callbacks (called from paho's network thread) ----------------

    def _on_connect(
        self,
        _client: paho.Client,
        _userdata: object,
        _flags: ConnectFlags,
        reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        """Handle CONNACK from broker."""
        connected = reason_code == ReasonCode(paho.CONNACK_ACCEPTED)
        with self._lock:
            self._connected = connected

        if connected:
            _LOGGER.debug("MQTT connected to %s:%s", self._host, self._port)
        else:
            _LOGGER.warning("MQTT connection refused: %s", reason_code)

        # Signal the asyncio connect() waiter
        if self._loop is not None and self._connect_event is not None:
            self._loop.call_soon_threadsafe(self._connect_event.set)

        # Notify connection callback
        with self._lock:
            cb = self._connection_callback
        if cb is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(cb, connected)

    def _on_disconnect(
        self,
        _client: paho.Client,
        _userdata: object,
        _flags: DisconnectFlags,
        reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        """Handle disconnect from broker."""
        with self._lock:
            self._connected = False
        _LOGGER.debug("MQTT disconnected: %s", reason_code)

        with self._lock:
            cb = self._connection_callback
        if cb is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(cb, False)

    def _on_message(
        self,
        _client: paho.Client,
        _userdata: object,
        msg: MQTTMessage,
    ) -> None:
        """Handle incoming message — dispatch to asyncio loop."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace")

        with self._lock:
            cb = self._message_callback
        if cb is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(cb, topic, payload)
