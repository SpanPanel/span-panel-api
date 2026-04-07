"""Homie v5 property accumulator with lifecycle state machine.

Generic Homie protocol layer that knows nothing about SPAN-specific
concepts. Stores property values, tracks lifecycle, and exposes a
query API for higher-level consumers.
"""

from __future__ import annotations

from collections.abc import Callable
import enum
import json
import logging
import time

from .const import HOMIE_STATE_DISCONNECTED, HOMIE_STATE_LOST, HOMIE_STATE_READY, TOPIC_PREFIX

_LOGGER = logging.getLogger(__name__)

# Callback signature: (node_id, prop_id, value, old_value)
PropertyCallback = Callable[[str, str, str, str | None], None]


class HomieLifecycle(enum.Enum):
    """Homie device lifecycle states."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    DESCRIPTION_RECEIVED = "description_received"
    READY = "ready"


class HomiePropertyAccumulator:
    """Accumulate Homie v5 property values and track device lifecycle.

    Topic prefix: ``ebus/5/{serial_number}``

    All methods must be called from the asyncio event loop thread.
    """

    def __init__(self, serial_number: str) -> None:
        self._serial_number = serial_number
        self._topic_prefix = f"{TOPIC_PREFIX}/{serial_number}"

        # Lifecycle
        self._lifecycle = HomieLifecycle.DISCONNECTED
        self._received_state_ready = False
        self._received_description = False
        self._ready_since: float = 0.0

        # Property storage
        self._property_values: dict[str, dict[str, str]] = {}
        self._property_timestamps: dict[str, dict[str, int]] = {}
        self._target_values: dict[str, dict[str, str]] = {}

        # Node type mapping from $description
        self._node_types: dict[str, str] = {}

        # Generation counter — incremented when $description clears property
        # values so consumers can invalidate caches built from stale data.
        self._generation: int = 0

        # Dirty tracking
        self._dirty_nodes: set[str] = set()

        # Callbacks
        self._property_callbacks: list[PropertyCallback] = []

    # -- Public properties ---------------------------------------------------

    @property
    def serial_number(self) -> str:
        """Serial number this accumulator tracks."""
        return self._serial_number

    @property
    def lifecycle(self) -> HomieLifecycle:
        """Current lifecycle state."""
        return self._lifecycle

    @property
    def ready_since(self) -> float:
        """Monotonic timestamp of the last READY transition, 0.0 if never ready."""
        return self._ready_since

    @property
    def generation(self) -> int:
        """Counter incremented on the initial $description and after lifecycle resets."""
        return self._generation

    def is_ready(self) -> bool:
        """True when lifecycle is READY."""
        return self._lifecycle == HomieLifecycle.READY

    # -- Message routing -----------------------------------------------------

    def handle_message(self, topic: str, payload: str) -> None:
        """Route an MQTT message to the appropriate handler."""
        prefix_with_sep = f"{self._topic_prefix}/"
        if not topic.startswith(prefix_with_sep):
            return

        suffix = topic[len(prefix_with_sep) :]

        if suffix == "$state":
            self._handle_state(payload)
        elif suffix == "$description":
            self._handle_description(payload)
        elif suffix.endswith("/set"):
            return  # ignore /set topics
        elif "/" in suffix:
            parts = suffix.split("/", 1)
            node_id = parts[0]
            prop_part = parts[1]
            if prop_part.endswith("/$target"):
                # Target value: {node_id}/{prop_id}/$target
                prop_id = prop_part[: -len("/$target")]
                self._handle_target(node_id, prop_id, payload)
            else:
                # Reported value: {node_id}/{prop_id}
                self._handle_property(node_id, prop_part, payload)

    # -- Query API -----------------------------------------------------------

    def get_prop(self, node_id: str, prop_id: str, default: str = "") -> str:
        """Get a property's reported value."""
        return self._property_values.get(node_id, {}).get(prop_id, default)

    def get_timestamp(self, node_id: str, prop_id: str) -> int:
        """Get the epoch timestamp of a property's last update."""
        return self._property_timestamps.get(node_id, {}).get(prop_id, 0)

    def get_target(self, node_id: str, prop_id: str) -> str | None:
        """Get a property's target value, or None if no target set."""
        return self._target_values.get(node_id, {}).get(prop_id)

    def has_target(self, node_id: str, prop_id: str) -> bool:
        """True if a target value exists for the given property."""
        return prop_id in self._target_values.get(node_id, {})

    def find_node_by_type(self, type_str: str) -> str | None:
        """Find the first node ID matching a given type string."""
        for node_id, node_type in self._node_types.items():
            if node_type == type_str:
                return node_id
        return None

    def nodes_by_type(self, type_str: str) -> list[str]:
        """Return all node IDs matching a given type string."""
        return [nid for nid, ntype in self._node_types.items() if ntype == type_str]

    def get_node_type(self, node_id: str) -> str:
        """Get the type string for a node, or empty string if unknown."""
        return self._node_types.get(node_id, "")

    def all_node_types(self) -> dict[str, str]:
        """Return a copy of the node_id → type mapping."""
        return dict(self._node_types)

    def dirty_node_ids(self) -> frozenset[str]:
        """Return the set of node IDs with changed properties since last mark_clean."""
        return frozenset(self._dirty_nodes)

    def mark_clean(self) -> None:
        """Clear the dirty set."""
        self._dirty_nodes.clear()

    def register_property_callback(self, callback: PropertyCallback) -> Callable[[], None]:
        """Register a callback fired on property value changes.

        Callback signature: (node_id, prop_id, new_value, old_value).
        Only fires when the value actually changes, not on every message.

        Returns an unregister function.
        """
        self._property_callbacks.append(callback)

        def unregister() -> None:
            try:
                self._property_callbacks.remove(callback)
            except ValueError:
                _LOGGER.debug("Callback already unregistered")

        return unregister

    # -- Internal handlers ---------------------------------------------------

    def _handle_state(self, payload: str) -> None:
        """Handle $state topic and drive lifecycle transitions."""
        if payload == HOMIE_STATE_READY:
            self._received_state_ready = True
            if self._received_description:
                self._transition_to_ready()
            else:
                # State ready but no description yet
                if self._lifecycle == HomieLifecycle.DISCONNECTED:
                    self._lifecycle = HomieLifecycle.CONNECTED
        elif payload in (HOMIE_STATE_DISCONNECTED, HOMIE_STATE_LOST):
            self._lifecycle = HomieLifecycle.DISCONNECTED
            self._received_state_ready = False
            self._received_description = False
        else:
            # init, sleeping, alert, etc. — connected but not ready.
            # Always move out of READY/DESCRIPTION_RECEIVED into a
            # non-ready connected lifecycle state.
            #
            # Reset _received_description so that the upcoming $description
            # triggers a property clear.  This covers fast reboots where
            # the broker's LWT ($state=disconnected) may not reach us
            # before the panel publishes $state=init.
            self._lifecycle = HomieLifecycle.CONNECTED
            self._received_state_ready = False
            self._received_description = False

        _LOGGER.debug("Homie $state: %s → lifecycle=%s", payload, self._lifecycle.value)

    def _handle_description(self, payload: str) -> None:
        """Parse $description JSON and extract node type mappings."""
        try:
            desc = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Invalid $description JSON")
            return

        # _handle_state() already reset _received_description to False due to
        # a state change that starts a new panel lifecycle, including
        # $state=disconnected/lost and other non-ready states such as init.
        # This means the panel rebooted while we were connected.  On a pure
        # MQTT reconnect (no panel reboot), _received_description is still
        # True from the previous session so we skip the clear — the retained
        # property messages will carry the correct (unchanged) values.
        if not self._received_description:
            self._property_values.clear()
            self._property_timestamps.clear()
            self._target_values.clear()
            self._generation += 1
            _LOGGER.debug("Cleared stale property values (generation %d)", self._generation)

        self._received_description = True
        self._node_types.clear()

        nodes = desc.get("nodes", {})
        if isinstance(nodes, dict):
            for node_id, node_def in nodes.items():
                if isinstance(node_def, dict):
                    node_type = node_def.get("type", "")
                    if isinstance(node_type, str):
                        self._node_types[str(node_id)] = node_type

        # Mark all known nodes dirty
        self._dirty_nodes.update(self._node_types.keys())

        _LOGGER.debug(
            "Parsed $description with %d nodes (generation %d)",
            len(self._node_types),
            self._generation,
        )

        # Lifecycle transition
        if self._received_state_ready:
            self._transition_to_ready()
        elif self._lifecycle in (HomieLifecycle.DISCONNECTED, HomieLifecycle.CONNECTED):
            self._lifecycle = HomieLifecycle.DESCRIPTION_RECEIVED

    def _handle_property(self, node_id: str, prop_id: str, value: str) -> None:
        """Handle a reported property value update."""
        now_s = int(time.time())

        if node_id not in self._property_values:
            self._property_values[node_id] = {}
            self._property_timestamps[node_id] = {}

        old_value = self._property_values[node_id].get(prop_id)

        if old_value == value:
            return  # no change — no dirty, no callbacks, no timestamp bump

        self._property_values[node_id][prop_id] = value
        self._property_timestamps[node_id][prop_id] = now_s
        self._dirty_nodes.add(node_id)

        self._fire_callbacks(node_id, prop_id, value, old_value)

    def _handle_target(self, node_id: str, prop_id: str, value: str) -> None:
        """Handle a $target property value."""
        if node_id not in self._target_values:
            self._target_values[node_id] = {}

        old_target = self._target_values[node_id].get(prop_id)
        if old_target == value:
            return  # no change

        self._target_values[node_id][prop_id] = value
        self._dirty_nodes.add(node_id)

    def _transition_to_ready(self) -> None:
        """Transition lifecycle to READY."""
        self._lifecycle = HomieLifecycle.READY
        self._ready_since = time.monotonic()

    def _fire_callbacks(self, node_id: str, prop_id: str, value: str, old_value: str | None) -> None:
        """Fire all registered property callbacks, catching exceptions."""
        for cb in self._property_callbacks:
            try:
                cb(node_id, prop_id, value, old_value)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Property callback error for %s/%s", node_id, prop_id, exc_info=True)
