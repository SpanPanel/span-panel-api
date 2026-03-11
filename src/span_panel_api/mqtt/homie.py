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
from typing import ClassVar

from ..models import SpanBatterySnapshot, SpanCircuitSnapshot, SpanEvseSnapshot, SpanPanelSnapshot, SpanPVSnapshot
from .const import (
    HOMIE_STATE_READY,
    LUGS_DOWNSTREAM,
    LUGS_UPSTREAM,
    TOPIC_PREFIX,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_CORE,
    TYPE_EVSE,
    TYPE_LUGS,
    TYPE_LUGS_DOWNSTREAM,
    TYPE_LUGS_UPSTREAM,
    TYPE_POWER_FLOWS,
    TYPE_PV,
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

    def __init__(self, serial_number: str, panel_size: int) -> None:
        self._serial_number = serial_number
        self._panel_size = panel_size
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

    def circuit_nodes_missing_names(self) -> list[str]:
        """Return circuit-like node IDs that have no ``name`` property yet."""
        missing: list[str] = []
        for node_id, node_type in self._node_types.items():
            if node_type in self._CIRCUIT_LIKE_TYPES:
                name = self._property_values.get(node_id, {}).get("name")
                if not name:  # None or ""
                    missing.append(node_id)
        return missing

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

    def find_node_by_type(self, type_string: str) -> str | None:
        """Find the first node ID matching a given type."""
        for node_id, node_type in self._node_types.items():
            if node_type == type_string:
                return node_id
        return None

    def _find_lugs_node(self, direction: str) -> str | None:
        """Find the lugs node with a specific direction.

        Handles two firmware conventions:
        - Typed: node type is ``energy.ebus.device.lugs.upstream`` / ``.downstream``
        - Generic: node type is ``energy.ebus.device.lugs`` with a ``direction`` property
        """
        # Typed variant (direction embedded in the type string)
        typed_map = {
            LUGS_UPSTREAM: TYPE_LUGS_UPSTREAM,
            LUGS_DOWNSTREAM: TYPE_LUGS_DOWNSTREAM,
        }
        target_type = typed_map.get(direction)
        if target_type:
            for node_id, node_type in self._node_types.items():
                if node_type == target_type:
                    return node_id

        # Generic variant (single TYPE_LUGS with direction property)
        for node_id, node_type in self._node_types.items():
            if node_type == TYPE_LUGS:
                prop_dir = self._property_values.get(node_id, {}).get("direction", "")
                if prop_dir.upper() == direction:
                    return node_id
        return None

    # Only TYPE_CIRCUIT nodes have the full MQTT property schema (power,
    # energy, relay, space, etc.).  PV and EVSE nodes are metadata-only
    # (feed, nameplate-capacity, vendor-name) and reference the physical
    # circuit via their ``feed`` property.
    _CIRCUIT_LIKE_TYPES: frozenset[str] = frozenset({TYPE_CIRCUIT})

    # Metadata node types whose ``feed`` property points to a circuit,
    # annotating it with a device_type.
    _FEED_TYPE_MAP: ClassVar[dict[str, str]] = {
        TYPE_PV: "pv",
        TYPE_EVSE: "evse",
    }

    def _is_circuit_node(self, node_id: str) -> bool:
        """Check if node is a circuit device."""
        return self._node_types.get(node_id, "") in self._CIRCUIT_LIKE_TYPES

    def _build_feed_metadata(self) -> dict[str, dict[str, str]]:
        """Build mapping of circuit node_id → metadata from PV/EVSE feed references.

        Returns dict keyed by circuit node_id with values containing:
          - device_type: "pv" | "evse"
          - relative_position: "IN_PANEL" | "UPSTREAM" | "DOWNSTREAM" | ""
        """
        feed_meta: dict[str, dict[str, str]] = {}
        for node_id, node_type in self._node_types.items():
            device_type = self._FEED_TYPE_MAP.get(node_type)
            if device_type:
                feed_circuit = self._get_prop(node_id, "feed")
                if feed_circuit:
                    rel_pos = self._get_prop(node_id, "relative-position")
                    feed_meta[feed_circuit] = {
                        "device_type": device_type,
                        "relative_position": rel_pos.upper() if rel_pos else "",
                    }
        return feed_meta

    def _build_circuit(self, node_id: str, device_type: str = "circuit", relative_position: str = "") -> SpanCircuitSnapshot:
        """Build a circuit snapshot from accumulated properties."""
        circuit_id = normalize_circuit_id(node_id)

        # active-power is in watts; negate so positive = consumption
        raw_power_w = _parse_float(self._get_prop(node_id, "active-power"))
        instant_power_w = -raw_power_w or 0.0

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
            device_type=device_type,
            relative_position=relative_position,
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
        bess_node = self.find_node_by_type(TYPE_BESS)
        if bess_node is None:
            return SpanBatterySnapshot()

        soc_str = self._get_prop(bess_node, "soc")
        soe_str = self._get_prop(bess_node, "soe")

        vn = self._get_prop(bess_node, "vendor-name")
        pn = self._get_prop(bess_node, "product-name")
        mdl = self._get_prop(bess_node, "model")
        sn = self._get_prop(bess_node, "serial-number")
        sw = self._get_prop(bess_node, "software-version")
        nc = self._get_prop(bess_node, "nameplate-capacity")
        conn = self._get_prop(bess_node, "connected")

        return SpanBatterySnapshot(
            soe_percentage=_parse_float(soc_str) if soc_str else None,
            soe_kwh=_parse_float(soe_str) if soe_str else None,
            vendor_name=vn if vn else None,
            product_name=pn if pn else None,
            model=mdl if mdl else None,
            serial_number=sn if sn else None,
            software_version=sw if sw else None,
            nameplate_capacity_kwh=_parse_float(nc) if nc else None,
            connected=conn.lower() == "true" if conn else None,
        )

    def _build_pv(self) -> SpanPVSnapshot:
        """Build PV snapshot from the first PV metadata node."""
        pv_node = self.find_node_by_type(TYPE_PV)
        if pv_node is None:
            return SpanPVSnapshot()

        vn = self._get_prop(pv_node, "vendor-name")
        pn = self._get_prop(pv_node, "product-name")
        nc = self._get_prop(pv_node, "nameplate-capacity")

        return SpanPVSnapshot(
            vendor_name=vn if vn else None,
            product_name=pn if pn else None,
            nameplate_capacity_w=_parse_float(nc) if nc else None,
        )

    def _build_evse_devices(self) -> dict[str, SpanEvseSnapshot]:
        """Build EVSE snapshots from all EVSE metadata nodes."""
        result: dict[str, SpanEvseSnapshot] = {}
        for node_id, node_type in self._node_types.items():
            if node_type != TYPE_EVSE:
                continue
            feed = self._get_prop(node_id, "feed")
            if not feed:
                continue
            adv = self._get_prop(node_id, "advertised-current")
            result[node_id] = SpanEvseSnapshot(
                node_id=node_id,
                feed_circuit_id=normalize_circuit_id(feed),
                status=self._get_prop(node_id, "status") or "UNKNOWN",
                lock_state=self._get_prop(node_id, "lock-state") or "UNKNOWN",
                advertised_current_a=_parse_float(adv) if adv else None,
                vendor_name=self._get_prop(node_id, "vendor-name") or None,
                product_name=self._get_prop(node_id, "product-name") or None,
                part_number=self._get_prop(node_id, "part-number") or None,
                serial_number=self._get_prop(node_id, "serial-number") or None,
                software_version=self._get_prop(node_id, "software-version") or None,
            )
        return result

    def _derive_dsm_state(self, core_node: str | None, grid_power: float, power_flow_grid: float | None) -> str:
        """Derive dsm_state from multiple signals.

        Priority:
        1. bess/grid-state — authoritative when BESS is commissioned
        2. dominant-power-source == GRID — grid is the primary source
        3. grid_power or power_flow_grid non-zero — grid exchanging power
        4. both grid signals zero AND DPS != GRID — islanded
        """
        # 1. BESS grid-state is authoritative when available
        bess_node = self.find_node_by_type(TYPE_BESS)
        if bess_node is not None:
            gs = self._get_prop(bess_node, "grid-state")
            if gs == "ON_GRID":
                return "DSM_ON_GRID"
            if gs == "OFF_GRID":
                return "DSM_OFF_GRID"

        # 2-4. Fallback heuristic using DPS and grid power signals
        if core_node is not None:
            dps = self._get_prop(core_node, "dominant-power-source")
            if dps == "GRID":
                return "DSM_ON_GRID"

            if dps in ("BATTERY", "PV", "GENERATOR"):
                grid_exchanging = grid_power != 0.0 or (power_flow_grid is not None and power_flow_grid != 0.0)
                return "DSM_ON_GRID" if grid_exchanging else "DSM_OFF_GRID"

        return "UNKNOWN"

    def _derive_run_config(self, dsm_state: str, grid_islandable: bool | None, dps: str | None) -> str:
        """Derive current_run_config from grid state, islandability, and power source.

        Decision table:
        - DSM_ON_GRID → PANEL_ON_GRID (regardless of islandable)
        - DSM_OFF_GRID + islandable + BATTERY → PANEL_BACKUP
        - DSM_OFF_GRID + islandable + PV/GENERATOR → PANEL_OFF_GRID
        - DSM_OFF_GRID + islandable + other → UNKNOWN
        - DSM_OFF_GRID + not islandable → UNKNOWN (shouldn't happen)
        """
        if dsm_state == "DSM_ON_GRID":
            return "PANEL_ON_GRID"

        if dsm_state == "DSM_OFF_GRID":
            if not grid_islandable:
                return "UNKNOWN"
            if dps == "BATTERY":
                return "PANEL_BACKUP"
            if dps in ("PV", "GENERATOR"):
                return "PANEL_OFF_GRID"
            return "UNKNOWN"

        return "UNKNOWN"

    def _build_unmapped_tabs(
        self,
        circuits: dict[str, SpanCircuitSnapshot],
    ) -> dict[str, SpanCircuitSnapshot]:
        """Synthesize unmapped tab entries for breaker positions with no circuit.

        Creates zero-power SpanCircuitSnapshot entries for unoccupied positions
        up to ``self._panel_size``.
        """
        occupied_tabs: set[int] = set()
        for circuit in circuits.values():
            occupied_tabs.update(circuit.tabs)

        unmapped: dict[str, SpanCircuitSnapshot] = {}
        for tab in range(1, self._panel_size + 1):
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
        core_node = self.find_node_by_type(TYPE_CORE)
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

        # Upstream lugs → main meter (grid connection)
        # imported-energy = energy imported from the grid = consumed by the house
        # exported-energy = energy exported to the grid = produced (solar)
        grid_power = 0.0
        main_consumed = 0.0
        main_produced = 0.0
        upstream_l1_current: float | None = None
        upstream_l2_current: float | None = None
        if upstream_lugs is not None:
            grid_power = _parse_float(self._get_prop(upstream_lugs, "active-power"))
            main_consumed = _parse_float(self._get_prop(upstream_lugs, "imported-energy"))
            main_produced = _parse_float(self._get_prop(upstream_lugs, "exported-energy"))

            l1_i = self._get_prop(upstream_lugs, "l1-current")
            upstream_l1_current = _parse_float(l1_i) if l1_i else None
            l2_i = self._get_prop(upstream_lugs, "l2-current")
            upstream_l2_current = _parse_float(l2_i) if l2_i else None

        # Downstream lugs → feedthrough
        feedthrough_power = 0.0
        feedthrough_consumed = 0.0
        feedthrough_produced = 0.0
        downstream_l1_current: float | None = None
        downstream_l2_current: float | None = None
        if downstream_lugs is not None:
            feedthrough_power = _parse_float(self._get_prop(downstream_lugs, "active-power"))
            feedthrough_consumed = _parse_float(self._get_prop(downstream_lugs, "imported-energy"))
            feedthrough_produced = _parse_float(self._get_prop(downstream_lugs, "exported-energy"))

            dl1_i = self._get_prop(downstream_lugs, "l1-current")
            downstream_l1_current = _parse_float(dl1_i) if dl1_i else None
            dl2_i = self._get_prop(downstream_lugs, "l2-current")
            downstream_l2_current = _parse_float(dl2_i) if dl2_i else None

        # Power flows
        pf_node = self.find_node_by_type(TYPE_POWER_FLOWS)
        power_flow_pv: float | None = None
        power_flow_battery: float | None = None
        power_flow_grid: float | None = None
        power_flow_site: float | None = None
        if pf_node is not None:
            pf_pv = self._get_prop(pf_node, "pv")
            power_flow_pv = _parse_float(pf_pv) if pf_pv else None
            pf_bat = self._get_prop(pf_node, "battery")
            power_flow_battery = _parse_float(pf_bat) if pf_bat else None
            pf_grid = self._get_prop(pf_node, "grid")
            power_flow_grid = _parse_float(pf_grid) if pf_grid else None
            pf_site = self._get_prop(pf_node, "site")
            power_flow_site = _parse_float(pf_site) if pf_site else None

        # Build metadata annotations from PV/EVSE metadata nodes
        feed_metadata = self._build_feed_metadata()

        # Circuits
        circuits: dict[str, SpanCircuitSnapshot] = {}
        for node_id in self._node_types:
            if self._is_circuit_node(node_id):
                meta = feed_metadata.get(node_id, {})
                device_type = meta.get("device_type", "circuit")
                relative_position = meta.get("relative_position", "")
                circuit = self._build_circuit(node_id, device_type, relative_position)
                circuits[circuit.circuit_id] = circuit

        # Synthesize unmapped tab entries
        unmapped = self._build_unmapped_tabs(circuits)
        circuits.update(unmapped)

        # Battery, PV, and EVSE metadata
        battery = self._build_battery()
        pv = self._build_pv()
        evse = self._build_evse_devices()

        # BESS grid state for v2-native field
        bess_node = self.find_node_by_type(TYPE_BESS)
        grid_state: str | None = None
        if bess_node is not None:
            gs = self._get_prop(bess_node, "grid-state")
            grid_state = gs if gs else None

        # Derived state values
        dsm_state = self._derive_dsm_state(core_node, grid_power, power_flow_grid)
        current_run_config = self._derive_run_config(dsm_state, grid_islandable, dominant_power_source)

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
            dsm_state=dsm_state,
            current_run_config=current_run_config,
            door_state=door_state,
            proximity_proven=self._state == HOMIE_STATE_READY,
            uptime_s=uptime,
            eth0_link=eth0,
            wlan_link=wlan,
            wwan_link=wwan_connected,
            panel_size=self._panel_size,
            dominant_power_source=dominant_power_source,
            grid_state=grid_state,
            grid_islandable=grid_islandable,
            l1_voltage=l1_voltage,
            l2_voltage=l2_voltage,
            main_breaker_rating_a=main_breaker,
            wifi_ssid=wifi_ssid,
            vendor_cloud=vendor_cloud,
            power_flow_pv=power_flow_pv,
            power_flow_battery=power_flow_battery,
            power_flow_grid=power_flow_grid,
            power_flow_site=power_flow_site,
            upstream_l1_current_a=upstream_l1_current,
            upstream_l2_current_a=upstream_l2_current,
            downstream_l1_current_a=downstream_l1_current,
            downstream_l2_current_a=downstream_l2_current,
            circuits=circuits,
            battery=battery,
            pv=pv,
            evse=evse,
        )
