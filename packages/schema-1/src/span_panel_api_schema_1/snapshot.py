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
    PROP_SERIAL_NUMBER,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_EVSE,
    TYPE_INVERTER,
    TYPE_LUGS,
    TYPE_MID,
    TYPE_PV,
)
from span_panel_api_schema_1.devices import build_battery, build_evse, build_mid, build_pv, feed_circuit_ids
from span_panel_api_schema_1.panel import (
    PanelFields,
    build_unmapped_tabs,
    find_lugs,
    panel_size_from_model,
    resolve_dominant_power_source,
    resolve_dsm_state,
    resolve_grid_islandable,
    resolve_islanding_state,
    resolve_run_config,
    text,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

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

    # Grid answers are read from the MID rather than derived, per the recorded
    # decision. `device_types` resolves `grid-forming-entity` to a device class, which
    # is what makes PANEL_BACKUP distinguishable from PANEL_OFF_GRID authoritatively
    # instead of guessed from a power source the way flat had to.
    device_types = {device.device_id: device_type(device) for device in children}
    device_names: dict[str, str] = {}
    for device in children:
        description: dict[str, object] = device.description or {}
        name = description.get("name")
        if name:
            device_names[device.device_id] = str(name)
    inverters = [device for device in children if device_type(device) == TYPE_INVERTER]
    islanding = resolve_islanding_state(roles.mid, panel)

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
        # Read, not derived. Flat had to infer both from `dominant-power-source`
        # plus grid power because nothing stated them; v1.0 states them on the MID,
        # so the multi-signal heuristic is gone and only the no-MID tier remains a
        # heuristic. The user-visible value set is unchanged.
        dsm_state=resolve_dsm_state(islanding),
        current_run_config=resolve_run_config(roles.mid, islanding, device_types),
        door_state=fields.door_state,
        # The panel has no proximity sensor property; the flat adapter reports
        # authenticated-and-ready, and the same holds here.
        proximity_proven=True,
        uptime_s=int(time.monotonic() - ready_since) if ready_since > 0.0 else 0,
        eth0_link=fields.eth0_link,
        wlan_link=fields.wlan_link,
        wwan_link=fields.wwan_link,
        panel_size=panel_size,
        dominant_power_source=resolve_dominant_power_source(roles.mid, device_types),
        grid_state=fields.grid_state,
        grid_islandable=resolve_grid_islandable(inverters),
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
        pv=build_pv(roles.pv, feeds, upstream, downstream),
        mid=build_mid(roles.mid, device_names),
        evse={key: build_evse(device, feeds, node_id=key) for device, key in _harmonised_evse_keys(roles.evse).items()},
    )


def _harmonised_evse_keys(evse_devices: Sequence[DiscoveredDevice]) -> dict[DiscoveredDevice, str]:
    """Key each EVSE by its serial, which is what flat firmware keys it by.

    **This library is the harmonisation layer.** The integration builds an EVSE
    entity's `unique_id` and its device-registry `identifiers` from what it finds
    here, so a key that changes between schemas orphans a user's charger and stands a
    duplicate up beside it. Presenting the same handle for the same physical device is
    this seam's job, not the integration's.

    On real flat firmware the EVSE **node id is the Drive's serial**. Confirmed on a
    live panel in SpanPanel/span#214: the reporter's topic is
    `ebus/5/<panel-serial>/<drive-serial>`, their diagnostics show the snapshot keyed
    `"evse": {"<drive-serial>": ...}`, and the whole thread turns on that node id being
    what the `unique_id` is built from. `schema_0` writes `result[node_id]` verbatim,
    so against firmware it is already serial-keyed without knowing it.

    v1.0 names the same device `<panel>-<serial>`, the proxied form, so stripping to
    the serial reproduces flat's key exactly. The proxy model prescribes the same
    thing independently: `devices/proxy.md` says a proxied device id is *not* stable
    across the proxy-to-native transition, and that "consumers that need
    cross-transition stable identity use `info/serial-number`".

    **The flat simulator does not do this**, and it briefly cost us the wrong design.
    It names EVSE nodes `evse` / `evse-2` -- positional slots no panel publishes --
    which made this look like an ordering problem, needing a rule to reconstruct
    firmware's enumeration and needing SPAN to confirm that rule. There was no
    ordering problem; there was an unfaithful fixture.

    A device with no serial keeps its v1.0 device id. Inventing an ordinal is the
    thing this function exists to avoid, and an unkeyable EVSE is better left
    obviously distinct than quietly merged with another.
    """
    return {device: (text(device, NODE_INFO, PROP_SERIAL_NUMBER) or device.device_id) for device in evse_devices}
