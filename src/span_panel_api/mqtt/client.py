"""SPAN Panel MQTT client.

Composes AsyncMqttBridge and HomieDeviceConsumer to implement
SpanPanelClientProtocol, CircuitControlProtocol, and
StreamingCapableProtocol.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging

from ..exceptions import SpanPanelConnectionError
from ..models import SpanPanelSnapshot
from ..protocol import PanelCapability
from .connection import AsyncMqttBridge
from .const import MQTT_READY_TIMEOUT_S, PROPERTY_SET_TOPIC_FMT, WILDCARD_TOPIC_FMT
from .homie import HomieDeviceConsumer
from .models import MqttClientConfig

_LOGGER = logging.getLogger(__name__)

# How long to wait for circuit name properties after device ready.
# Retained messages typically arrive within 1-2s, but allow headroom.
_CIRCUIT_NAMES_TIMEOUT_S = 10.0
_CIRCUIT_NAMES_POLL_INTERVAL_S = 0.25


class SpanMqttClient:
    """MQTT transport — implements all span-panel-api protocols."""

    def __init__(
        self,
        host: str,
        serial_number: str,
        broker_config: MqttClientConfig,
    ) -> None:
        self._host = host
        self._serial_number = serial_number
        self._broker_config = broker_config

        self._bridge: AsyncMqttBridge | None = None
        self._homie = HomieDeviceConsumer(serial_number)
        self._streaming = False
        self._snapshot_callbacks: list[Callable[[SpanPanelSnapshot], Awaitable[None]]] = []
        self._ready_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

    # -- SpanPanelClientProtocol -------------------------------------------

    @property
    def capabilities(self) -> PanelCapability:
        """Advertise MQTT transport capabilities."""
        return (
            PanelCapability.EBUS_MQTT
            | PanelCapability.PUSH_STREAMING
            | PanelCapability.CIRCUIT_CONTROL
            | PanelCapability.BATTERY_SOE
        )

    @property
    def serial_number(self) -> str:
        """Return the panel serial number."""
        return self._serial_number

    async def connect(self) -> None:
        """Connect to MQTT broker and wait for Homie device ready.

        Flow:
        1. Create AsyncMqttBridge with broker credentials
        2. Connect to MQTT broker
        3. Subscribe to ebus/5/{serial}/#
        4. Wait for $state==ready and $description parsed

        Raises:
            SpanPanelConnectionError: Cannot connect or device not ready
            SpanPanelTimeoutError: Connection or ready timed out
        """
        self._loop = asyncio.get_running_loop()
        self._ready_event = asyncio.Event()

        _LOGGER.debug(
            "MQTT: Creating bridge to %s:%s (serial=%s)",
            self._broker_config.broker_host,
            self._broker_config.effective_port,
            self._serial_number,
        )

        self._bridge = AsyncMqttBridge(
            host=self._broker_config.broker_host,
            port=self._broker_config.effective_port,
            username=self._broker_config.username,
            password=self._broker_config.password,
            panel_host=self._host,
            serial_number=self._serial_number,
            transport=self._broker_config.transport,
            use_tls=self._broker_config.use_tls,
            loop=self._loop,
        )

        # Wire message handler
        self._bridge.set_message_callback(self._on_message)
        self._bridge.set_connection_callback(self._on_connection_change)

        # Connect to broker
        _LOGGER.debug("MQTT: Connecting to broker...")
        await self._bridge.connect()
        _LOGGER.debug("MQTT: Broker connected, subscribing...")

        # Subscribe to all device topics
        wildcard = WILDCARD_TOPIC_FMT.format(serial=self._serial_number)
        self._bridge.subscribe(wildcard, qos=0)
        _LOGGER.debug("MQTT: Subscribed to %s, waiting for Homie ready...", wildcard)

        # Wait for Homie ready state
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=MQTT_READY_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            await self.close()
            raise SpanPanelConnectionError(f"Timed out waiting for Homie device ready ({self._serial_number})") from exc

        _LOGGER.debug("MQTT: Homie device ready, waiting for circuit names...")

        # Wait for circuit name properties to arrive (retained messages
        # may arrive after $state=ready). Without this, the first snapshot
        # has empty circuit names and entities are created without labels.
        await self._wait_for_circuit_names(timeout=_CIRCUIT_NAMES_TIMEOUT_S)
        _LOGGER.debug("MQTT: Connection fully established")

    async def close(self) -> None:
        """Disconnect from broker and clean up."""
        self._streaming = False
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()
        if self._bridge is not None:
            await self._bridge.disconnect()
            self._bridge = None

    async def ping(self) -> bool:
        """Check if MQTT connection is alive and device is ready."""
        if self._bridge is None:
            return False
        return self._bridge.is_connected() and self._homie.is_ready()

    async def get_snapshot(self) -> SpanPanelSnapshot:
        """Return current snapshot from accumulated MQTT state.

        No network call — snapshot is built from in-memory property values.
        """
        return self._homie.build_snapshot()

    # -- CircuitControlProtocol --------------------------------------------

    async def set_circuit_relay(self, circuit_id: str, state: str) -> None:
        """Publish relay state change for a circuit.

        Args:
            circuit_id: Dashless UUID (matches wire format)
            state: "OPEN" or "CLOSED"
        """
        topic = PROPERTY_SET_TOPIC_FMT.format(serial=self._serial_number, node=circuit_id, prop="relay")
        if self._bridge is not None:
            self._bridge.publish(topic, state, qos=1)

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> None:
        """Publish shed-priority change for a circuit.

        Args:
            circuit_id: Dashless UUID (matches wire format)
            priority: v2 enum value (NEVER, SOC_THRESHOLD, OFF_GRID)
        """
        topic = PROPERTY_SET_TOPIC_FMT.format(serial=self._serial_number, node=circuit_id, prop="shed-priority")
        if self._bridge is not None:
            self._bridge.publish(topic, priority, qos=1)

    # -- StreamingCapableProtocol ------------------------------------------

    def register_snapshot_callback(
        self,
        callback: Callable[[SpanPanelSnapshot], Awaitable[None]],
    ) -> Callable[[], None]:
        """Register a callback to receive snapshot updates.

        Returns an unregister function.
        """
        self._snapshot_callbacks.append(callback)

        def unregister() -> None:
            try:
                self._snapshot_callbacks.remove(callback)
            except ValueError:
                _LOGGER.debug("Snapshot callback already unregistered")

        return unregister

    async def start_streaming(self) -> None:
        """Enable snapshot callback dispatch on property changes."""
        self._streaming = True

    async def stop_streaming(self) -> None:
        """Disable snapshot callback dispatch."""
        self._streaming = False

    # -- Internal callbacks ------------------------------------------------

    def _on_message(self, topic: str, payload: str) -> None:
        """Handle incoming MQTT message (called from asyncio loop)."""
        was_ready = self._homie.is_ready()
        self._homie.handle_message(topic, payload)

        # Check if device just became ready
        if not was_ready and self._homie.is_ready() and self._ready_event is not None:
            self._ready_event.set()

        # Dispatch snapshot callbacks if streaming
        if self._streaming and self._homie.is_ready() and self._loop is not None:
            task = self._loop.create_task(
                self._dispatch_snapshot(),
                name="span_mqtt_dispatch_snapshot",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    def _on_connection_change(self, connected: bool) -> None:
        """Handle MQTT connection state change (called from asyncio loop)."""
        if connected:
            _LOGGER.debug("MQTT connection established")
            # Re-subscribe on reconnect
            if self._bridge is not None:
                wildcard = WILDCARD_TOPIC_FMT.format(serial=self._serial_number)
                self._bridge.subscribe(wildcard, qos=0)
        else:
            _LOGGER.debug("MQTT connection lost")

    async def _wait_for_circuit_names(self, timeout: float) -> None:
        """Wait for all circuit-like nodes to have a ``name`` property.

        Retained MQTT messages may arrive after the Homie device transitions
        to ready. This polls the HomieDeviceConsumer at short intervals and
        returns as soon as all circuit names are populated, or when the
        timeout elapses (non-fatal — entities will use fallback names).
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            missing = self._homie.circuit_nodes_missing_names()
            if not missing:
                _LOGGER.debug("All circuit names received")
                return
            await asyncio.sleep(_CIRCUIT_NAMES_POLL_INTERVAL_S)

        still_missing = self._homie.circuit_nodes_missing_names()
        if still_missing:
            _LOGGER.warning(
                "Timed out waiting for circuit names (%d still missing): %s",
                len(still_missing),
                still_missing[:5],
            )

    async def _dispatch_snapshot(self) -> None:
        """Build snapshot and send to all registered callbacks."""
        snapshot = self._homie.build_snapshot()
        for cb in list(self._snapshot_callbacks):
            try:
                await cb(snapshot)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Snapshot callback error", exc_info=True)
