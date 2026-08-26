"""BESS, PV and EVSE mapping from the v1.0 tree."""

from __future__ import annotations

from collections.abc import Mapping
import json

import pytest

from ebus_sdk.homie import DiscoveredDevice

from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api_schema_1.devices import (
    build_mid,
    build_battery,
    build_evse,
    build_pv,
    connection_status_for,
    feed_circuit_ids,
)

_TREE = parent_child_tree()

SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"


def _device(device_id: str) -> DiscoveredDevice:
    return device_from_topics(device_id, _TREE[device_id])


def _circuits() -> list[DiscoveredDevice]:
    return [_device(SOLAR_CIRCUIT), _device("0ab966b95f92a6a51ec548485aa85f54")]


BESS_POWER_TOPIC = "meter/active-power"
BESS_COMMS_TOPIC = "status/communication-state"


def _published(device_id: str, topic: str) -> str:
    """What the capture publishes on this topic, or fail saying it does not.

    Every expectation below is computed from this rather than written as a
    literal, so a test cannot keep passing against a fixture that stopped
    carrying the value it is about.
    """
    value = _TREE[device_id].get(topic)
    assert value is not None, f"{device_id} publishes no {topic} in the capture"
    return value


def _bess_with(overrides: Mapping[str, str | None]) -> DiscoveredDevice:
    """The captured BESS with topics rewritten, or removed where the value is `None`.

    Removal is the point of the `None` case: a panel that stops publishing a
    property retains nothing, which is a different event from publishing `""`
    and has to produce a different answer.
    """
    topics = dict(_TREE["bess"])
    for topic, value in overrides.items():
        if value is None:
            topics.pop(topic, None)
        else:
            topics[topic] = value
    return device_from_topics("bess", topics)


def _without(device_id: str, *topics: str) -> DiscoveredDevice:
    """The captured device with these topics unpublished.

    The counterpart of `_published`: identity values now arrive valued in the
    capture, so proving a consumer distinguishes "not published" from "published
    blank" needs the absence built rather than found.
    """
    remaining = {topic: value for topic, value in _TREE[device_id].items() if topic not in topics}
    return device_from_topics(device_id, remaining)


def _bess_without_node(node_id: str) -> DiscoveredDevice:
    """The captured BESS with one capability node gone from its `$description`.

    The third shape of absence, and the one a fixture edit alone cannot reach:
    hardware that never had the capability, as opposed to hardware that has it
    and is not reporting. The `$description` is the authoritative property set,
    so removing the node is what "this BESS has no meter" actually looks like.
    """
    description = json.loads(_TREE["bess"]["$description"])
    del description["nodes"][node_id]
    topics = {topic: value for topic, value in _TREE["bess"].items() if not topic.startswith(f"{node_id}/")}
    topics["$description"] = json.dumps(description)
    return device_from_topics("bess", topics)


# ---------------------------------------------------------------------------
# Topology — v1.0 states the relationship on the circuit, not the DER
# ---------------------------------------------------------------------------


def test_feed_relationships_are_read_off_the_circuits() -> None:
    feeds = feed_circuit_ids(_circuits())

    assert feeds == {"pv": SOLAR_CIRCUIT}


def test_connection_status_is_reported_by_the_owner_not_the_device() -> None:
    """The upstream lugs claim the BESS, and it is their view of that link that
    `battery.connected` reflects."""
    upstream = _device("lugs-upstream")

    assert upstream.get_property("connection", "fed-by-device-id") == "bess"
    assert connection_status_for("bess", [upstream]) == "OK"
    assert connection_status_for("pv", [upstream]) is None


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------


def test_battery_state_of_charge_and_energy() -> None:
    battery = build_battery(_device("bess"), [])

    # Historically misnamed and kept that way: soe_percentage holds the
    # percentage, soe_kwh the energy.
    assert battery.soe_percentage == pytest.approx(50.4104, rel=1e-4)
    assert battery.soe_kwh == pytest.approx(6.8054, rel=1e-4)
    assert battery.nameplate_capacity_kwh == 13.5
    assert battery.vendor_name == "Span"


def test_battery_identity_is_read_straight_through_without_a_swap() -> None:
    """The crossover is gone: the snapshot speaks v1.0's vocabulary directly.

    This asserted the opposite until 2026-08-10 -- `info/model` onto `product_name` and
    `info/part-number` onto `model` -- to hold each entity's displayed meaning still
    against flat, which puts the SKU in `bess/model`. It worked, and it permanently
    encoded flat's irregularity in the snapshot, so every reader had to be told why
    `battery.model` was not a model.

    Flat is the inconsistent side, not v1.0: it puts the SKU in `model` on the BESS and
    in `part-number` on the EVSE, for the same concept. v1.0 normalises all three. So the
    snapshot adopts v1.0's names and `schema_0` translates flat into them -- which also
    moves the change off the firmware migration, where a user meets it unplanned, and
    onto a library release we schedule.
    """
    bess = _device("bess")
    bess.update_property("info", "part-number", "1232100-00-E")

    battery = build_battery(bess, [])

    assert battery.model == "Example BESS"  # designation, from info/model
    assert battery.part_number == "1232100-00-E"  # SKU, from info/part-number


def test_battery_connected_comes_from_the_owner_not_the_bess() -> None:
    """The BESS publishes `status/communication-state`, which looks like the
    right property and is a different signal. The guide warns against
    conflating them."""
    bess = _device("bess")
    assert bess.get_property("status", "communication-state") == "OK"

    assert build_battery(bess, [_device("lugs-upstream")]).connected is True


def test_battery_connected_is_none_when_nothing_claims_it() -> None:
    """ "Nobody has said" is not the same as "not OK" — the latter would report a
    healthy battery as disconnected while the owner is still announcing."""
    assert build_battery(_device("bess"), []).connected is None


def test_a_degraded_link_is_not_connected() -> None:
    upstream = _device("lugs-upstream")
    upstream.update_property("connection", "fed-by-device-status", "DEGRADED")

    assert build_battery(_device("bess"), [upstream]).connected is False


def test_no_bess_yields_the_empty_battery_snapshot() -> None:
    battery = build_battery(None, [])

    assert battery.soe_percentage is None
    assert battery.connected is None


# ---------------------------------------------------------------------------
# Battery power — the sign is the whole content of these
# ---------------------------------------------------------------------------


def test_the_capture_is_a_charging_battery() -> None:
    """The premise of every sign assertion below, derived rather than assumed.

    A sign convention can only be tested against a known physical state, and
    "negative means charging" is the claim under test, so reading the state off
    the sign would be circular. The enclosure's four power flows balance instead
    -- ``pv + battery + grid + site == 0``, the node balance `power-flows` 0.3
    describes, in which every term is positive when power flows *into* the thing
    it names -- and solving that identity says which way the battery is going
    without appealing to any convention this library chose.

    In this capture 8500 W of PV meets 2653 W of site load and exports 2347 W;
    the 3500 W left over is going into the battery. So the battery is charging,
    and both the enclosure and the BESS publish that as a positive number.

    Were the capture ever retaken with the battery discharging, this fails first
    and says so, rather than the negation tests failing and reading as a mapper
    bug.
    """
    flows = {name: float(_published("example-40t-001", f"power-flows/{name}")) for name in ("pv", "battery", "grid", "site")}

    assert flows["pv"] + flows["battery"] + flows["grid"] + flows["site"] == pytest.approx(0.0, abs=1e-9)
    # PV alone exceeds the site load, so the surplus has nowhere to go but the
    # battery and the grid -- and the grid term is an export.
    assert -flows["pv"] > flows["site"]
    assert flows["grid"] > 0
    assert flows["battery"] > 0
    assert float(_published("bess", BESS_POWER_TOPIC)) > 0


def test_battery_power_is_the_negation_of_the_wire() -> None:
    """One negation, and the frame it lands in is the BESS device's own.

    Positive means power flowing *out of* the battery -- discharging -- which is
    what the eBus specification asks of a device's own meter, and deliberately
    NOT the into-the-device rule `SpanCircuitSnapshot.instant_power_w` follows.
    The wire inputs are in opposite frames, so the same single negation lands the
    two fields on opposite conventions. This test used to claim the circuit rule
    held here too; it does not, and the helper was renamed from
    `_charge_positive` to `_discharge_positive` to stop implying it.

    Asserted against the wire rather than against a constant, and the sign
    separately from the magnitude: dropping the negation keeps the magnitude and
    fails on the sign, which is the mistake worth catching.

    The direction was settled by measurement rather than by reading the catalog
    -- a producer in self-consumption with the grid at zero, PV and battery
    together meeting the load, leaves no room to argue which way the battery is
    going.

    The *wire* input flipped under the producer at `ebus-panel-sim` 0.6.0, which
    is why the asserted sign moved without the mapper changing: what an enclosure
    proxies for a battery it hosts is the enclosure's reading of that battery,
    positive while charging, and `power-flows/battery` in the same capture says
    the same thing about the same instant. The reference tree carried the earlier
    frame until it was recaptured, so this assertion used to read `> 0` on a
    capture the test above calls a charging battery -- the two contradicted each
    other, and only the fixture was wrong.
    """
    raw = float(_published("bess", BESS_POWER_TOPIC))

    battery = build_battery(_device("bess"), [])

    assert battery.power_w == -raw
    assert battery.power_w is not None and battery.power_w < 0


def test_battery_power_follows_a_republished_value() -> None:
    """Proof the value is read off the wire and not defaulted into place."""
    raw = float(_published("bess", BESS_POWER_TOPIC))
    discharging = -raw / 2

    battery = build_battery(_bess_with({BESS_POWER_TOPIC: str(discharging)}), [])

    # Charging became discharging, so the snapshot's sign flips with it.
    assert battery.power_w == -discharging
    assert battery.power_w is not None and battery.power_w > 0


def test_a_battery_at_rest_reports_zero_and_not_negative_zero() -> None:
    """`-0.0` compares equal to `0.0` and renders as "-0.0" beside it.

    `build_circuit` carries the same guard for the same reason; a negation added
    without it produces a reading that looks broken exactly when nothing is
    happening.
    """
    battery = build_battery(_bess_with({BESS_POWER_TOPIC: "0.0"}), [])

    assert battery.power_w == 0.0
    assert str(battery.power_w) == "0.0"


def test_an_unpublished_battery_power_is_none_rather_than_zero() -> None:
    """Zero is a reading — "the battery is idle" — and absence is not one."""
    assert build_battery(_bess_with({BESS_POWER_TOPIC: None}), []).power_w is None


def test_a_bess_with_no_meter_node_has_no_power() -> None:
    assert build_battery(_bess_without_node("meter"), []).power_w is None


def test_the_bess_meter_and_the_enclosure_flow_agree_about_direction() -> None:
    """The two properties describing this battery's power must not disagree.

    `panel.power_flow_battery` is the enclosure's own arbitrated figure and is
    passed through untouched; `battery.power_w` is the BESS's own meter and is
    negated. That is only coherent because the two properties are published in
    the *same* frame on the wire -- asserted here rather than assumed, because a
    consumer rendering both beside each other has to negate exactly one of them,
    and which one is a fact about the capture rather than a preference.
    """
    bess_meter = float(_published("bess", BESS_POWER_TOPIC))
    enclosure_flow = float(_published("example-40t-001", "power-flows/battery"))

    assert (bess_meter < 0) == (enclosure_flow < 0)


# ---------------------------------------------------------------------------
# Battery communication state
# ---------------------------------------------------------------------------


def test_communication_state_is_the_published_enum() -> None:
    """Kept as the published string: DEGRADED is neither OK nor LOST, so a bool
    would have to pick one, and `connected` is already the bool answer to the
    other question."""
    published = _published("bess", BESS_COMMS_TOPIC)

    assert build_battery(_device("bess"), []).communication_state == published


def test_communication_state_follows_a_republished_value() -> None:
    published = _published("bess", BESS_COMMS_TOPIC)
    declared = json.loads(_TREE["bess"]["$description"])["nodes"]["status"]["properties"]
    options = declared[BESS_COMMS_TOPIC.split("/", 1)[1]]["format"].split(",")
    other = next(option for option in options if option != published)

    assert build_battery(_bess_with({BESS_COMMS_TOPIC: other}), []).communication_state == other


def test_an_unpublished_communication_state_is_none() -> None:
    """`""` would read as a device reporting an empty answer; it reported nothing."""
    assert build_battery(_bess_with({BESS_COMMS_TOPIC: None}), []).communication_state is None


def test_a_bess_with_no_status_node_has_no_communication_state() -> None:
    assert build_battery(_bess_without_node("status"), []).communication_state is None


def test_communication_state_and_connected_are_independent() -> None:
    """The two link facts this task deliberately keeps apart.

    The BESS reports its own link `LOST` while the enclosure still claims it as
    `OK`: one is the device speaking about itself, the other the panel speaking
    about it, and a mapping that conflated them would make this impossible to
    express.
    """
    battery = build_battery(_bess_with({BESS_COMMS_TOPIC: "LOST"}), [_device("lugs-upstream")])

    assert battery.communication_state == "LOST"
    assert battery.connected is True


# ---------------------------------------------------------------------------
# PV
# ---------------------------------------------------------------------------


def test_pv_metadata_and_feed() -> None:
    pv = build_pv(_device("pv"), feed_circuit_ids(_circuits()), feed_statuses={})

    assert pv.vendor_name == "Enphase"
    assert pv.model == "IQ8PLUS-72-2-US"
    assert pv.nameplate_capacity_w == 10000.0
    assert pv.feed_circuit_id == SOLAR_CIRCUIT


def test_pv_relative_position_is_not_guessed() -> None:
    """Retired in v1.0 and only "derivable from connection records (when
    present)". The integration gates control entities on it, so a wrong value
    creates or removes a control."""
    assert build_pv(_device("pv"), {}, feed_statuses={}).relative_position is None


def test_no_pv_yields_the_empty_snapshot() -> None:
    assert build_pv(None, {}, feed_statuses={}).vendor_name is None


# ---------------------------------------------------------------------------
# EVSE
# ---------------------------------------------------------------------------


def test_evse_state_and_metadata() -> None:
    evse = build_evse(_device("evse"), {}, node_id="evse", feed_statuses={})

    assert evse.node_id == "evse"
    assert evse.status == "CHARGING"
    assert evse.lock_state == "LOCKED"
    assert evse.advertised_current_a == 32.0
    assert evse.vendor_name == "SPAN"
    assert evse.model == "SPAN Drive"
    assert evse.part_number == "SPN-DRV-001"
    assert evse.serial_number == "SIM-EVSE-example-40t-001"


def test_evse_without_a_feeding_circuit_reports_empty_not_none() -> None:
    """`feed_circuit_id` is non-optional on the dataclass, so an unclaimed EVSE
    gets the empty string rather than breaking construction."""
    assert build_evse(_device("evse"), {}, node_id="evse", feed_statuses={}).feed_circuit_id == ""


def test_the_mid_is_surfaced_as_its_own_device() -> None:
    """v1.0's islanding authority, exposed so a consumer can render it as hardware.

    The enclosure model puts `grid` on the MID rather than on the enclosure -- "the
    enclosure device itself does not publish them" -- so this is where islanding state,
    grid state and the grid-forming entity actually live.

    Identity is the published serial, per `devices/proxy.md`: a proxied device id is
    not stable across the proxy-to-native transition, so it cannot be what a consumer
    keys its registry on. `test_the_mid_falls_back_to_its_device_id_without_a_serial`
    covers the other branch.
    """
    mid = build_mid(_device("bess-mid"), {})

    assert mid is not None
    assert mid.islanding_state == _published("bess-mid", "grid/islanding-state")
    assert mid.grid_state == _published("bess-mid", "grid/grid-state")
    assert mid.grid_forming_entity == _published("bess-mid", "grid/grid-forming-entity")
    assert mid.vendor_name == _published("bess-mid", "info/vendor-name")
    assert mid.model == _published("bess-mid", "info/model")
    assert mid.serial_number == _published("bess-mid", "info/serial-number")
    assert mid.node_id == mid.serial_number


def test_the_mid_falls_back_to_its_device_id_without_a_serial() -> None:
    """A MID that publishes no serial still gets an identity, from the Homie device id.

    The fallback branch of the rule above. It was the capture's own state until the
    reference tree caught up with what the producer publishes, so it is written out
    rather than left to a fixture that happens not to carry a value.
    """
    mid = build_mid(_without("bess-mid", "info/serial-number"), {})

    assert mid is not None
    assert mid.serial_number is None
    assert mid.node_id == "bess-mid"


def test_a_panel_with_no_mid_reports_none_rather_than_an_empty_device() -> None:
    """Presence is `snapshot.mid is not None`, with nothing to infer.

    `has_bess` has to guess from `soe_percentage is not None` because the battery field
    is always present; its own docstring records that only that one field is reliable.
    A new optional device should not inherit that guessing game.
    """
    assert build_mid(None, {}) is None


def test_the_mid_carries_its_own_firmware_and_hardware_revision() -> None:
    """`info/firmware-version` and `info/hardware-version` reach the snapshot.

    r202633 documents both on the MID's `info` node, and a consumer has fields for
    them (`DeviceInfo(sw_version=..., hw_version=...)`). Until these were mapped the
    MID's device card showed a model and a serial and nothing else, beside a battery
    showing all three — the battery's identical property having been mapped from the
    start. Found by valuing them in the simulator, which had never published them
    either, so nothing downstream had ever been asked for them.

    `software_version` rather than `firmware_version`: the sub-devices share a
    spelling because a consumer builds all of them the same way. Only the enclosure
    calls it `firmware_version`.

    Read straight off the capture now that it carries what the producer publishes;
    it used to inject the two values, which asked whether the mapper could read a
    property this tree did not have.
    """
    mid = build_mid(_device("bess-mid"), {})

    assert mid is not None
    assert mid.software_version == _published("bess-mid", "info/firmware-version")
    assert mid.hardware_version == _published("bess-mid", "info/hardware-version")


def test_the_pv_carries_its_firmware_version() -> None:
    """The other half of the same gap: `info/firmware-version` on the PV.

    Documented by r202633, published by the simulator, and dropped on the floor until
    now. Unlike the MID there is no `hardware-version` to carry — the topic reference
    documents five properties on the PV and that is not one of them.
    """
    pv = build_pv(_device("pv"), {}, feed_statuses={})

    assert pv.software_version == _published("pv", "info/firmware-version")


def test_a_device_publishing_no_revision_reports_none_rather_than_empty_string() -> None:
    """Absent stays absent, so a consumer can tell "not published" from "published blank".

    `DeviceInfo` renders an empty string as a present-but-blank row; `None` omits the
    row. The capture publishes all three, so the absence is built by unpublishing
    them, which is what a panel whose firmware omits them actually looks like.
    """
    mid = build_mid(_without("bess-mid", "info/firmware-version", "info/hardware-version"), {})

    assert mid is not None
    assert mid.software_version is None
    assert mid.hardware_version is None
    assert build_pv(_without("pv", "info/firmware-version"), {}, feed_statuses={}).software_version is None
