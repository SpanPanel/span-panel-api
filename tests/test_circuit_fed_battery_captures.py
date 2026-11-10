"""A battery fed by a circuit reports that circuit's link status, over every reference capture."""

from __future__ import annotations

import pytest

from reference_payloads.captures import CAPTURES, capture_snapshot, capture_tree, circuit_fed_batteries


@pytest.mark.parametrize("stem", CAPTURES)
def test_a_circuit_fed_battery_follows_its_circuit(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    for circuit_id in circuit_fed_batteries(tree).values():
        status = tree.value(circuit_id, "connection/feeds-device-status")
        assert snapshot.battery.connected == (None if status is None else status == "OK")
        assert snapshot.battery.feed_circuit_id == circuit_id
        assert snapshot.circuits[circuit_id].device_type == "circuit"


def test_the_captures_hold_a_circuit_fed_battery() -> None:
    assert any(circuit_fed_batteries(capture_tree(stem)) for stem in CAPTURES)
