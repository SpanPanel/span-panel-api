"""Assemble a ``SpanPanelSnapshot`` from a discovered v1.0 device tree.

Sorting the tree into roles is the one job here, and it is done by **declared
device type**, never by device id. The reference tree's ids (``bess``, ``pv``,
``lugs-upstream``) are the simulator's naming; real firmware uses whatever it
likes, and the type string is what the schema defines.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1.circuits import build_circuit
from span_panel_api_schema_1.const import (
    NODE_INFO,
    PROP_MODEL,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_EVSE,
    TYPE_LUGS,
    TYPE_MID,
    TYPE_PV,
    UNKNOWN,
)
from span_panel_api_schema_1.devices import build_battery, build_evse, build_pv, feed_circuit_ids
from span_panel_api_schema_1.panel import PanelFields, build_unmapped_tabs, find_lugs, panel_size_from_model, text

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice


def device_type(device: DiscoveredDevice) -> str:
    """The device's declared type from its description, or '' before it arrives.

    A device exists in the tree from the moment its parent names it as a child,
    so an empty type is the normal mid-discovery state rather than an error.
    """
    description: dict[str, object] = device.description or {}
    declared = description.get("type")
    return str(declared) if declared else ""


class TreeRoles:
    """The tree sorted into the roles a snapshot needs.

    Matching is prefix-based for lugs, because firmware may declare either the
    base ``…device.lugs`` type with a ``direction`` property or a subtyped
    ``…device.lugs.upstream`` — the flat adapter already had to handle both
    conventions, and there is no reason to assume v1.0 settled it.
    """

    def __init__(self, devices: list[DiscoveredDevice]) -> None:
        self.circuits: list[DiscoveredDevice] = []
        self.lugs: list[DiscoveredDevice] = []
        self.evse: list[DiscoveredDevice] = []
        self.bess: DiscoveredDevice | None = None
        self.pv: DiscoveredDevice | None = None
        self.mid: DiscoveredDevice | None = None

        for device in devices:
            declared = device_type(device)
            if declared == TYPE_CIRCUIT:
                self.circuits.append(device)
            elif declared.startswith(TYPE_LUGS):
                self.lugs.append(device)
            elif declared == TYPE_EVSE:
                self.evse.append(device)
            elif declared == TYPE_BESS and self.bess is None:
                self.bess = device
            elif declared == TYPE_PV and self.pv is None:
                self.pv = device
            elif declared == TYPE_MID and self.mid is None:
                self.mid = device


def build_snapshot(panel: DiscoveredDevice, children: list[DiscoveredDevice], ready_since: float = 0.0) -> SpanPanelSnapshot:
    """Build a full snapshot from the panel and its descendants."""
    roles = TreeRoles(children)
    upstream = find_lugs(roles.lugs, upstream=True)
    downstream = find_lugs(roles.lugs, upstream=False)
    fields = PanelFields(panel=panel, upstream_lugs=upstream, downstream_lugs=downstream, mid=roles.mid)

    feeds = feed_circuit_ids(roles.circuits)
    # A DER's device type decides how its feeding circuit is labelled, so the
    # circuit inherits it — matching the flat adapter, where the same circuit
    # reports device_type "pv" rather than "circuit".
    der_type_by_circuit = {
        circuit_id: kind
        for kind, device in (("pv", roles.pv), *(("evse", e) for e in roles.evse))
        if device is not None and (circuit_id := feeds.get(device.device_id))
    }

    circuits = {}
    for circuit in roles.circuits:
        snapshot = build_circuit(circuit, device_type=der_type_by_circuit.get(circuit.device_id, "circuit"))
        circuits[snapshot.circuit_id] = snapshot

    occupied = {tab for circuit in circuits.values() for tab in circuit.tabs}
    # Unoccupied positions are `total - occupied`, so this is only meaningful
    # when the model gave a real total. An unknown model yields size 0 and no
    # unmapped entries rather than a fabricated set.
    panel_size = panel_size_from_model(text(panel, NODE_INFO, PROP_MODEL))
    circuits.update(build_unmapped_tabs(panel_size, occupied))

    # Owners are every device that can claim a DER through a `connection` node.
    owners = [*roles.lugs, *roles.circuits, panel]

    return SpanPanelSnapshot(
        serial_number=fields.serial_number,
        firmware_version=fields.firmware_version,
        main_relay_state=fields.main_relay_state,
        instant_grid_power_w=fields.instant_grid_power_w,
        feedthrough_power_w=fields.feedthrough_power_w,
        main_meter_energy_consumed_wh=fields.main_meter_energy_consumed_wh,
        main_meter_energy_produced_wh=fields.main_meter_energy_produced_wh,
        feedthrough_energy_consumed_wh=fields.feedthrough_energy_consumed_wh,
        feedthrough_energy_produced_wh=fields.feedthrough_energy_produced_wh,
        # Both are v1 fields the flat adapter derives from multiple v2 signals.
        # Left UNKNOWN here rather than reproducing that heuristic against a
        # schema whose inputs moved: `dominant-power-source` and
        # `grid-islandable` — two of its three inputs — no longer exist.
        dsm_state=UNKNOWN,
        current_run_config=UNKNOWN,
        door_state=fields.door_state,
        # The panel has no proximity sensor property; the flat adapter reports
        # authenticated-and-ready, and the same holds here.
        proximity_proven=True,
        uptime_s=int(time.monotonic() - ready_since) if ready_since > 0.0 else 0,
        eth0_link=fields.eth0_link,
        wlan_link=fields.wlan_link,
        wwan_link=fields.wwan_link,
        panel_size=panel_size,
        dominant_power_source=fields.dominant_power_source,
        grid_state=fields.grid_state,
        grid_islandable=fields.grid_islandable,
        l1_voltage=fields.l1_voltage,
        l2_voltage=fields.l2_voltage,
        main_breaker_rating_a=fields.main_breaker_rating_a,
        wifi_ssid=fields.wifi_ssid,
        vendor_cloud=fields.vendor_cloud,
        power_flow_pv=fields.power_flow_pv,
        power_flow_battery=fields.power_flow_battery,
        power_flow_grid=fields.power_flow_grid,
        power_flow_site=fields.power_flow_site,
        upstream_l1_current_a=fields.upstream_l1_current_a,
        upstream_l2_current_a=fields.upstream_l2_current_a,
        downstream_l1_current_a=fields.downstream_l1_current_a,
        downstream_l2_current_a=fields.downstream_l2_current_a,
        circuits=circuits,
        battery=build_battery(roles.bess, owners),
        pv=build_pv(roles.pv, feeds),
        evse={device.device_id: build_evse(device, feeds) for device in roles.evse},
    )
