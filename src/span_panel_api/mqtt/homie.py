"""Homie v5 device consumer for SPAN Panel.

Parses Homie device description and tracks property values from MQTT
messages. Builds transport-agnostic SpanPanelSnapshot from the
accumulated state.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import time

from ..models import SpanBatterySnapshot, SpanCircuitSnapshot, SpanPanelSnapshot
from .const import (
    HOMIE_STATE_READY,
    LUGS_DOWNSTREAM,
    LUGS_UPSTREAM,
    TOPIC_PREFIX,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_CORE,
    TYPE_LUGS,
    normalize_circuit_id,
)

_LOGGER = logging.getLogger(__name__)

# Callback signature: (node_id, prop_id, value, old_value)
PropertyCallback = Callable[[str, str, str, str | None], None]


def _parse_bool(value: str) -> bool:
    """Parse a Homie boolean string."""
    return value.lower() == "true"


def _parse_float(value: str, default: float = 0.0) -> float:
    """Parse a float string, returning default on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _parse_int(value: str, default: int = 0) -> int:
    """Parse an integer string, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class HomieDeviceConsumer:
    """Parse Homie device description and track property values.

    All methods must be called from the asyncio event loop thread
    (guaranteed by AsyncMqttBridge's call_soon_threadsafe dispatch).
    """

    def __init__(self, serial_number: str) -> None:
        self._serial_number = serial_number
        self._topic_prefix = f"{TOPIC_PREFIX}/{serial_number}"

        self._state: str = ""
        self._description: dict[str, object] | None = None
        self._property_values: dict[str, dict[str, str]] = {}
        self._property_timestamps: dict[str, dict[str, int]] = {}
        self._node_types: dict[str, str] = {}
        self._ready_since: float = 0.0
        self._property_callbacks: list[PropertyCallback] = []

    def is_ready(self) -> bool:
        """True when $state == ready and $description has been parsed."""
        return self._state == HOMIE_STATE_READY and self._description is not None

    def handle_message(self, topic: str, payload: str) -> None:
        """Route an MQTT message to the appropriate handler."""
        if not topic.startswith(self._topic_prefix):
            return

        suffix = topic[len(self._topic_prefix) + 1 :]  # strip prefix + "/"

        if suffix == "$state":
            self._handle_state(payload)
        elif suffix == "$description":
            self._handle_description(payload)
        elif "/" in suffix and not suffix.endswith("/set"):
            # Property value: {node_id}/{property_id}
            parts = suffix.split("/", 1)
            self._handle_property(parts[0], parts[1], payload)

    def register_property_callback(self, callback: PropertyCallback) -> Callable[[], None]:
        """Register callback(node_id, prop_id, value, old_value).

        Returns an unregister function.
        """
        self._property_callbacks.append(callback)

        def unregister() -> None:
            try:
                self._property_callbacks.remove(callback)
            except ValueError:
                _LOGGER.debug("Callback already unregistered")

        return unregister

    def build_snapshot(self) -> SpanPanelSnapshot:
        """Build a point-in-time snapshot from current property values.

        Must be called after is_ready() returns True.
        """
        return self._build_snapshot()

    # -- Internal handlers -------------------------------------------------

    def _handle_state(self, payload: str) -> None:
        """Handle $state topic."""
        self._state = payload
        if payload == HOMIE_STATE_READY and self._ready_since == 0.0:
            self._ready_since = time.monotonic()
        _LOGGER.debug("Homie $state: %s", payload)

    def _handle_description(self, payload: str) -> None:
        """Parse $description JSON and extract node type mappings."""
        try:
            desc = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Invalid $description JSON")
            return

        self._description = desc
        self._node_types.clear()

        # Extract node types from description
        nodes = desc.get("nodes", {})
        if isinstance(nodes, dict):
            for node_id, node_def in nodes.items():
                if isinstance(node_def, dict):
                    node_type = node_def.get("type", "")
                    if isinstance(node_type, str):
                        self._node_types[str(node_id)] = node_type

        _LOGGER.debug("Parsed $description with %d nodes", len(self._node_types))

    def _handle_property(self, node_id: str, prop_id: str, value: str) -> None:
        """Handle a property value update."""
        now_s = int(time.time())

        if node_id not in self._property_values:
            self._property_values[node_id] = {}
            self._property_timestamps[node_id] = {}

        old_value = self._property_values[node_id].get(prop_id)
        self._property_values[node_id][prop_id] = value
        self._property_timestamps[node_id][prop_id] = now_s

        for cb in self._property_callbacks:
            try:
                cb(node_id, prop_id, value, old_value)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Property callback error for %s/%s", node_id, prop_id)

    # -- Snapshot building --------------------------------------------------

    def _get_prop(self, node_id: str, prop_id: str, default: str = "") -> str:
        """Get a property value."""
        return self._property_values.get(node_id, {}).get(prop_id, default)

    def _get_timestamp(self, node_id: str, prop_id: str) -> int:
        """Get a property timestamp."""
        return self._property_timestamps.get(node_id, {}).get(prop_id, 0)

    def _find_node_by_type(self, type_string: str) -> str | None:
        """Find the first node ID matching a given type."""
        for node_id, node_type in self._node_types.items():
            if node_type == type_string:
                return node_id
        return None

    def _find_lugs_node(self, direction: str) -> str | None:
        """Find the lugs node with a specific direction."""
        for node_id, node_type in self._node_types.items():
            if node_type == TYPE_LUGS and self._property_values.get(node_id, {}).get("direction") == direction:
                return node_id
        return None

    def _is_circuit_node(self, node_id: str) -> bool:
        """Check if node is a circuit by its type in $description."""
        return self._node_types.get(node_id) == TYPE_CIRCUIT

    def _build_circuit(self, node_id: str) -> SpanCircuitSnapshot:
        """Build a circuit snapshot from accumulated properties."""
        circuit_id = normalize_circuit_id(node_id)

        # active-power in schema is kW for circuits — convert to W
        raw_power_kw = _parse_float(self._get_prop(node_id, "active-power"))
        # Negate: Homie negative=consumption → positive=consumption
        instant_power_w = -raw_power_kw * 1000.0

        # Energy: exported-energy = consumption (panel exports TO circuit)
        consumed_wh = _parse_float(self._get_prop(node_id, "exported-energy"))
        # imported-energy = production (panel imports FROM circuit)
        produced_wh = _parse_float(self._get_prop(node_id, "imported-energy"))

        # Tabs: derived from space + dipole
        # Dipole circuits occupy two consecutive spaces on the same bus bar
        # side: [space, space + 2] (odd+odd or even+even)
        space_val = self._get_prop(node_id, "space")
        is_dipole = _parse_bool(self._get_prop(node_id, "dipole"))
        tabs: list[int] = []
        if space_val:
            space = _parse_int(space_val)
            tabs = [space, space + 2] if is_dipole else [space]

        always_on = _parse_bool(self._get_prop(node_id, "always-on"))

        # Timestamps from MQTT arrival time
        energy_ts = max(
            self._get_timestamp(node_id, "exported-energy"),
            self._get_timestamp(node_id, "imported-energy"),
        )
        power_ts = self._get_timestamp(node_id, "active-power")

        return SpanCircuitSnapshot(
            circuit_id=circuit_id,
            name=self._get_prop(node_id, "name"),
            relay_state=self._get_prop(node_id, "relay", "UNKNOWN"),
            instant_power_w=instant_power_w,
            produced_energy_wh=produced_wh,
            consumed_energy_wh=consumed_wh,
            tabs=tabs,
            priority=self._get_prop(node_id, "shed-priority", "UNKNOWN"),
            is_user_controllable=not always_on,
            is_sheddable=_parse_bool(self._get_prop(node_id, "sheddable")),
            is_never_backup=_parse_bool(self._get_prop(node_id, "never-backup")),
            is_240v=_parse_bool(self._get_prop(node_id, "dipole")),
            current_a=_parse_float(self._get_prop(node_id, "current")) if self._get_prop(node_id, "current") else None,
            breaker_rating_a=(
                _parse_float(self._get_prop(node_id, "breaker-rating"))
                if self._get_prop(node_id, "breaker-rating")
                else None
            ),
            always_on=always_on,
            relay_requester=self._get_prop(node_id, "relay-requester", "UNKNOWN"),
            energy_accum_update_time_s=energy_ts,
            instant_power_update_time_s=power_ts,
        )

    def _build_battery(self) -> SpanBatterySnapshot:
        """Build battery snapshot from BESS node."""
        bess_node = self._find_node_by_type(TYPE_BESS)
        if bess_node is None:
            return SpanBatterySnapshot()

        soc_str = self._get_prop(bess_node, "soc")
        soe_str = self._get_prop(bess_node, "soe")

        return SpanBatterySnapshot(
            soe_percentage=_parse_float(soc_str) if soc_str else None,
            soe_kwh=_parse_float(soe_str) if soe_str else None,
        )

    def _derive_dsm_state(self, core_node: str | None) -> str:
        """Derive v1-compatible dsm_state from dominant-power-source."""
        if core_node is None:
            return "UNKNOWN"
        dps = self._get_prop(core_node, "dominant-power-source")
        if dps == "GRID":
            return "DSM_GRID_UP"
        if dps in ("BATTERY", "PV", "GENERATOR"):
            return "DSM_GRID_DOWN"
        return "UNKNOWN"

    def _derive_dsm_grid_state(self) -> str:
        """Derive v1-compatible dsm_grid_state from bess/grid-state."""
        bess_node = self._find_node_by_type(TYPE_BESS)
        if bess_node is None:
            return "UNKNOWN"
        grid_state = self._get_prop(bess_node, "grid-state")
        if grid_state == "ON_GRID":
            return "DSM_ON_GRID"
        if grid_state == "OFF_GRID":
            return "DSM_OFF_GRID"
        return "UNKNOWN"

    def _derive_run_config(self, core_node: str | None) -> str:
        """Derive v1-compatible current_run_config from dominant-power-source."""
        if core_node is None:
            return "UNKNOWN"
        dps = self._get_prop(core_node, "dominant-power-source")
        if dps == "GRID":
            return "PANEL_ON_GRID"
        if dps in ("BATTERY", "PV", "GENERATOR"):
            return "PANEL_OFF_GRID"
        return "UNKNOWN"

    def _build_unmapped_tabs(self, circuits: dict[str, SpanCircuitSnapshot]) -> dict[str, SpanCircuitSnapshot]:
        """Synthesize unmapped tab entries for breaker positions with no circuit.

        Determines panel size from the highest occupied tab, then creates
        zero-power SpanCircuitSnapshot entries for unoccupied positions.
        """
        # Collect all occupied tabs from commissioned circuits
        occupied_tabs: set[int] = set()
        for circuit in circuits.values():
            occupied_tabs.update(circuit.tabs)

        if not occupied_tabs:
            return {}

        # Panel size is the highest occupied tab (rounded up to even
        # to cover both bus bar sides)
        max_tab = max(occupied_tabs)
        panel_size = max_tab if max_tab % 2 == 0 else max_tab + 1

        # Synthesize entries for unoccupied positions
        unmapped: dict[str, SpanCircuitSnapshot] = {}
        for tab in range(1, panel_size + 1):
            if tab not in occupied_tabs:
                circuit_id = f"unmapped_tab_{tab}"
                unmapped[circuit_id] = SpanCircuitSnapshot(
                    circuit_id=circuit_id,
                    name=f"Unmapped Tab {tab}",
                    relay_state="CLOSED",
                    instant_power_w=0.0,
                    produced_energy_wh=0.0,
                    consumed_energy_wh=0.0,
                    tabs=[tab],
                    priority="UNKNOWN",
                    is_user_controllable=False,
                    is_sheddable=False,
                    is_never_backup=False,
                )

        return unmapped

    def _build_snapshot(self) -> SpanPanelSnapshot:
        """Build full snapshot from accumulated property values."""
        core_node = self._find_node_by_type(TYPE_CORE)
        upstream_lugs = self._find_lugs_node(LUGS_UPSTREAM)
        downstream_lugs = self._find_lugs_node(LUGS_DOWNSTREAM)

        # Core properties
        firmware = ""
        door_state = "UNKNOWN"
        main_relay = "UNKNOWN"
        eth0 = False
        wlan = False
        wwan_connected = False
        dominant_power_source: str | None = None
        grid_islandable: bool | None = None
        l1_voltage: float | None = None
        l2_voltage: float | None = None
        main_breaker: int | None = None
        wifi_ssid: str | None = None
        vendor_cloud: str | None = None

        if core_node is not None:
            firmware = self._get_prop(core_node, "software-version")
            door_state = self._get_prop(core_node, "door", "UNKNOWN")
            main_relay = self._get_prop(core_node, "relay", "UNKNOWN")
            eth0 = _parse_bool(self._get_prop(core_node, "ethernet"))
            wlan = _parse_bool(self._get_prop(core_node, "wifi"))

            vc = self._get_prop(core_node, "vendor-cloud")
            wwan_connected = vc == "CONNECTED"
            vendor_cloud = vc if vc else None

            dps = self._get_prop(core_node, "dominant-power-source")
            dominant_power_source = dps if dps else None

            gi = self._get_prop(core_node, "grid-islandable")
            grid_islandable = _parse_bool(gi) if gi else None

            l1v = self._get_prop(core_node, "l1-voltage")
            l1_voltage = _parse_float(l1v) if l1v else None

            l2v = self._get_prop(core_node, "l2-voltage")
            l2_voltage = _parse_float(l2v) if l2v else None

            br = self._get_prop(core_node, "breaker-rating")
            main_breaker = _parse_int(br) if br else None

            ws = self._get_prop(core_node, "wifi-ssid")
            wifi_ssid = ws if ws else None

        # Upstream lugs → main meter
        grid_power = 0.0
        main_consumed = 0.0
        main_produced = 0.0
        if upstream_lugs is not None:
            # Lugs active-power is in W (unlike circuit which is kW)
            grid_power = _parse_float(self._get_prop(upstream_lugs, "active-power"))
            main_consumed = _parse_float(self._get_prop(upstream_lugs, "exported-energy"))
            main_produced = _parse_float(self._get_prop(upstream_lugs, "imported-energy"))

        # Downstream lugs → feedthrough
        feedthrough_power = 0.0
        feedthrough_consumed = 0.0
        feedthrough_produced = 0.0
        if downstream_lugs is not None:
            feedthrough_power = _parse_float(self._get_prop(downstream_lugs, "active-power"))
            feedthrough_consumed = _parse_float(self._get_prop(downstream_lugs, "exported-energy"))
            feedthrough_produced = _parse_float(self._get_prop(downstream_lugs, "imported-energy"))

        # Circuits
        circuits: dict[str, SpanCircuitSnapshot] = {}
        for node_id in self._node_types:
            if self._is_circuit_node(node_id):
                circuit = self._build_circuit(node_id)
                circuits[circuit.circuit_id] = circuit

        # Synthesize unmapped tab entries
        unmapped = self._build_unmapped_tabs(circuits)
        circuits.update(unmapped)

        # Battery
        battery = self._build_battery()

        # BESS grid state for v2-native field
        bess_node = self._find_node_by_type(TYPE_BESS)
        grid_state: str | None = None
        if bess_node is not None:
            gs = self._get_prop(bess_node, "grid-state")
            grid_state = gs if gs else None

        # Connection uptime since $state==ready
        uptime = int(time.monotonic() - self._ready_since) if self._ready_since > 0.0 else 0

        return SpanPanelSnapshot(
            serial_number=self._serial_number,
            firmware_version=firmware,
            main_relay_state=main_relay,
            instant_grid_power_w=grid_power,
            feedthrough_power_w=feedthrough_power,
            main_meter_energy_consumed_wh=main_consumed,
            main_meter_energy_produced_wh=main_produced,
            feedthrough_energy_consumed_wh=feedthrough_consumed,
            feedthrough_energy_produced_wh=feedthrough_produced,
            dsm_state=self._derive_dsm_state(core_node),
            dsm_grid_state=self._derive_dsm_grid_state(),
            current_run_config=self._derive_run_config(core_node),
            door_state=door_state,
            proximity_proven=self._state == HOMIE_STATE_READY,
            uptime_s=uptime,
            eth0_link=eth0,
            wlan_link=wlan,
            wwan_link=wwan_connected,
            dominant_power_source=dominant_power_source,
            grid_state=grid_state,
            grid_islandable=grid_islandable,
            l1_voltage=l1_voltage,
            l2_voltage=l2_voltage,
            main_breaker_rating_a=main_breaker,
            wifi_ssid=wifi_ssid,
            vendor_cloud=vendor_cloud,
            circuits=circuits,
            battery=battery,
        )
