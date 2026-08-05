"""BESS, PV and EVSE mapping from the v1.0 tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api_schema_1.devices import (
    build_battery,
    build_evse,
    build_pv,
    connection_status_for,
    feed_circuit_ids,
)

_TREE = json.loads((Path(__file__).parent / "fixtures" / "parent_child_tree.json").read_text(encoding="utf-8"))

SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"


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


def _circuits() -> list[DiscoveredDevice]:
    return [_device(SOLAR_CIRCUIT), _device("0ab966b95f92a6a51ec548485aa85f54")]


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


def test_battery_model_and_product_name_are_swapped_not_copied() -> None:
    """Flat `bess/model` was the SKU; v1.0 `info/model` is the designation and
    the SKU moved to `info/part-number`. Mapping info/model onto `model` would
    keep the entity and change what it displays."""
    bess = _device("bess")
    bess.update_property("info", "part-number", "1232100-00-E")

    battery = build_battery(bess, [])

    assert battery.product_name == "Example BESS"  # designation
    assert battery.model == "1232100-00-E"  # SKU


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
# PV
# ---------------------------------------------------------------------------


def test_pv_metadata_and_feed() -> None:
    pv = build_pv(_device("pv"), feed_circuit_ids(_circuits()))

    assert pv.vendor_name == "Enphase"
    assert pv.product_name == "IQ8PLUS-72-2-US"
    assert pv.nameplate_capacity_w == 10000.0
    assert pv.feed_circuit_id == SOLAR_CIRCUIT


def test_pv_relative_position_is_not_guessed() -> None:
    """Retired in v1.0 and only "derivable from connection records (when
    present)". The integration gates control entities on it, so a wrong value
    creates or removes a control."""
    assert build_pv(_device("pv"), {}).relative_position is None


def test_no_pv_yields_the_empty_snapshot() -> None:
    assert build_pv(None, {}).vendor_name is None


# ---------------------------------------------------------------------------
# EVSE
# ---------------------------------------------------------------------------


def test_evse_state_and_metadata() -> None:
    evse = build_evse(_device("evse"), {})

    assert evse.node_id == "evse"
    assert evse.status == "CHARGING"
    assert evse.lock_state == "LOCKED"
    assert evse.advertised_current_a == 32.0
    assert evse.vendor_name == "SPAN"
    assert evse.product_name == "SPAN Drive"
    assert evse.part_number == "SPN-DRV-001"
    assert evse.serial_number == "SIM-EVSE-example-40t-001"


def test_evse_without_a_feeding_circuit_reports_empty_not_none() -> None:
    """`feed_circuit_id` is non-optional on the dataclass, so an unclaimed EVSE
    gets the empty string rather than breaking construction."""
    assert build_evse(_device("evse"), {}).feed_circuit_id == ""
