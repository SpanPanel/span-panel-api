"""Parent/child adapter: `ebus_sdk.Controller` behind the `SchemaAdapter` protocol.

The SDK does the Homie work — walking `$description.children`, gating each
child's subscription on its parent reaching `ready`, and cascading state down
the tree. This adapter supplies the transport it parses over, sorts the result
into a `SpanPanelSnapshot`, and builds the topics the transport publishes
commands to.

**It never touches the connection.** `SchemaAdapter` instances are built before
one exists, so `Controller` is given a route table (`ControllerRoutes`) that
records its subscriptions instead of making them, and this adapter asks for one
broad subscription up front through `topics_to_subscribe()`. Every message then
arrives via `handle_message` and is routed to whichever SDK callback wanted it.
A reconnect re-subscribes that same static list, the broker replays the retained
tree, and the SDK repopulates — so there is no resync hook to wire or forget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ebus_sdk import Controller

from span_panel_api_schema_1.const import (
    HOMIE_DOMAIN,
    HOMIE_VERSION,
    NODE_INFO,
    NODE_LOAD_SHED,
    NODE_SWITCH,
    PROP_NAME,
    PROP_PRIORITY,
    PROP_RELAY,
    STATE_READY,
)
from span_panel_api_schema_1.field_metadata import build_field_metadata
from span_panel_api_schema_1.snapshot import TreeRoles, build_snapshot, device_type
from span_panel_api_schema_1.transport import ControllerRoutes

if TYPE_CHECKING:
    from collections.abc import Callable

    from ebus_sdk.homie import DiscoveredDevice

    from span_panel_api.models import FieldMetadata, SpanPanelSnapshot, V2HomieSchema


class SchemaOneAdapter:
    """Parser for the parent/child schema (data-model-version 1.x)."""

    schema_major = "schema_1"
    SUPPORTS_DATA_MODEL_VERSIONS: tuple[str, str] = (">=1.0", "<2.0")

    def __init__(self, serial_number: str, schema: V2HomieSchema) -> None:
        self._serial_number = serial_number
        self._schema = schema
        self._routes = ControllerRoutes()
        self._controller = Controller(root_device_id=serial_number, mqttc=self._routes)
        self._property_callbacks: list[Callable[[str, str, str, str | None], None]] = []
        self._controller.set_on_property_changed_callback(self._on_property_changed)
        # Records the subscriptions the tree walk needs; nothing reaches the
        # wire, because this object has no connection to reach it with.
        self._controller.start_discovery()

    # -- SchemaAdapter -----------------------------------------------------

    def topics_to_subscribe(self) -> list[str]:
        """One subscription covering the whole tree.

        Deliberately broader than the SDK's own per-device subscriptions,
        because the adapter is asked this once at connect and again after a
        reconnect — it has no way to add one when a child announces later. The
        flat adapter takes the same approach with `ebus/5/{serial}/#`; here the
        wildcard spans devices, since children are peers of the panel in the
        topic tree rather than nodes beneath it.
        """
        return [f"{HOMIE_DOMAIN}/{HOMIE_VERSION}/#"]

    def handle_message(self, topic: str, payload: str) -> None:
        self._routes.dispatch(topic, payload)

    def is_ready(self) -> bool:
        """Ready when the panel has announced itself and described its tree.

        Children are deliberately not required: a panel with a slow-announcing
        child would otherwise never become ready, and the snapshot already
        handles a partial tree.
        """
        root = self._controller.get_root(self._serial_number)
        return root is not None and root.state == STATE_READY and bool(root.description)

    def build_snapshot(self) -> SpanPanelSnapshot:
        root = self._require_root()
        return build_snapshot(root, self._children())

    def build_field_metadata(self) -> dict[str, FieldMetadata]:
        root = self._controller.get_root(self._serial_number)
        devices = [] if root is None else [root, *self._children()]
        return build_field_metadata(devices)

    def circuit_nodes_missing_names(self) -> list[str]:
        """Circuits whose retained name has not arrived yet.

        The transport polls this during connect so the first snapshot carries
        human-readable names rather than falling back to identifiers.
        """
        return [
            circuit.device_id
            for circuit in TreeRoles(self._children()).circuits
            if not circuit.get_property(NODE_INFO, PROP_NAME)
        ]

    def find_node_by_type(self, type_str: str) -> str | None:
        """Return the id of the first device declaring `type_str`.

        Named for the flat schema's nodes; under parent/child the same question
        is asked of devices, and the answer is a device id.
        """
        for device in self._children():
            if device_type(device) == type_str:
                return device.device_id
        return None

    # -- Command topics ----------------------------------------------------
    #
    # The adapter names the topic and the transport publishes it, so commanding
    # a panel needs no connection here either.

    def set_circuit_relay_topic(self, circuit_id: str) -> str:
        return self._set_topic(circuit_id, NODE_SWITCH, PROP_RELAY)

    def set_circuit_priority_topic(self, circuit_id: str) -> str:
        return self._set_topic(circuit_id, NODE_LOAD_SHED, PROP_PRIORITY)

    def set_dominant_power_source_topic(self) -> str | None:
        """No v1.0 equivalent, so no topic.

        `dominant-power-source` split into `grid-forming-entity` and
        `asserted-islanding-state`, which are different controls on different
        devices rather than a renamed one. Returning None makes the transport
        reject the command instead of publishing to a topic nothing serves —
        and which successor to expose is a product decision, tracked in the
        entity and config deltas write-up.
        """
        return None

    def register_property_callback(self, callback: Callable[[str, str, str, str | None], None]) -> Callable[[], None]:
        """Subscribe to per-property updates; returns an unregister callable."""
        self._property_callbacks.append(callback)

        def _unregister() -> None:
            if callback in self._property_callbacks:
                self._property_callbacks.remove(callback)

        return _unregister

    # -- internals ---------------------------------------------------------

    def _set_topic(self, device_id: str, node: str, prop: str) -> str:
        return f"{HOMIE_DOMAIN}/{HOMIE_VERSION}/{device_id}/{node}/{prop}/set"

    def _require_root(self) -> DiscoveredDevice:
        """The root, or a clear error if discovery has not finished.

        Checks readiness rather than existence: `start_discovery` pre-creates
        the root entry so descendants have somewhere to attach, so the device
        object exists from construction and proves nothing on its own.
        """
        root = self._controller.get_root(self._serial_number)
        if root is None or not self.is_ready():
            raise RuntimeError(f"Device tree for {self._serial_number!r} is not ready; build_snapshot called too early")
        return root

    def _children(self) -> list[DiscoveredDevice]:
        return list(self._controller.get_descendants(self._serial_number))

    def _on_property_changed(self, device_id: str, node_id: str, property_id: str, value: str, _old: str | None) -> None:
        """Fan a Controller property change out to registered consumers.

        Signature adapts the SDK's five arguments to the protocol's four: the
        protocol has no place for the previous value, and consumers that need
        one keep it themselves.
        """
        for callback in list(self._property_callbacks):
            callback(device_id, node_id, property_id, value)
