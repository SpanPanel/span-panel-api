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
from typing import TYPE_CHECKING

from span_panel_api.schema_drift import log_schema_drift

from ..adapters import installed_adapter_keys, resolve_adapter
from ..auth import get_homie_schema
from ..dispatch import select_adapter_key
from ..exceptions import (
    SpanPanelAdapterIncompatibleError,
    SpanPanelAdapterMissingError,
    SpanPanelConnectionError,
    SpanPanelSchemaVersionError,
    SpanPanelServerError,
    SpanPanelStaleDataError,
    SpanPanelTimeoutError,
)
from ..models import AdoptedProperty, FieldMetadata, HomieSchemaTypes, SpanPanelSnapshot, V2HomieSchema
from ..protocol import PanelCapability, SchemaAdapter
from .connection import AsyncMqttBridge
from .const import MQTT_READY_TIMEOUT_S
from .models import MqttClientConfig

if TYPE_CHECKING:
    import httpx

_LOGGER = logging.getLogger(__name__)

# How long to wait for circuit name properties after device ready.
# Retained messages typically arrive within 1-2s, but allow headroom.
_CIRCUIT_NAMES_TIMEOUT_S = 10.0
_CIRCUIT_NAMES_POLL_INTERVAL_S = 0.25

# Re-reading the schema after a suspected generation change. Bounded because the
# caller is a fire-and-forget task on a live connection, and generous enough to
# outlast a panel that is still binding its HTTP port after a restart.
_REDISPATCH_RETRY_INITIAL_S = 1.0
_REDISPATCH_RETRY_MAX_S = 30.0
_REDISPATCH_LOG_EVERY = 20
"""How long to wait for the panel's HTTP endpoint after it returns on MQTT.

Sized from a live firmware upgrade rather than guessed. The panel dropped MQTT at
11:22:07 and the broker was back at 11:26:15 -- four minutes -- and its HTTP
front end was still answering 502 at that moment. Five attempts capped at 8s
gives up after about 23 seconds, which is not the same order of magnitude as a
device that is still booting: catching the 502 buys nothing if the loop stops
before the panel is ready.

Twelve attempts backing off to 30s is a little over four minutes. Each one is a
single GET, and the panel is the only thing that can end the wait.
"""


def _metadata_for_the_log() -> tuple[list[str], str]:
    """Every distribution-metadata read connect() needs, in one place.

    Grouped so there is a single thing to run in a thread rather than two calls
    that look unrelated and drift apart — which is exactly what happened once
    already, when the adapter keys were moved off the event loop and the version
    lookup beside them was not.
    """
    return installed_adapter_keys(), version("span-panel-api")


class SpanMqttClient:
    """MQTT transport — implements all span-panel-api protocols."""

    def __init__(
        self,
        host: str,
        serial_number: str,
        broker_config: MqttClientConfig,
        snapshot_interval: float = 1.0,
        panel_http_port: int = 80,
        adapter_factory: Callable[[str, V2HomieSchema], SchemaAdapter] | None = None,
        data_model_version: str | None = None,
        schema_dispatch_reason: str | None = None,
        schema: V2HomieSchema | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._host = host
        self._serial_number = serial_number
        self._broker_config = broker_config
        self._snapshot_interval = snapshot_interval
        self._panel_http_port = panel_http_port
        self._adapter_factory = adapter_factory
        # Shared by the caller, owned by the caller: never closed here, and its
        # policy -- timeouts, limits, headers -- is whatever the caller set. That
        # is the same rule the four config-flow entry points already state, and
        # the reason this exists at all is that the runtime path was the one place
        # left without it. See `_get_client`.
        self._httpx_client = httpx_client

        self._bridge: AsyncMqttBridge | None = None
        self._adapter: SchemaAdapter | None = None
        self._streaming = False
        self._snapshot_callbacks: list[Callable[[SpanPanelSnapshot], Awaitable[None]]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._schema_change_callbacks: list[Callable[[str | None, str | None], None]] = []
        self._live = False
        self._ready_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._snapshot_timer: asyncio.TimerHandle | None = None
        self._schema_hash: str | None = None
        self._previous_schema_types: HomieSchemaTypes | None = None
        # Supplied by create_span_client, which already fetched it to dispatch
        # on; None when constructed directly, in which case connect() fetches.
        # Either way it is cached for the pre-rebuild hook, which rebuilds the
        # parser after a transport-level rebuild.
        #
        # The cache used to be justified as "a panel cannot change schema within a
        # session". A firmware upgrade does exactly that: the panel disconnects and
        # returns as a different generation with the consumer's session still open.
        # `_redispatch_if_generation_changed` re-reads it on every reconnect edge and
        # replaces this, so the cache is now a per-connection value rather than a
        # per-session one.
        self._schema = schema
        # Diagnostics. create_span_client passes these so they are true from the
        # first moment the object exists; constructing directly leaves them
        # describing a client that has not dispatched yet, which connect() then
        # fills in once it has a schema to dispatch on.
        self._data_model_version = data_model_version
        self._schema_dispatch_reason = schema_dispatch_reason or "not dispatched"
        # The MQTT half of the same signal, filled in as the retained tree arrives and
        # checked against the REST half once the tree is complete. `None` means the
        # panel published no such property, which is itself the flat answer.
        self._observed_data_model_version: str | None = None
        # One reconsideration at a time. The MQTT trigger can fire repeatedly while a
        # fetch is retrying, and each would otherwise start its own retry loop.
        self._redispatch_in_flight = False

    async def _preload_adapter(self, schema: V2HomieSchema) -> None:
        """Resolve this schema's adapter in a thread, ahead of building it.

        Everything in ``adapters`` does blocking file I/O: entry-point
        enumeration reads distribution metadata, and resolving ``schema_1``
        imports the eBus SDK and jsonschema. Done on the event loop that is a
        two-second stall on a cold import cache, which Home Assistant reports as
        a blocking call and asks for a bug report about.

        Resolution caches per key for the life of the process, so this leaves
        ``_build_adapter``'s own resolve a dict lookup on every path that
        follows — including ``_on_pre_rebuild``, which runs from a synchronous
        bridge callback with no thread to defer to and depends on exactly that.

        Nothing to do when a factory was injected: that path never consults
        discovery, which is what lets an adapter-less install run one.

        Raises:
            SpanPanelSchemaVersionError: The version reads as no schema major.
            SpanPanelAdapterMissingError: Nothing registers the key it selects.
            SpanPanelAdapterIncompatibleError: Something does, and cannot be driven.
        """
        if self._adapter_factory is not None:
            return
        adapter_key, dispatch_reason = select_adapter_key(schema.data_model_version)
        await asyncio.to_thread(resolve_adapter, adapter_key, dispatch_reason)

    def _build_adapter(self, schema: V2HomieSchema) -> SchemaAdapter:
        """Construct the parser for this session.

        Called from connect() and from the reconnect path — the only two
        places a parser is built today. Both await ``_preload_adapter`` first,
        so the resolve below is a cache hit and this stays safe to call from a
        synchronous context.

        With no injected factory this dispatches on the schema rather than
        assuming the flat adapter. That matters because a client can be built
        directly, bypassing create_span_client: before, such a client handed a
        parent/child panel to the flat parser, which does not fail — it reports
        plausible and wrong figures. Dispatch now happens on whichever path a
        parser is built, so there is one answer rather than two.

        Resolving the adapter here rather than in ``__init__`` is deliberate:
        constructing a client must not require an adapter to be installed, only
        building a parser must. That keeps ``import span_panel_api.mqtt.client``
        working in an adapter-less install — the configuration entry-point
        discovery exists to support — and puts the failure at the point where it
        is actionable.

        Raises:
            SpanPanelSchemaVersionError: The panel reports a data-model-version
                whose schema major cannot be determined.
            SpanPanelAdapterMissingError: No adapter_factory was supplied and no
                installed package registers the key this panel needs.
        """
        factory = self._adapter_factory
        if factory is None:
            adapter_key, dispatch_reason = select_adapter_key(schema.data_model_version)
            self._data_model_version = schema.data_model_version
            self._schema_dispatch_reason = dispatch_reason
            factory = resolve_adapter(adapter_key, dispatch_reason)
        self._adapter = factory(self._serial_number, schema)
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
    def installed_adapters(self) -> list[str]:
        """Return the sorted keys every installed package registers an adapter for.

        Registered, not vetted — see ``installed_adapter_keys``. Reads
        distribution metadata off disk on first call, so an event loop should
        reach it through a thread.
        """
        return installed_adapter_keys()

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
        """Schema-derived metadata for snapshot fields, or None before ready.

        Keyed by snapshot field path (e.g. ``"panel.instant_grid_power_w"``).

        Computed from the adapter's current view at access time rather than
        cached during connect(). Under the parent/child schema the adapter reads
        each device's `$description`, which has not arrived when connect() runs
        its setup — a value captured there is permanently empty. Returning None
        until the adapter is ready keeps the documented none-before-connect
        sentinel and keeps "not ready" distinguishable from "ready with nothing".

        Cost: the schema_1 walk is devices x nodes x properties — under a
        thousand dict operations for a 40-circuit panel — against an access rate
        of once per connect-session.
        """
        adapter = self._adapter
        if adapter is None or not adapter.is_ready():
            return None
        return adapter.build_field_metadata()

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

        # create_span_client already fetched this to dispatch on; refetching
        # would be a second call to the same unauthenticated endpoint for a
        # value that cannot have changed. A directly-constructed client has no
        # schema yet, so it fetches here and dispatches in _build_adapter.
        schema = (
            self._schema
            if self._schema is not None
            else await get_homie_schema(self._host, port=self._panel_http_port, httpx_client=self._httpx_client)
        )
        self._schema = schema
        await self._preload_adapter(schema)
        adapter = self._build_adapter(schema)

        # Both halves of this line read distribution metadata off disk, and both
        # have to be gathered before it is logged. `version()` is the less obvious
        # one — it opens this package's own dist-info METADATA — and it was left
        # on the loop when its sibling was moved off, which Home Assistant went on
        # reporting as three blocking calls after the rest was fixed.
        #
        # Threaded on their own account rather than relying on the preload above,
        # which skips discovery entirely when a factory was injected.
        installed, library_version = await asyncio.to_thread(_metadata_for_the_log)
        _LOGGER.info(
            "MQTT adapter selected: %s (span-panel-api %s)\n  data-model-version: %r\n  reason: %s\n  installed: %s",
            adapter.schema_major,
            library_version,
            self._data_model_version,
            self._schema_dispatch_reason,
            installed,
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

        self._assert_transports_agree_on_schema_generation()
        _LOGGER.debug("MQTT: Connection fully established")

    def _assert_transports_agree_on_schema_generation(self) -> None:
        """Refuse a panel whose two schema-generation signals disagree.

        The migration guide's "Schema-generation detection" carries one rule on two
        transports: MQTT ``info/data-model-version`` absent = flat, present =
        parent/child; REST ``dataModelVersion`` absent = flat, exactly mirroring the
        MQTT signal. Dispatch reads REST, because the adapter decides which topics to
        subscribe to and so must exist before the first SUBSCRIBE. That makes the MQTT
        value a free second opinion, and until now nothing looked at it.

        Nothing looking at it is how a v1.0 panel gets parsed by the flat adapter in
        silence: a producer that publishes the MQTT property but omits the REST one
        dispatches to ``schema_0``, every value in the tree is read against the wrong
        vocabulary, and the connection reports success. Wrong numbers, no error.

        Raising rather than warning follows the rule dispatch already applies to an
        unparseable version: an unknown schema generation means every value in the
        tree may be misread, and the blast radius is the whole panel. A disagreement
        is that same situation with a second witness.

        Compared by the adapter each value *selects*, not by string equality --
        ``'1.0'`` and ``'1.0.3'`` are both parsed by ``schema_1``, and failing that
        pair would be a false alarm about a patch release.
        """
        observed = self._observed_data_model_version
        reported = self._data_model_version
        try:
            observed_key, _ = select_adapter_key(observed)
            reported_key, _ = select_adapter_key(reported)
        except SpanPanelSchemaVersionError:
            # One of them is present but unparseable. Dispatch already refused on the
            # REST value before we got here, so this is the MQTT one -- report it as
            # the disagreement it is rather than re-raising a message about REST.
            raise SpanPanelSchemaVersionError(
                f"Panel {self._serial_number} publishes MQTT info/data-model-version="
                f"{observed!r}, which no adapter major can be read from, while REST "
                f"reports dataModelVersion={reported!r}"
            ) from None

        if observed_key != reported_key:
            raise SpanPanelSchemaVersionError(
                f"Panel {self._serial_number} disagrees with itself about its schema "
                f"generation: REST dataModelVersion={reported!r} selects "
                f"{reported_key!r}, MQTT info/data-model-version={observed!r} selects "
                f"{observed_key!r}. The migration guide requires the two to mirror each "
                f"other; parsing the tree with either parser would misread values the "
                f"other owns."
            )

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

    def register_schema_change_callback(self, callback: Callable[[str | None, str | None], None]) -> Callable[[], None]:
        """Subscribe to the panel changing schema generation mid-session.

        Fires with ``(previous_version, new_version)`` after the parser has been
        rebuilt, so a consumer reading the client inside the callback sees the new
        generation rather than the one being replaced.

        This exists because swapping the parser is not the whole job. It fixes
        *reading* — values resolve again immediately — but a consumer that built
        devices and entities from the old tree still has the old topology: v1.0 adds
        a MID that the flat tree has no equivalent for, and re-keys EVSEs. Only the
        consumer knows how to rebuild that, so it is told rather than guessed at.

        Returns an unregister function. Calling it twice is safe.
        """
        self._schema_change_callbacks.append(callback)

        def unregister() -> None:
            with contextlib.suppress(ValueError):
                self._schema_change_callbacks.remove(callback)

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

        The adapter names both the topic and the payload, because the two
        schemas do not accept the same values. Flat takes this vocabulary
        directly; v1.0 routes the command to `shed/asserted-islanding-state`,
        whose enum is `NONE`/`ON_GRID`/`OFF_GRID`. Publishing `value` unchanged
        would put a string outside that enum on the wire.
        """
        adapter = self._require_adapter()
        topic = adapter.set_dominant_power_source_topic()
        if topic is None:
            raise SpanPanelServerError("Core node not found in panel topology")
        payload = adapter.dominant_power_source_payload(value)
        if payload is None:
            raise SpanPanelServerError(f"{value!r} has no representation on this schema's control")
        if self._bridge is not None:
            self._bridge.publish(topic, payload, qos=1)

    # -- EvseControlProtocol -----------------------------------------------

    async def set_evse_charge_limit(self, node_id: str, amps: int) -> None:
        """Publish a charge-current limit for one commissioned EV charger.

        Args:
            node_id: the key this charger has in `SpanPanelSnapshot.evse`
            amps: the new ceiling, in amps

        Shaped like `set_dominant_power_source` and for the same reason: the
        adapter names both the topic and the payload, because only it knows
        which property this panel's charger declares settable and what bounds
        it. Two refusals rather than one, so the error says which happened —
        "no such control" and "that value may not be written" are different
        facts and a user can act on only one of them.
        """
        adapter = self._require_adapter()
        topic = adapter.set_evse_charge_limit_topic(node_id)
        if topic is None:
            raise SpanPanelServerError(f"No settable charge-current limit on EVSE {node_id!r}")
        payload = adapter.evse_charge_limit_payload(node_id, amps)
        if payload is None:
            raise SpanPanelServerError(f"{amps} A is outside what EVSE {node_id!r} accepts")
        if self._bridge is not None:
            self._bridge.publish(topic, payload, qos=1)

    # -- AdoptedControlProtocol --------------------------------------------

    async def set_adopted_property(self, device_id: str, node_id: str, property_id: str, value: str) -> None:
        """Publish a write to one settable property of an adopted device.

        Args:
            device_id: the adopted device's wire id, as `AdoptedDevice.device_id`
            node_id: the Homie node
            property_id: the Homie property
            value: the payload, already in the property's declared vocabulary

        **The lookup is the authorisation.** No topic is accepted from the
        caller: this finds the property in the current snapshot's adopted
        devices and publishes to the topic that property carries. A device the
        adapter models has no `AdoptedDevice`, and a property the device does not
        declare settable carries no `set_topic`, so neither can be reached from
        here however the arguments are spelled. That is what keeps this from
        being a generic write that routes around the curated setters -- which
        would skip real work, since the islanding assertion needs its value
        translated and the charge-current ceiling refuses values above what the
        charger was commissioned for.

        No payload translation and no bounds check, deliberately. Both exist on
        curated controls because this library knows what those properties mean.
        It knows nothing about an adopted one beyond its declaration, and
        inventing a bound would be inventing a fact about somebody's hardware.
        The caller constrains the value to the declared `format`; the panel
        remains the authority on whether to accept it.
        """
        surface = self._adopted_property(device_id, node_id, property_id)
        if surface is None or surface.set_topic is None:
            raise SpanPanelServerError(f"No settable adopted property {node_id}/{property_id} on device {device_id!r}")
        if self._bridge is not None:
            self._bridge.publish(surface.set_topic, value, qos=1)

    def _adopted_property(self, device_id: str, node_id: str, property_id: str) -> AdoptedProperty | None:
        """The named property of the named adopted device in the current snapshot.

        Built fresh rather than cached: a device that has left the tree must stop
        being writable the moment it does, and a snapshot is the only thing that
        knows.
        """
        for device in self._require_adapter().build_snapshot().adopted_devices:
            if device.device_id != device_id:
                continue
            for surface in device.properties:
                if surface.node_id == node_id and surface.property_id == property_id:
                    return surface
        return None

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
        # The bootstrap signal, observed rather than parsed, and deliberately ahead of
        # the adapter guard: reading it is what tells the generations apart, so it
        # cannot be something only a chosen parser can do.
        #
        # Matched on suffix so no Homie domain constant has to exist in the transport.
        # Only the root device's copy counts -- under parent/child every device has an
        # `info` node, and a child's copy would otherwise overwrite the panel's answer
        # depending on retained-message ordering.
        if topic.endswith(f"/{self._serial_number}/info/data-model-version"):
            self._observed_data_model_version = payload or None
            # This is the trigger for a mid-session generation change, not the
            # reconnect edge. The edge fires the instant the broker accepts a
            # connection, which on a real upgrade is *before* the panel has bound
            # its HTTP port -- observed as `Cannot reach panel` roughly 25ms after
            # reconnect, with no further edge to retry on because MQTT had already
            # succeeded. The retained tree arrives only once the new panel is
            # actually publishing, which makes this the first moment the answer
            # exists at all.
            self._schedule_redispatch()

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
            # A reconnect can be a different panel generation than the one we
            # dispatched on. Checked only on a real edge, because paho re-emits
            # connected=True after session restoration and refetching the schema
            # on each of those would be a HTTP round trip per duplicate.
            if not self._live:
                self._schedule_redispatch()
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

    def _schedule_redispatch(self) -> None:
        """Reconsider the panel's schema generation, off the calling callback.

        Both callers are synchronous — the connection-change handler and the message
        handler — and the work is a HTTP round trip, so it is handed to the loop.

        Cheap to call often: the MQTT trigger fires on every retained
        `info/data-model-version`, and the common case is that it agrees with the
        active adapter. That is answered here without scheduling anything, so a
        steady-state panel costs one string comparison per republish.
        """
        if self._loop is None or self._adapter is None:
            # No loop means connect() never ran, so there is nothing dispatched to
            # reconsider and no loop to schedule the reconsideration on.
            return
        if self._redispatch_in_flight:
            return
        if not self._generation_appears_changed():
            return
        self._redispatch_in_flight = True
        task = self._loop.create_task(self._redispatch_if_generation_changed())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _generation_appears_changed(self) -> bool:
        """Whether any signal we hold suggests a parser other than the active one.

        Deliberately permissive: it gates scheduling, not the swap itself. The
        authoritative comparison happens in `_redispatch_if_generation_changed`
        against a freshly fetched REST schema, so a false positive here costs one
        HTTP request and a false negative costs a missed upgrade.
        """
        try:
            active, _ = select_adapter_key(self._data_model_version)
            observed, _ = select_adapter_key(self._observed_data_model_version)
        except SpanPanelSchemaVersionError:
            # Unreadable version. Let the full path report it properly.
            return True
        return active != observed

    async def _fetch_schema_with_retry(self) -> V2HomieSchema | None:
        """Read the panel's REST schema, waiting for HTTP to catch up with the broker.

        A panel that has just restarted accepts MQTT before it serves HTTP — the
        broker is listening while the application is still binding its port. The
        first attempt at a real upgrade failed 25ms after reconnect with
        `Cannot reach panel`, and because MQTT had reconnected successfully there
        was no further edge to retry on, leaving the wrong parser in place for the
        rest of the session.

        **This waits as long as it takes, and that is deliberate.** Every bounded
        version of it has been wrong, twice for the same reason: the bound was
        sized against a reboot somebody had measured, and the next reboot was not
        that reboot. Giving up has no upside to weigh against being wrong. The
        triggers for another attempt are the reconnect edge and the retained
        `data-model-version` message, and a panel that finishes booting after the
        loop gave up produces neither — so exhausting a bound does not mean
        "try again later", it means stranded until somebody reloads by hand.

        Nor does waiting cost the freshness of anything. Energy sensors already
        hold their last valid reading through an outage on their own grace period,
        which exists precisely so a gap does not become an `unknown` and a
        statistics spike; that mechanism is untouched by how long this waits, and
        it is the thing that would have justified a deadline here. What is left is
        one HTTP GET every thirty seconds to a device on the local network, which
        is less traffic than the ordinary snapshot poll.

        Ends on success, on cancellation — `close()` cancels this task, so unload
        and shutdown are prompt — or on an error that is not the panel still
        coming up, which is left to raise.
        """
        delay = _REDISPATCH_RETRY_INITIAL_S
        attempts = 0
        while True:
            try:
                return await get_homie_schema(self._host, port=self._panel_http_port, httpx_client=self._httpx_client)
            except (
                SpanPanelConnectionError,
                SpanPanelTimeoutError,
                # The panel answering rather than refusing: a 5xx from its front
                # end while the application behind it starts, or a 200 carrying a
                # body that cannot be a schema. The ordinary shape of a reboot,
                # because a device brings its network stack and proxy up before
                # its application -- and the shape that stranded two live installs
                # when it was not caught here.
                SpanPanelServerError,
            ) as exc:
                attempts += 1
                if attempts == 1 or attempts % _REDISPATCH_LOG_EVERY == 0:
                    # First failure, then occasionally. A panel that never returns
                    # would otherwise write a line every thirty seconds forever,
                    # and the second line is worth no more than the first.
                    _LOGGER.warning(
                        "Panel is not serving its schema yet (%s). Attempt %d; still "
                        "waiting, and the parser stays as it is until it answers.",
                        exc,
                        attempts,
                    )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _REDISPATCH_RETRY_MAX_S)

    async def _redispatch_if_generation_changed(self) -> None:
        """Swap the parser when the panel comes back as a different schema generation.

        The adapter is chosen once, at connect, from the REST `dataModelVersion`.
        Everything after that reuses it: `connect()` short-circuits on the cached
        `self._schema`, the reconnect path re-subscribes with the existing adapter's
        topics, and `_on_pre_rebuild` rebuilds from the cached schema on the stated
        assumption that "the Homie schema cannot change within a session".

        A firmware upgrade breaks that assumption exactly. The panel disconnects and
        returns as a different generation while the consumer's session is still open
        — no reload, no new `connect()`, so nothing ever reconsiders. Observed as a
        flat panel upgrading to v1.0 underneath a live client: the client reconnected,
        kept the flat parser, and read the v1.0 tree with it. It logged one
        `Invalid $description JSON` and then reported every circuit as missing, which
        is a wrong answer rather than an error.

        Failure here is deliberately non-fatal. The panel is reachable over MQTT or
        this callback would not be running, and its HTTP endpoint may lag that by
        seconds while it finishes booting; treating a refused fetch as fatal would
        turn a slow boot into a dead integration. The generation is re-read on the
        next reconnect, and a stale parser reports missing data rather than wrong
        data, because the two schemas do not share a topic shape.
        """
        try:
            await self._redispatch_once()
        except Exception:  # pylint: disable=broad-exception-caught
            # Nothing may escape here. This runs as a fire-and-forget task, so an
            # escaping exception becomes "Task exception was never retrieved" in
            # the log and the parser silently stays on the old generation --
            # which is the failure this whole method exists to prevent, arrived at
            # by a different route. That is not hypothetical: a 502 from a
            # rebooting panel did exactly this on two live installs.
            #
            # Logged at ERROR with the consequence spelled out, because the user's
            # remedy is a reload and nothing else will tell them so.
            _LOGGER.error(
                "Could not follow the panel's schema-generation change; the %r parser is "
                "unchanged and its data will read as missing. Reload the integration once "
                "the panel is fully back up.",
                self._data_model_version,
                exc_info=True,
            )
        finally:
            # Released only when the swap is finished, not when the fetch is.
            # Clearing it after the fetch left a window that the slowest step in
            # the method sits inside: `_preload_adapter` imports the new parser in
            # a thread and takes seconds on a cold schema_1 import, and through
            # all of it `_data_model_version` still holds the old value, so
            # `_generation_appears_changed()` was still true. A second retained
            # `data-model-version` message -- or the connect edge -- scheduled a
            # second redispatch, and the consumer got two schema-change callbacks
            # for one upgrade. The integration reloads its config entry off that
            # callback, so that is a reload racing its own teardown.
            self._redispatch_in_flight = False

    async def _redispatch_once(self) -> None:
        """The body of one redispatch. See `_redispatch_if_generation_changed`."""
        schema = await self._fetch_schema_with_retry()
        if schema is None:
            return

        before = self._data_model_version
        try:
            new_key, _ = select_adapter_key(schema.data_model_version)
            old_key, _ = select_adapter_key(before)
        except SpanPanelSchemaVersionError:
            _LOGGER.warning(
                "Panel reports data-model-version %r after reconnect, which no adapter "
                "major can be read from; keeping the %r parser",
                schema.data_model_version,
                before,
            )
            return

        if new_key == old_key:
            return

        # Before anything is mutated, because this is where the upgrade can turn
        # out to be one this install cannot follow: a flat panel that becomes
        # v1.0 needs a package a flat-only install has no reason to have. The
        # caller is a fire-and-forget task, so an escaping error would surface as
        # a bare traceback; naming the missing package and keeping the parser we
        # have is the same non-fatal stance the fetch retry takes above.
        try:
            await self._preload_adapter(schema)
        except (SpanPanelAdapterMissingError, SpanPanelAdapterIncompatibleError) as exc:
            _LOGGER.error(
                "Panel upgraded from schema generation %s to %s, but this install cannot "
                "parse the new one: %s. Keeping the %s parser, which will report missing "
                "data rather than wrong data until the adapter is installed.",
                old_key,
                new_key,
                exc,
                old_key,
            )
            return

        _LOGGER.warning(
            "Panel changed schema generation while connected: data-model-version %r -> "
            "%r (%s -> %s). Rebuilding the parser; entities will repopulate from the "
            "new tree.",
            before,
            schema.data_model_version,
            old_key,
            new_key,
        )
        self._schema = schema
        # Set here rather than relying on `_build_adapter`, which only records it on
        # the dispatching path. A client constructed with an injected `adapter_factory`
        # skips that branch, and would go on reporting the generation it started with
        # after having been rebuilt for a different one.
        self._data_model_version = schema.data_model_version
        adapter = self._build_adapter(schema)
        # Ready is a property of the tree, and this is a different tree. Leaving the
        # old event set would let `is_ready()` answer for a parser that has not seen
        # a single message yet.
        self._ready_event = asyncio.Event()
        if self._bridge is not None:
            for topic in adapter.topics_to_subscribe():
                self._bridge.subscribe(topic, qos=0)

        # Announced after the swap, so a consumer inspecting the client from inside
        # the callback sees the generation it is being told about. Iterate a copy —
        # a subscriber may unregister while handling this, and reloading a config
        # entry (the expected response) tears down the very object that registered.
        for cb in list(self._schema_change_callbacks):
            try:
                cb(before, schema.data_model_version)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Schema-change callback raised", exc_info=True)

    def _on_pre_rebuild(self) -> None:
        """Reset Homie accumulator state before the bridge rebuilds its paho client.

        Called synchronously from the bridge's `_rebuild_client` before the
        old paho client is torn down and the new one is wired up. Discards
        any stale `$state=disconnected` cached during the outage so the
        new subscription's retained messages repopulate from a clean slate.

        Schema-derived state (`_schema`, `_schema_hash`,
        `_previous_schema_types`) is intentionally preserved — the Homie
        schema cannot change within a session, so the cache remains valid
        and a refetch would just add cost. If the panel reboots and the
        schema actually changed, the existing drift-detection log fires on
        the next session's `connect()`. `field_metadata` needs no preserving:
        it reads the live adapter, so it re-derives itself from the rebuilt
        tree once that tree is ready again.

        A cached schema is also what makes the rebuild safe to run from a
        synchronous callback. ``_build_adapter`` can raise — on an unreadable
        version, or on a key nothing provides — but a cached schema means
        connect() already dispatched and resolved successfully on this exact
        value, so neither can fail here. The guard below is what enforces that:
        no schema means connect() never completed, and there is nothing to
        rebuild.
        """
        if self._schema is None:
            # Pre-rebuild fired before connect() cached the schema. Treat as a
            # no-op — there is no accumulator state to reset because connect()
            # never completed.
            return
        _LOGGER.debug("Pre-rebuild — resetting Homie accumulator")
        self._build_adapter(self._schema)

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
