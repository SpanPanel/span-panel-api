"""The synthetic tree builders, and the adapter's replay of what they build.

Each builder stands for a shape the reference payloads do not carry. These tests
pin two things: what each builder declares and values, which is the contract a
test written against it relies on, and that every tree the builders make passes
through the adapter's real discovery path without raising. What the adapter
makes of each shape belongs to the tests of that behaviour, not to these.
"""

from __future__ import annotations

import json

import pytest

from reference_payloads.schema_one import RetainedTopicTree, replay
from reference_payloads.synthetic_trees import (
    PANEL_ID,
    battery,
    circuit_fed_battery,
    discovered,
    evse,
    hosted_circuit,
    lugs,
    panel,
    panel_with_positions,
    shared_pair,
    space_less_meter,
    tree,
    without_values,
)

_CIRCUIT_TYPE = "energy.ebus.device.circuit"


def _description(topics: RetainedTopicTree, device_id: str) -> dict[str, object]:
    description: object = json.loads(topics[device_id]["$description"])
    assert isinstance(description, dict)
    return description


def _declared(topics: RetainedTopicTree, device_id: str) -> set[str]:
    """Every `node/property` the device's description declares."""
    nodes = _description(topics, device_id)["nodes"]
    assert isinstance(nodes, dict)
    return {f"{node}/{prop}" for node, body in nodes.items() for prop in body["properties"]}


def _valued(topics: RetainedTopicTree, device_id: str) -> set[str]:
    return {topic for topic in topics[device_id] if not topic.startswith("$")}


def _circuit_ids(topics: RetainedTopicTree) -> set[str]:
    return {device_id for device_id in topics if _description(topics, device_id)["type"] == _CIRCUIT_TYPE}


_TREES: dict[str, RetainedTopicTree] = {
    "panel alone": tree(panel()),
    "meter without a breaker space": tree(panel(), space_less_meter()),
    "unvalued meter without a breaker space": tree(panel(), without_values(space_less_meter())),
    "occupied positions": panel_with_positions("MAIN_32", (1, 2, 7)),
    "occupied positions, unsized model": panel_with_positions("UNKNOWN", (9, 10)),
    "declared feeds role": tree(panel(), hosted_circuit("c-1", (1,), feeds_role="LOADS")),
    "out-of-set feeds role": tree(panel(), hosted_circuit("c-1", (1,), feeds_role="NOT_A_ROLE")),
    "shared meter and relay": tree(panel(), *shared_pair()),
    "panel and circuit readings": tree(
        panel(busbar_current_a=12.5, frequency_hz=60.02),
        lugs("UPSTREAM", overcurrent_protection_a=125),
        lugs("DOWNSTREAM"),
        hosted_circuit("c-1", (1, 3), nominal_voltage_v=240.0, protection_functions=("OVERCURRENT", "ARC_FAULT")),
    ),
    "settable charger lock": tree(panel(), evse()),
    "battery fed by a circuit": tree(panel(), *circuit_fed_battery()),
}


@pytest.mark.parametrize("topics", list(_TREES.values()), ids=list(_TREES))
def test_every_tree_replays_through_the_adapter(topics: RetainedTopicTree) -> None:
    adapter = replay(topics, PANEL_ID)

    assert adapter.is_ready()
    snapshot = adapter.build_snapshot()
    adapter.build_field_metadata()
    assert snapshot.serial_number == PANEL_ID
    assert _circuit_ids(topics) <= set(snapshot.circuits)


@pytest.mark.spec_only
def test_a_solar_role_circuit_replays_through_the_adapter() -> None:
    """The catalog's `connection/feeds-role` `SOLAR`, which no reference capture
    publishes on a circuit."""
    topics = tree(panel(), hosted_circuit("c-1", (1, 3), feeds_role="SOLAR"))

    adapter = replay(topics, PANEL_ID)

    assert adapter.is_ready()
    assert "c-1" in adapter.build_snapshot().circuits


def test_a_meter_without_a_breaker_space_declares_only_a_meter() -> None:
    topics = tree(panel(), space_less_meter("meter-a"))

    assert _description(topics, "meter-a")["type"] == _CIRCUIT_TYPE
    assert _declared(topics, "meter-a") == {
        "meter/active-power",
        "meter/current",
        "meter/imported-energy",
        "meter/exported-energy",
    }
    assert _valued(topics, "meter-a") == _declared(topics, "meter-a")


def test_without_values_keeps_the_declaration_and_drops_every_value() -> None:
    topics = tree(panel(), without_values(space_less_meter("meter-a")))

    assert _declared(topics, "meter-a") == _declared(tree(panel(), space_less_meter("meter-a")), "meter-a")
    assert _valued(topics, "meter-a") == set()


def test_without_values_drops_only_the_properties_it_names() -> None:
    topics = tree(panel(), without_values(space_less_meter("meter-a"), "meter/active-power"))

    assert _valued(topics, "meter-a") == {"meter/current", "meter/imported-energy", "meter/exported-energy"}


def test_without_values_refuses_a_property_the_device_does_not_declare() -> None:
    with pytest.raises(ValueError, match="info/spaces"):
        without_values(space_less_meter(), "info/spaces")


def test_a_hosted_circuit_publishes_its_spaces_and_a_controllable_relay() -> None:
    topics = tree(panel(), hosted_circuit("c-1", (5, 7)))

    assert topics["c-1"]["info/spaces"] == "5,7"
    assert topics["c-1"]["breaker/poles"] == "2"
    assert topics["c-1"]["switch/relay-controllable"] == "true"
    nodes = _description(topics, "c-1")["nodes"]
    assert isinstance(nodes, dict)
    assert nodes["switch"]["properties"]["relay"]["settable"] is True
    assert "connection" not in nodes


def test_a_declared_feeds_role_is_valued_as_given() -> None:
    topics = tree(panel(), hosted_circuit("c-1", (1,), feeds_role="NOT_A_ROLE"))

    assert topics["c-1"]["connection/feeds-role"] == "NOT_A_ROLE"


def test_a_shared_pair_names_each_other_on_one_space() -> None:
    first, second = shared_pair("c-1", "c-2", space=4)
    topics = tree(panel(), first, second)

    for member, peer in (("c-1", "c-2"), ("c-2", "c-1")):
        assert topics[member]["meter/shared-with-device-ids"] == peer
        assert topics[member]["switch/shared-with-device-ids"] == peer
        assert topics[member]["info/spaces"] == "4"
    meter = ("meter/active-power", "meter/current", "meter/imported-energy", "meter/exported-energy")
    assert [topics["c-1"][topic] for topic in meter] == [topics["c-2"][topic] for topic in meter]


def test_the_readings_are_declared_only_where_given() -> None:
    plain = tree(panel(), lugs("UPSTREAM"), hosted_circuit("c-1", (1,)))
    read = tree(
        panel(busbar_current_a=12.5, frequency_hz=60.02),
        lugs("UPSTREAM", overcurrent_protection_a=125),
        hosted_circuit("c-1", (1,), nominal_voltage_v=120.0, protection_functions=("OVERCURRENT", "GROUND_FAULT")),
    )
    readings = {
        PANEL_ID: {"meter/busbar-current", "meter/frequency"},
        "lugs-upstream": {"connection/overcurrent-protection"},
        "c-1": {"info/nominal-voltage", "breaker/protection-functions"},
    }

    for device_id, paths in readings.items():
        assert not paths & _declared(plain, device_id)
        assert paths <= _declared(read, device_id) & _valued(read, device_id)
    assert read[PANEL_ID]["meter/frequency"] == "60.02"
    assert read["lugs-upstream"]["connection/overcurrent-protection"] == "125"
    assert read["c-1"]["breaker/protection-functions"] == "OVERCURRENT,GROUND_FAULT"


def test_the_panel_advertises_the_model_it_reports_unless_told_otherwise() -> None:
    def model_format(device_id: str, topics: RetainedTopicTree) -> object:
        nodes = _description(topics, device_id)["nodes"]
        assert isinstance(nodes, dict)
        return nodes["info"]["properties"]["model"]["format"]

    assert model_format(PANEL_ID, tree(panel(model="MAIN_32"))) == "MAIN_32"
    advertised = tree(panel(model="UNKNOWN", advertised_models=("UNKNOWN", "MAIN_32")))
    assert model_format(PANEL_ID, advertised) == "UNKNOWN,MAIN_32"
    assert advertised[PANEL_ID]["info/model"] == "UNKNOWN"


def test_panel_with_positions_hosts_one_circuit_per_position() -> None:
    topics = panel_with_positions("MAIN_32", (2, 9))

    assert topics[PANEL_ID]["info/model"] == "MAIN_32"
    assert {device_id: topics[device_id]["info/spaces"] for device_id in _circuit_ids(topics)} == {"c-2": "2", "c-9": "9"}


def test_the_charger_lock_is_settable_unless_told_otherwise() -> None:
    def lock_declaration(topics: RetainedTopicTree) -> dict[str, object]:
        nodes = _description(topics, "evse-a")["nodes"]
        assert isinstance(nodes, dict)
        declaration: dict[str, object] = nodes["switch"]["properties"]["lock-state"]
        return declaration

    assert lock_declaration(tree(panel(), evse("evse-a")))["settable"] is True
    assert "settable" not in lock_declaration(tree(panel(), evse("evse-a", lock_settable=False)))


def test_a_circuit_fed_battery_is_named_by_its_circuit() -> None:
    circuit, fed = circuit_fed_battery("c-1", "battery-a", status="LOST")
    topics = tree(panel(), circuit, fed)

    assert topics["c-1"]["connection/feeds-device-id"] == "battery-a"
    assert topics["c-1"]["connection/feeds-device-type"] == _description(topics, "battery-a")["type"]
    assert topics["c-1"]["connection/feeds-device-status"] == "LOST"


def test_the_tree_links_every_device_to_its_parent() -> None:
    standalone = battery("battery-a")
    child = without_values(space_less_meter("meter-a"))
    topics = tree(panel(), standalone, hosted_circuit("c-1", (1,)), child)

    assert _description(topics, PANEL_ID)["children"] == ["battery-a", "c-1", "meter-a"]
    assert "parent" not in _description(topics, PANEL_ID)
    for device_id in ("battery-a", "c-1", "meter-a"):
        assert _description(topics, device_id)["parent"] == PANEL_ID
        assert _description(topics, device_id)["root"] == PANEL_ID


def test_the_tree_refuses_a_device_id_twice() -> None:
    with pytest.raises(ValueError, match="c-1"):
        tree(panel(), hosted_circuit("c-1", (1,)), hosted_circuit("c-1", (2,)))


def test_a_device_rebuilt_alone_reads_its_values() -> None:
    device = discovered(hosted_circuit("c-1", (3,), name="Workshop"))

    assert device.device_id == "c-1"
    assert device.get_property("info", "name") == "Workshop"
    assert device.get_property("info", "spaces") == "3"
