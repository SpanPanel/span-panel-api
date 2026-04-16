"""SPAN Panel MQTT client.

Composes AsyncMqttBridge and HomieDeviceConsumer to implement
SpanPanelClientProtocol, CircuitControlProtocol,
PanelControlProtocol, and StreamingCapableProtocol.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import logging

from ..auth import get_homie_schema
from ..exceptions import SpanPanelConnectionError, SpanPanelServerError
from ..models import FieldMetadata, HomieSchemaTypes, SpanPanelSnapshot
from ..protocol import PanelCapability
from .accumulator import HomiePropertyAccumulator
from .connection import AsyncMqttBridge
from .const import MQTT_READY_TIMEOUT_S, PROPERTY_SET_TOPIC_FMT, TYPE_CORE, WILDCARD_TOPIC_FMT
from .field_metadata import build_field_metadata, log_schema_drift
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
        snapshot_interval: float = 1.0,
        panel_http_port: int = 80,
    ) -> None:
        self._host = host
        self._serial_number = serial_number
        self._broker_config = broker_config
        self._snapshot_interval = snapshot_interval
        self._panel_http_port = panel_http_port

        self._bridge: AsyncMqttBridge | None = None
        self._accumulator: HomiePropertyAccumulator | None = None
        self._homie: HomieDeviceConsumer | None = None
        self._streaming = False
        self._snapshot_callbacks: list[Callable[[SpanPanelSnapshot], Awaitable[None]]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._live = False
        self._ready_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._snapshot_timer: asyncio.TimerHandle | None = None
        self._field_metadata: dict[str, FieldMetadata] | None = None
        self._schema_hash: str | None = None
        self._previous_schema_types: HomieSchemaTypes | None = None

    def _require_homie(self) -> HomieDeviceConsumer:
        """Return the HomieDeviceConsumer, raising if not yet connected."""
        if self._homie is None:
            raise SpanPanelConnectionError("Client not connected — call connect() first")
        return self._homie

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

    @property
    def field_metadata(self) -> dict[str, FieldMetadata] | None:
        """Schema-derived metadata for snapshot fields, or None before connect().

        Keyed by snapshot field path (e.g. ``"panel.instant_grid_power_w"``).
        Built once during ``connect()`` from the Homie schema.
        """
        return self._field_metadata

    async def connect(self) -> None:
        """Connect to MQTT broker and wait for Homie device ready.

        Flow:
        1. Fetch Homie schema to determine panel size
        2. Create AsyncMqttBridge with broker credentials
        3. Connect to MQTT broker
        4. Subscribe to ebus/5/{serial}/#
        5. Wait for $state==ready and $description parsed

        Raises:
            SpanPanelConnectionError: Cannot connect or device not ready
            SpanPanelTimeoutError: Connection or ready timed out
        """
        self._loop = asyncio.get_running_loop()
        self._ready_event = asyncio.Event()

        # Fetch schema to determine panel size and build field metadata
        schema = await get_homie_schema(self._host, port=self._panel_http_port)
        self._accumulator = HomiePropertyAccumulator(self._serial_number)
        self._homie = HomieDeviceConsumer(self._accumulator, schema.panel_size)

        # Detect schema drift from previous connection
        new_hash = schema.types_schema_hash
        if self._schema_hash is not None and new_hash != self._schema_hash:
            _LOGGER.debug(
                "Homie schema hash changed: %s → %s (firmware update may have modified the property schema)",
                self._schema_hash,
                new_hash,
            )
            if self._previous_schema_types is not None:
                log_schema_drift(self._previous_schema_types, schema.types)
        self._schema_hash = new_hash
        self._previous_schema_types = schema.types

        # Build transport-agnostic field metadata from schema
        self._field_metadata = build_field_metadata(schema.types)

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
            panel_http_port=self._panel_http_port,
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
        self._cancel_snapshot_timer()
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()
        if self._bridge is not None:
            await self._bridge.disconnect()
            self._bridge = None
        self._accumulator = None

    async def ping(self) -> bool:
        """Check if MQTT connection is alive and device is ready."""
        if self._bridge is None or self._homie is None:
            return False
        return self._bridge.is_connected() and self._homie.is_ready()

    def register_connection_callback(self, callback: Callable[[bool], None]) -> Callable[[], None]:
        """Subscribe to broker connection state transitions.

        Callback fires with False on broker disconnect and True on reconnect.
        No synthetic call is made at registration time — callbacks only fire
        on real state edges. To check current connection state on registration,
        call ping().

        Returns an unregister function that removes the callback from the
        dispatch list. Calling unregister twice is safe.
        """
        self._connection_callbacks.append(callback)

        def unregister() -> None:
            with contextlib.suppress(ValueError):
                self._connection_callbacks.remove(callback)

        return unregister

    async def get_snapshot(self) -> SpanPanelSnapshot:
        """Return current snapshot from accumulated MQTT state.

        No network call — snapshot is built from in-memory property values.
        """
        return self._require_homie().build_snapshot()

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

    # -- PanelControlProtocol ----------------------------------------------

    async def set_dominant_power_source(self, value: str) -> None:
        """Publish dominant-power-source change to the core node.

        Args:
            value: DPS enum value (GRID, BATTERY, NONE, GENERATOR, PV)
        """
        core_node = self._require_homie().find_node_by_type(TYPE_CORE)
        if core_node is None:
            raise SpanPanelServerError("Core node not found in panel topology")
        topic = PROPERTY_SET_TOPIC_FMT.format(serial=self._serial_number, node=core_node, prop="dominant-power-source")
        if self._bridge is not None:
            self._bridge.publish(topic, value, qos=1)

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
        self._cancel_snapshot_timer()

    # -- Internal callbacks ------------------------------------------------

    def _on_message(self, topic: str, payload: str) -> None:
        """Handle incoming MQTT message (called from asyncio loop)."""
        homie = self._homie
        if homie is None:
            return
        was_ready = homie.is_ready()
        homie.handle_message(topic, payload)

        # Check if device just became ready
        if not was_ready and homie.is_ready() and self._ready_event is not None:
            self._ready_event.set()

        # Dispatch snapshot callbacks if streaming
        if self._streaming and homie.is_ready() and self._loop is not None:
            if self._snapshot_interval <= 0:
                # No debounce — dispatch immediately (backward compat)
                self._create_dispatch_task()
            elif self._snapshot_timer is None:
                # Schedule debounced dispatch
                self._snapshot_timer = self._loop.call_later(self._snapshot_interval, self._fire_snapshot)

    def _on_connection_change(self, connected: bool) -> None:
        """Handle MQTT connection state change (called from asyncio loop).

        Re-subscribes to the wildcard topic on reconnect (pre-existing
        behavior), then fans out an edge-only notification to registered
        connection callbacks. Duplicate state transitions are suppressed
        so subscribers only see real edges.
        """
        # Re-subscribe runs on every connected=True, including duplicates —
        # paho may re-emit connected events after session restoration, and
        # re-subscribing is broker-benign. Callback fan-out below is
        # edge-only (see the guard after this block).
        if connected:
            _LOGGER.debug("MQTT connection established")
            if self._bridge is not None:
                wildcard = WILDCARD_TOPIC_FMT.format(serial=self._serial_number)
                self._bridge.subscribe(wildcard, qos=0)
        else:
            _LOGGER.debug("MQTT connection lost")

        # Edge-only dispatch
        if connected == self._live:
            return
        self._live = connected

        # Iterate a copy — subscribers may unregister during their callback
        for cb in list(self._connection_callbacks):
            try:
                cb(connected)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Connection callback raised")

    async def _wait_for_circuit_names(self, timeout: float) -> None:
        """Wait for all circuit-like nodes to have a ``name`` property.

        Retained MQTT messages may arrive after the Homie device transitions
        to ready. This polls the HomieDeviceConsumer at short intervals and
        returns as soon as all circuit names are populated, or when the
        timeout elapses (non-fatal — entities will use fallback names).
        """
        homie = self._require_homie()
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            missing = homie.circuit_nodes_missing_names()
            if not missing:
                _LOGGER.debug("All circuit names received")
                return
            await asyncio.sleep(_CIRCUIT_NAMES_POLL_INTERVAL_S)

        still_missing = homie.circuit_nodes_missing_names()
        if still_missing:
            _LOGGER.warning(
                "Timed out waiting for circuit names (%d still missing): %s",
                len(still_missing),
                still_missing[:5],
            )

    def _create_dispatch_task(self) -> None:
        """Create a background task to build and dispatch a snapshot."""
        if self._loop is None:
            return
        task = self._loop.create_task(
            self._dispatch_snapshot(),
            name="span_mqtt_dispatch_snapshot",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _fire_snapshot(self) -> None:
        """Timer callback — clear timer and dispatch one snapshot."""
        self._snapshot_timer = None
        self._create_dispatch_task()

    def _cancel_snapshot_timer(self) -> None:
        """Cancel any pending debounce timer."""
        if self._snapshot_timer is not None:
            self._snapshot_timer.cancel()
            self._snapshot_timer = None

    def set_snapshot_interval(self, interval: float) -> None:
        """Update the snapshot debounce interval at runtime.

        Args:
            interval: Seconds between snapshot dispatches. 0 = no debounce.
        """
        self._snapshot_interval = interval
        # Cancel any pending timer so the new interval takes effect on next message
        self._cancel_snapshot_timer()

    async def _dispatch_snapshot(self) -> None:
        """Build snapshot and send to all registered callbacks."""
        snapshot = self._require_homie().build_snapshot()
        for cb in list(self._snapshot_callbacks):
            try:
                await cb(snapshot)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Snapshot callback error", exc_info=True)
