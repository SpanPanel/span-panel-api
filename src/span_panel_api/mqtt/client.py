"""SPAN Panel MQTT client.

Composes AsyncMqttBridge and a SchemaAdapter to implement
SpanPanelClientProtocol, CircuitControlProtocol,
PanelControlProtocol, and StreamingCapableProtocol.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from importlib.metadata import version
import logging
import time

from span_panel_api._impl.schema_0 import SchemaZeroAdapter
from span_panel_api.schema_drift import log_schema_drift

from ..adapters import discover_adapters
from ..auth import get_homie_schema
from ..exceptions import SpanPanelConnectionError, SpanPanelServerError, SpanPanelStaleDataError
from ..models import FieldMetadata, HomieSchemaTypes, SpanPanelSnapshot
from ..protocol import PanelCapability, SchemaAdapter
from .connection import AsyncMqttBridge
from .const import MQTT_READY_TIMEOUT_S
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
        adapter_factory: Callable[[str, int], SchemaAdapter] = SchemaZeroAdapter,
    ) -> None:
        self._host = host
        self._serial_number = serial_number
        self._broker_config = broker_config
        self._snapshot_interval = snapshot_interval
        self._panel_http_port = panel_http_port
        self._adapter_factory = adapter_factory

        self._bridge: AsyncMqttBridge | None = None
        self._adapter: SchemaAdapter | None = None
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
        # Cached at connect() so the pre-rebuild hook can reconstruct the
        # Homie accumulator with the same panel size after a transport-level
        # rebuild. Schema cannot change within a session, so caching is safe.
        self._panel_size: int | None = None
        # Diagnostics — the factory overwrites these after adapter selection.
        # Defaults describe a client built directly (bypassing create_span_client).
        self._data_model_version: str | None = None
        self._schema_dispatch_reason: str = "not dispatched"

    def _build_adapter(self, panel_size: int) -> SchemaAdapter:
        """Construct the parser for this session.

        Called from connect() and from the reconnect path — the only two
        places a parser is built today.
        """
        self._adapter = self._adapter_factory(self._serial_number, panel_size)
        return self._adapter

    @property
    def adapter(self) -> SchemaAdapter | None:
        """Return the active schema adapter, or None before connect().

        On transport rebuild (see ``_on_pre_rebuild``), the adapter instance
        is replaced with a fresh one — any callback registered via
        ``adapter.register_property_callback(...)`` on the old instance does
        not survive the rebuild and must be re-registered on the new one.
        """
        return self._adapter

    @property
    def schema_major(self) -> str | None:
        """Return the active adapter's schema major, or None before connect()."""
        return self._adapter.schema_major if self._adapter is not None else None

    @property
    def data_model_version(self) -> str | None:
        """Return the panel's observed data-model-version, or None if absent/not yet dispatched."""
        return self._data_model_version

    @property
    def schema_dispatch_reason(self) -> str:
        """Return the human-readable reason the active adapter was selected."""
        return self._schema_dispatch_reason

    @property
    def available_adapters(self) -> list[str]:
        """Return the sorted keys of every schema adapter discovered in this process."""
        return sorted(discover_adapters())

    def _require_adapter(self) -> SchemaAdapter:
        """Return the SchemaAdapter, raising if not yet connected."""
        if self._adapter is None:
            raise SpanPanelConnectionError("Client not connected — call connect() first")
        return self._adapter

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
        4. Subscribe to the adapter's topics
        5. Wait for $state==ready and $description parsed

        Raises:
            SpanPanelConnectionError: Cannot connect or device not ready
            SpanPanelTimeoutError: Connection or ready timed out
        """
        self._loop = asyncio.get_running_loop()
        self._ready_event = asyncio.Event()

        # Fetch schema to determine panel size and build field metadata
        schema = await get_homie_schema(self._host, port=self._panel_http_port)
        self._panel_size = schema.panel_size
        adapter = self._build_adapter(schema.panel_size)

        _LOGGER.info(
            "MQTT adapter selected: %s (span-panel-api %s)\n  data-model-version: %r\n  reason: %s\n  available: %s",
            adapter.schema_major,
            version("span-panel-api"),
            self._data_model_version,
            self._schema_dispatch_reason,
            sorted(discover_adapters()),
        )

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
        self._field_metadata = self._require_adapter().build_field_metadata(schema.types)

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
        # Pre-rebuild hook: reset Homie accumulator before the bridge swaps
        # paho clients, so retained messages on the new subscription start
        # from a clean slate (no stale `$state=disconnected` cached from
        # the original outage).
        self._bridge.set_pre_rebuild_callback(self._on_pre_rebuild)

        # Connect to broker
        _LOGGER.debug("MQTT: Connecting to broker...")
        await self._bridge.connect()
        _LOGGER.debug("MQTT: Broker connected, subscribing...")

        # Subscribe to all device topics
        topics = self._require_adapter().topics_to_subscribe()
        for topic in topics:
            self._bridge.subscribe(topic, qos=0)
        _LOGGER.debug("MQTT: Subscribed to %s, waiting for Homie ready...", topics)

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
        self._live = False

    async def ping(self) -> bool:
        """Check if MQTT connection is alive and device is ready."""
        if self._bridge is None or self._adapter is None:
            return False
        return self._bridge.is_connected() and self._adapter.is_ready()

    def register_connection_callback(self, callback: Callable[[bool], None]) -> Callable[[], None]:
        """Subscribe to broker connection state transitions.

        Callback fires with False on broker disconnect and True on reconnect.
        No synthetic call is made at registration time — callbacks only fire
        on real state edges. To check current connection state on registration,
        await ping().

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

        Raises SpanPanelStaleDataError if the client is not fully live.
        "Live" means: the bridge is connected AND the Homie accumulator
        has reached ready state. Callers can treat SpanPanelStaleDataError
        as the canonical "panel currently unreachable" signal.

        No network call — snapshot is built from in-memory property values
        when the liveness checks pass.
        """
        if self._bridge is None or self._adapter is None:
            raise SpanPanelStaleDataError("Client not connected — call connect() first")
        if not self._bridge.is_connected():
            raise SpanPanelStaleDataError("MQTT broker disconnected")
        if not self._adapter.is_ready():
            raise SpanPanelStaleDataError("Homie device not ready")
        return self._adapter.build_snapshot()

    # -- CircuitControlProtocol --------------------------------------------

    async def set_circuit_relay(self, circuit_id: str, state: str) -> None:
        """Publish relay state change for a circuit.

        Args:
            circuit_id: Dashless UUID (matches wire format)
            state: "OPEN" or "CLOSED"
        """
        topic = self._require_adapter().set_circuit_relay_topic(circuit_id)
        if self._bridge is not None:
            self._bridge.publish(topic, state, qos=1)

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> None:
        """Publish a circuit priority change.

        Args:
            circuit_id: Dashless UUID (matches wire format)
            priority: v2 enum value (NEVER, SOC_THRESHOLD, OFF_GRID)
        """
        topic = self._require_adapter().set_circuit_priority_topic(circuit_id)
        if self._bridge is not None:
            self._bridge.publish(topic, priority, qos=1)

    # -- PanelControlProtocol ----------------------------------------------

    async def set_dominant_power_source(self, value: str) -> None:
        """Publish a dominant power source change for the panel.

        Args:
            value: DPS enum value (GRID, BATTERY, NONE, GENERATOR, PV)
        """
        topic = self._require_adapter().set_dominant_power_source_topic()
        if topic is None:
            raise SpanPanelServerError("Core node not found in panel topology")
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
        adapter = self._adapter
        if adapter is None:
            return
        was_ready = adapter.is_ready()
        adapter.handle_message(topic, payload)

        # Check if device just became ready
        if not was_ready and adapter.is_ready() and self._ready_event is not None:
            self._ready_event.set()

        # Dispatch snapshot callbacks if streaming
        if self._streaming and adapter.is_ready() and self._loop is not None:
            if self._snapshot_interval <= 0:
                # Real-time mode — dispatch immediately, no debounce.
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

        On disconnect, any pending snapshot-debounce timer is cancelled
        so a stale timer cannot dispatch a post-disconnect snapshot.
        """
        # Re-subscribe runs on every connected=True, including duplicates —
        # paho may re-emit connected events after session restoration, and
        # re-subscribing is broker-benign. Callback fan-out below is
        # edge-only (see the guard after this block).
        if connected:
            _LOGGER.debug("MQTT connection established")
            if self._bridge is not None and self._adapter is not None:
                for topic in self._adapter.topics_to_subscribe():
                    self._bridge.subscribe(topic, qos=0)
        else:
            _LOGGER.debug("MQTT connection lost")
            # Cancel any pending snapshot-debounce timer so it cannot
            # fire post-disconnect with a stale snapshot.
            self._cancel_snapshot_timer()

        # Edge-only dispatch
        if connected == self._live:
            return
        self._live = connected

        # Iterate a copy — subscribers may unregister during their callback
        for cb in list(self._connection_callbacks):
            try:
                cb(connected)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Connection callback raised", exc_info=True)

    def _on_pre_rebuild(self) -> None:
        """Reset Homie accumulator state before the bridge rebuilds its paho client.

        Called synchronously from the bridge's `_rebuild_client` before the
        old paho client is torn down and the new one is wired up. Discards
        any stale `$state=disconnected` cached during the outage so the
        new subscription's retained messages repopulate from a clean slate.

        Schema-derived state (`_field_metadata`, `_schema_hash`,
        `_previous_schema_types`) is intentionally preserved — the Homie
        schema cannot change within a session, so the cache remains valid
        and a refetch would just add cost. If the panel reboots and the
        schema actually changed, the existing drift-detection log fires on
        the next session's `connect()`.
        """
        if self._panel_size is None:
            # Pre-rebuild fired before connect() cached the panel size.
            # Treat as a no-op — there is no accumulator state to reset
            # because connect() never completed.
            return
        _LOGGER.debug("Pre-rebuild — resetting Homie accumulator")
        self._build_adapter(self._panel_size)

    async def _wait_for_circuit_names(self, timeout: float) -> None:
        """Wait for all circuit-like nodes to have a ``name`` property.

        Retained MQTT messages may arrive after the Homie device transitions
        to ready. This polls the schema adapter at short intervals and
        returns as soon as all circuit names are populated, or when the
        timeout elapses (non-fatal — entities will use fallback names).
        """
        adapter = self._require_adapter()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            missing = adapter.circuit_nodes_missing_names()
            if not missing:
                _LOGGER.debug("All circuit names received")
                return
            await asyncio.sleep(_CIRCUIT_NAMES_POLL_INTERVAL_S)

        still_missing = adapter.circuit_nodes_missing_names()
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
            interval: Seconds between snapshot dispatches. ``0`` (or any
                non-positive value) disables debounce and dispatches a
                snapshot for every incoming property message — real-time
                mode, intended for fast consumers.
        """
        self._snapshot_interval = interval
        # Cancel any pending timer so the new interval takes effect on next message
        self._cancel_snapshot_timer()

    async def _dispatch_snapshot(self) -> None:
        """Build snapshot and send to all registered callbacks.

        Guarded by the same liveness predicate as get_snapshot() — if the
        bridge has disconnected or the Homie device is not ready, no
        dispatch occurs. This prevents a pending debounce timer that was
        scheduled just before a disconnect from delivering a stale
        snapshot to subscribers after the fact.
        """
        bridge = self._bridge
        adapter = self._adapter
        if bridge is None or not bridge.is_connected() or adapter is None or not adapter.is_ready():
            _LOGGER.debug(
                "Skipping stale snapshot dispatch (bridge_connected=%s, homie_ready=%s)",
                bridge is not None and bridge.is_connected(),
                adapter is not None and adapter.is_ready(),
            )
            return
        snapshot = adapter.build_snapshot()
        for cb in list(self._snapshot_callbacks):
            try:
                await cb(snapshot)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Snapshot callback error", exc_info=True)
