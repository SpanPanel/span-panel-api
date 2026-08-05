"""Panel-level mapping from the v1.0 tree.

Driven from `fixtures/parent_child_tree.json`, captured off a real `panel_sim`
parent/child tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api_schema_1.panel import (
    PanelFields,
    build_unmapped_tabs,
    find_lugs,
    panel_model_drift,
    panel_size_from_model,
)

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
# Panel size — from the model, because nothing else states it
# ---------------------------------------------------------------------------


def test_panel_size_comes_from_the_model() -> None:
    """`info/model` is the only place v1.0 states the panel's size, and it is a
    closed enum the panel itself advertises via Homie `$format`."""
    assert panel_size_from_model("MAIN_40") == 40
    assert panel_size_from_model("MAIN_16") == 16
    assert panel_size_from_model("MLO_48") == 48


def test_panel_size_reads_the_model_off_the_fixture() -> None:
    panel = _device(PANEL)

    assert panel.get_property("info", "model") == "MAIN_40"
    assert panel_size_from_model(panel.get_property("info", "model")) == 40


def test_an_unknown_model_yields_no_size_rather_than_a_guess() -> None:
    """Inventing a size is worse than reporting none: a wrong total fabricates
    unmapped positions that do not exist, or hides real ones."""
    assert panel_size_from_model("MAIN_99") == 0
    assert panel_size_from_model("") == 0


def test_the_panel_advertises_every_model_we_can_size(caplog: pytest.LogCaptureFixture) -> None:
    """The panel publishes the valid model set as `$format`, but neither the
    schema nor the SDK states the sizes — that half is ours. This is how a model
    we cannot size shows up at connect time instead of as missing positions."""
    definition = _device(PANEL).get_node_properties("info")["model"]
    assert definition["format"] == "MAIN_16,MLO_24,MAIN_32,MAIN_40,MLO_48"

    assert panel_model_drift(_device(PANEL)) == ()


def test_a_model_we_cannot_size_is_reported_as_drift() -> None:
    panel = _device(PANEL)
    description = json.loads(_TREE[PANEL]["$description"])
    description["nodes"]["info"]["properties"]["model"]["format"] = "MAIN_40,MAIN_64"
    panel.update_description(json.dumps(description))

    assert panel_model_drift(panel) == ("MAIN_64",)


# ---------------------------------------------------------------------------
# Unmapped positions — reproducible under v1.0 only because the model gives a total
# ---------------------------------------------------------------------------


def test_unmapped_tabs_fill_every_unoccupied_position() -> None:
    unmapped = build_unmapped_tabs(panel_size=6, occupied={1, 3})

    assert sorted(unmapped) == [
        "unmapped_tab_2",
        "unmapped_tab_4",
        "unmapped_tab_5",
        "unmapped_tab_6",
    ]
    assert unmapped["unmapped_tab_2"].tabs == [2]
    assert unmapped["unmapped_tab_2"].instant_power_w == 0.0
    assert unmapped["unmapped_tab_2"].name == "Unmapped Tab 2"


def test_the_unmapped_id_format_matches_the_flat_adapter() -> None:
    """The integration builds entity ids from this — `sensor.span_panel_
    unmapped_tab_32_power` — so a rename would strand existing entities."""
    unmapped = build_unmapped_tabs(panel_size=32, occupied=set(range(1, 32)))

    assert list(unmapped) == ["unmapped_tab_32"]


def test_a_fully_occupied_panel_has_no_unmapped_positions() -> None:
    assert build_unmapped_tabs(panel_size=4, occupied={1, 2, 3, 4}) == {}


def test_an_unsizable_panel_yields_no_unmapped_positions() -> None:
    """Better nothing than a fabricated set: size 0 is what an unknown model
    reports, and inventing positions would create phantom entities."""
    assert build_unmapped_tabs(panel_size=0, occupied={1}) == {}
