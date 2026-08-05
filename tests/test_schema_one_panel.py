"""Panel-level mapping from the v1.0 tree.

Driven from `fixtures/parent_child_tree.json`, captured off a real `panel_sim`
parent/child tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api_schema_1.panel import PanelFields, find_lugs, panel_size_from_tabs

_TREE = json.loads((Path(__file__).parent / "fixtures" / "parent_child_tree.json").read_text(encoding="utf-8"))

PANEL = "example-40t-001"


def _device(device_id: str) -> DiscoveredDevice:
    topics = _TREE[device_id]
    device = DiscoveredDevice(device_id, "ebus")
    device.update_description(topics["$description"])
    device.update_state(topics["$state"])
    for topic, value in topics.items():
        if topic.startswith("$"):
            continue
        node, _, prop = topic.partition("/")
        if prop:
            device.update_property(node, prop, value)
    return device


@pytest.fixture(name="fields")
def _fields() -> PanelFields:
    return PanelFields(
        panel=_device(PANEL),
        upstream_lugs=_device("lugs-upstream"),
        downstream_lugs=_device("lugs-downstream"),
        mid=_device("bess-mid"),
    )


def test_identity(fields: PanelFields) -> None:
    assert fields.serial_number == "example-40t-001"
    assert fields.firmware_version == "example/v0.1.0"


def test_hardware_status(fields: PanelFields) -> None:
    assert fields.main_relay_state == "CLOSED"
    assert fields.door_state == "CLOSED"
    assert fields.eth0_link is True
    assert fields.wlan_link is True
    assert fields.main_breaker_rating_a == 200


def test_wwan_link_reports_cloud_reachability(fields: PanelFields) -> None:
    """v1 exposed a WWAN radio link and v2 has no such property, so the flat
    adapter reported cloud reachability. Kept identical so the entity does not
    change meaning between adapters."""
    assert fields.vendor_cloud == "CONNECTED"
    assert fields.wwan_link is True


def test_voltages_come_from_the_panel_meter(fields: PanelFields) -> None:
    assert fields.l1_voltage == 120.0
    assert fields.l2_voltage == 120.0


def test_power_flows(fields: PanelFields) -> None:
    assert fields.power_flow_pv == 8500.0
    assert fields.power_flow_battery == -3500.0
    assert fields.power_flow_grid == -2347.0
    assert fields.power_flow_site == 2653.0


# ---------------------------------------------------------------------------
# Lugs — the direction rule that is the opposite of a circuit's
# ---------------------------------------------------------------------------


def test_grid_power_is_not_negated(fields: PanelFields) -> None:
    """The enclosure frame already reports import-positive at the lugs, which
    is what consumption means there. Applying the circuit rule would invert
    every grid figure while leaving it entirely plausible."""
    raw = _device("lugs-upstream").get_property("meter", "active-power")
    assert raw == "-5847.0"

    assert fields.instant_grid_power_w == -5847.0


def test_main_meter_energy_maps_imported_to_consumed(fields: PanelFields) -> None:
    """Opposite of a circuit: the panel imports from the grid, so imported
    energy is what the house consumed."""
    upstream = _device("lugs-upstream")
    assert upstream.get_property("meter", "imported-energy") == "44.21666666666666"

    assert fields.main_meter_energy_consumed_wh == pytest.approx(44.2166, rel=1e-4)
    assert fields.main_meter_energy_produced_wh == pytest.approx(141.6666, rel=1e-4)


def test_per_phase_currents(fields: PanelFields) -> None:
    """Lugs expose `current-a`/`current-b`; circuits expose a single `current`.
    Same capability type, different property set."""
    assert fields.upstream_l1_current_a == pytest.approx(46.4666, rel=1e-4)
    assert fields.upstream_l2_current_a == pytest.approx(46.4749, rel=1e-4)
    assert fields.downstream_l1_current_a == pytest.approx(46.4666, rel=1e-4)


def test_feedthrough_comes_from_the_downstream_lugs(fields: PanelFields) -> None:
    assert fields.feedthrough_power_w == -5847.0
    assert fields.feedthrough_energy_consumed_wh == pytest.approx(44.2166, rel=1e-4)


def test_lugs_are_found_by_declared_direction_not_device_id() -> None:
    """Device ids in the reference tree are the simulator's naming; direction
    is what the schema defines."""
    devices = [_device("lugs-downstream"), _device("lugs-upstream")]

    assert find_lugs(devices, upstream=True).device_id == "lugs-upstream"
    assert find_lugs(devices, upstream=False).device_id == "lugs-downstream"


def test_missing_lugs_yield_zeros_not_errors() -> None:
    """A panel without lugs devices must still produce a snapshot."""
    fields = PanelFields(panel=_device(PANEL), upstream_lugs=None, downstream_lugs=None, mid=None)

    assert fields.instant_grid_power_w == 0.0
    assert fields.upstream_l1_current_a is None
    assert fields.grid_state is None


# ---------------------------------------------------------------------------
# Moved and retired
# ---------------------------------------------------------------------------


def test_grid_state_comes_from_the_mid(fields: PanelFields) -> None:
    """It moved off the panel to the device where islanding is decided."""
    assert fields.grid_state == "UP"


def test_retired_fields_are_none_rather_than_substituted(fields: PanelFields) -> None:
    """`dominant-power-source` split into grid-forming-entity plus
    asserted-islanding-state, and `grid-islandable` was removed outright.
    Substituting either would be a silent product decision."""
    assert fields.dominant_power_source is None
    assert fields.grid_islandable is None


# ---------------------------------------------------------------------------
# Panel size — the gap
# ---------------------------------------------------------------------------


def test_panel_size_is_a_lower_bound_from_occupied_spaces() -> None:
    """v1.0 publishes no panel size anywhere: `info/spaces` is a plain string
    with no format, the panel device has no size property, and the migration
    guide does not map one. The highest occupied space is the best available
    answer and it undercounts."""
    assert panel_size_from_tabs([1, 3, 36, 38]) == 38
    assert panel_size_from_tabs([]) == 0


def test_panel_size_undercounts_a_sparsely_populated_panel() -> None:
    """The failure this documents: a 40-space panel whose highest occupied slot
    is 36 reports 36, so anything enumerating unoccupied slots is not
    reproducible from the wire."""
    assert panel_size_from_tabs([1, 2, 36]) == 36
