"""Panel-level mapping from the v1.0 tree.

Driven from `fixtures/parent_child_tree.json`, captured off a real `panel_sim`
parent/child tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api_schema_1.const import NODE_GRID, TYPE_BESS, TYPE_PV
from span_panel_api_schema_1.panel import (
    PanelFields,
    build_unmapped_tabs,
    find_lugs,
    panel_model_drift,
    panel_size_from_model,
    resolve_dominant_power_source,
    resolve_grid_forming_device_name,
    resolve_grid_islandable,
    resolve_islanding_state,
    resolve_run_config,
)

_TREE = json.loads((Path(__file__).parent / "fixtures" / "parent_child_tree.json").read_text(encoding="utf-8"))

PANEL = "example-40t-001"
MID = "bess-mid"


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
    """It moved off the panel to the device where islanding is decided.

    And it comes from `islanding-state`, keeping the flat schema's
    ON_GRID/OFF_GRID vocabulary.
    """
    assert fields.grid_state == "ON_GRID"


def test_grid_state_is_not_the_mids_utility_health_signal() -> None:
    """The MID publishes two grid properties and only one of them is this.

    `grid/grid-state` answers whether the utility supply is UP, DOWN or
    DEGRADED — new in v1.0, with no flat equivalent. `grid/islanding-state`
    answers ON_GRID/OFF_GRID, which is what the flat schema's `grid_state`
    meant and what every existing template compares against. Taking the
    similarly-named one keeps the entity's id and history while silently
    changing its vocabulary, so this pins the distinction rather than trusting
    it.
    """
    mid = _device(MID)
    assert mid.get_property(NODE_GRID, "grid-state") == "UP"
    assert mid.get_property(NODE_GRID, "islanding-state") == "ON_GRID"

    fields = PanelFields(panel=_device(PANEL), upstream_lugs=None, downstream_lugs=None, mid=mid)

    assert fields.grid_state == "ON_GRID"


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


# ---------------------------------------------------------------------------
# Grid answers: read, not derived — the 2026-08-10 decision
# ---------------------------------------------------------------------------


def _synthetic(device_id: str, state: str = "ready", **props: str) -> DiscoveredDevice:
    """A device built from nothing, for the cases no capture contains.

    The tracked producer models a BESS as one device with a MID child and no
    `inverter`, so `grid-forming/capable` has nowhere to live in any fixture. That is
    recorded in `_NOT_EXERCISED_BY_SIMULATOR`; this is what stops the mapping being
    merely untested as well as unexercised.
    """
    device = DiscoveredDevice(device_id, "ebus")
    device.update_state(state)
    for path, value in props.items():
        # `node__prop_name` -> node/prop-name, since the wire spells both with hyphens
        # and a Python keyword cannot.
        node, _, prop = path.partition("__")
        device.update_property(node.replace("_", "-"), prop.replace("_", "-"), value)
    return device


def test_islanding_is_sensed_when_the_mid_is_ready() -> None:
    """Tier 1. The MID is the islanding authority, so its answer wins outright."""
    mid = _synthetic("mid", grid__islanding_state="OFF_GRID")
    panel = _synthetic(PANEL, shed__asserted_islanding_state="ON_GRID")

    assert resolve_islanding_state(mid, panel) == "OFF_GRID", "a ready MID outranks the user's assertion"


def test_a_stale_mid_falls_back_to_the_users_assertion() -> None:
    """Tier 2, and the case the assertion control exists for.

    When comms to the BESS or MID are lost and the grid returns, the user asserts the
    grid is up so the BESS stops discharging. Declining to read it would wire the
    control and then ignore it at exactly the moment it matters.
    """
    mid = _synthetic("mid", state="lost", grid__islanding_state="OFF_GRID")
    panel = _synthetic(PANEL, shed__asserted_islanding_state="ON_GRID")

    assert resolve_islanding_state(mid, panel) == "ON_GRID"


def test_a_stale_mid_with_no_assertion_is_unknown_not_guessed() -> None:
    """Tier 4. `NONE` is the assertion's idle value, not an answer."""
    mid = _synthetic("mid", state="lost", grid__islanding_state="ON_GRID")
    panel = _synthetic(PANEL, shed__asserted_islanding_state="NONE")

    assert resolve_islanding_state(mid, panel) is None


def test_no_mid_reads_grid_power_and_never_asserts_off_grid() -> None:
    """Tier 3, and the error worth keeping a test on.

    An earlier draft reasoned that no MID means no islanding authority means on-grid.
    A missing MID means *SPAN* is not the authority and says nothing about whether the
    site is islanded — a generator-fed island is the counterexample. Grid power flowing
    is positive evidence of being on-grid; its absence is not evidence of the opposite.
    """
    assert resolve_islanding_state(None, _synthetic(PANEL, power_flows__grid="2400.0")) == "ON_GRID"
    assert resolve_islanding_state(None, _synthetic(PANEL, power_flows__grid="0.0")) is None
    assert resolve_islanding_state(None, _synthetic(PANEL)) is None


def test_run_config_names_the_forming_device_rather_than_guessing_it() -> None:
    """The part that gets better than flat.

    Flat guessed `PANEL_BACKUP` versus `PANEL_OFF_GRID` from `dominant-power-source`.
    v1.0 names the forming device, and its class is recoverable from the tree.
    """
    types = {"bess-1": TYPE_BESS, "gen-1": "energy.ebus.device.generator"}

    on_grid = _synthetic("mid", grid__grid_forming_entity="GRID")
    backup = _synthetic("mid", grid__grid_forming_entity="bess-1")
    off_grid = _synthetic("mid", grid__grid_forming_entity="gen-1")

    assert resolve_run_config(on_grid, "ON_GRID", types) == "PANEL_ON_GRID"
    assert resolve_run_config(backup, "OFF_GRID", types) == "PANEL_BACKUP"
    assert resolve_run_config(off_grid, "OFF_GRID", types) == "PANEL_OFF_GRID"


def test_run_config_degrades_honestly_when_the_forming_entity_is_unusable() -> None:
    """Unresolvable is not an excuse to pick one.

    Without knowing what is forming the grid, off-grid cannot be split into backup
    versus off-grid, so it reports unknown. On-grid still answers, because the islanding
    tier already established it.
    """
    unresolvable = _synthetic("mid", grid__grid_forming_entity="a-device-not-in-this-tree")

    assert resolve_run_config(unresolvable, "OFF_GRID", {}) == "UNKNOWN"
    assert resolve_run_config(unresolvable, "ON_GRID", {}) == "PANEL_ON_GRID"
    assert resolve_run_config(None, None, {}) == "UNKNOWN"


def test_grid_islandable_is_the_disjunction_over_inverters() -> None:
    """Flat's `grid_islandable`, relocated to where the capability actually lives.

    A panel does not island, its DER does; flat expressed a property of the DER as a
    property of the enclosure. BESS model 0.14 puts grid-forming on the `inverter`
    child, so the panel-level answer is "can any inverter here form a grid".
    """
    capable = _synthetic("inv-1", grid_forming__capable="true")
    incapable = _synthetic("inv-2", grid_forming__capable="false")

    assert resolve_grid_islandable([capable]) is True
    assert resolve_grid_islandable([incapable]) is False
    assert resolve_grid_islandable([incapable, capable]) is True, "one grid-forming inverter is enough"


def test_an_inverter_that_says_nothing_is_unknown_not_incapable() -> None:
    """`None`, not `False`. Absence means unknown.

    Reporting "cannot island" for a panel that has not told us turns a gap into a claim,
    and the integration declines to create the entity on `None` — an absent entity is
    the honest outcome, a confidently wrong one is not.
    """
    assert resolve_grid_islandable([_synthetic("inv-1")]) is None
    assert resolve_grid_islandable([]) is None


def test_dominant_power_source_dereferences_the_forming_device_to_a_class() -> None:
    """The entity keeps flat's closed enum, so nothing comparing to `BATTERY` breaks.

    The integration's sensor for this field is already named `grid_forming_entity`, so
    v1.0's property is the same concept it has always shown. Only the encoding changed:
    flat published a source class, v1.0 names the device. Dereferencing recovers the
    class from the tree.
    """
    types = {"bess-1": TYPE_BESS, "pv-1": TYPE_PV}

    assert resolve_dominant_power_source(_synthetic("mid", grid__grid_forming_entity="GRID"), types) == "GRID"
    assert resolve_dominant_power_source(_synthetic("mid", grid__grid_forming_entity="bess-1"), types) == "BATTERY"
    assert resolve_dominant_power_source(_synthetic("mid", grid__grid_forming_entity="pv-1"), types) == "PV"


def test_an_unresolvable_forming_entity_cannot_escape_as_a_raw_id() -> None:
    """`UNKNOWN` is in flat's enum already, so the value space stays closed.

    A device id naming something outside this tree, or a class with no mapping, must not
    reach an entity as an opaque string — that is exactly the silent break the decision
    to dereference exists to avoid. The device-type registry instructs consumers to
    tolerate unknown `$type` values; this is what tolerating one looks like.
    """
    stranger = _synthetic("mid", grid__grid_forming_entity="some-device-not-in-this-tree")
    unmapped = _synthetic("mid", grid__grid_forming_entity="wh-1")

    assert resolve_dominant_power_source(stranger, {}) == "UNKNOWN"
    assert resolve_dominant_power_source(unmapped, {"wh-1": "energy.ebus.device.water-heater"}) == "UNKNOWN"
    assert resolve_dominant_power_source(None, {}) is None


def test_the_forming_device_is_named_readably_not_by_wire_id() -> None:
    """A Homie device id is not a Home Assistant device id.

    `sim-40t-001-SIM-BESS-40T-001` on a dashboard is worse than nothing. The device's own
    `$description.name` is what a person recognises, and it is the precision v1.0 adds
    over flat — *which* battery, not merely that a battery is forming.
    """
    names = {"bess-1": "Battery", "pv-1": "Solar"}

    assert resolve_grid_forming_device_name(_synthetic("mid", grid__grid_forming_entity="bess-1"), names) == "Battery"
    # The grid is not a device, so there is nothing to name.
    assert resolve_grid_forming_device_name(_synthetic("mid", grid__grid_forming_entity="GRID"), names) is None
    # Unresolvable: the raw id stays on `grid_forming_entity` for anyone who needs it.
    assert resolve_grid_forming_device_name(_synthetic("mid", grid__grid_forming_entity="ghost"), names) is None
