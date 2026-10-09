"""What the divergent acceptance tests assert after their divergent line.

`expected_failures.py` lists two acceptance tests as divergent: each first
asserts a `device_type` this library deliberately does not report, and a strict
xfail stops there, so nothing after that line ever runs. These tests assert the
rest, over the same trees, so a regression in it still fails the run. Where an
assertion needs a later change, its test is listed as pending instead.
"""

from __future__ import annotations

from span_panel_api.models import SpanPVSnapshot
from test_schema_one_other_models import KITCHEN, SOLAR, _shipped, _snapshot, _tree


def test_a_solar_role_circuit_keeps_its_reading_and_its_neighbour_its_type() -> None:
    snapshot = _snapshot(_tree())

    assert snapshot.circuits[SOLAR].instant_power_w == -8500.0
    assert snapshot.circuits[KITCHEN].device_type == "circuit"


def test_the_shipped_tree_labels_its_charger_circuit_and_has_no_pv_device() -> None:
    snapshot = _shipped()

    assert snapshot.circuits["circuit-3"].device_type == "evse"
    assert snapshot.pv == SpanPVSnapshot()
    assert snapshot.pv_inverters == {}


def test_the_shipped_tree_reports_a_scheduled_relay_requester() -> None:
    assert _shipped().circuits["circuit-5"].relay_requester == "CIRCUIT_SCHEDULER"


def test_the_shipped_tree_reads_busbar_current_and_frequency() -> None:
    snapshot = _shipped()

    assert snapshot.busbar_current_a == 41.5
    assert snapshot.frequency_hz == 60.01


def test_the_shipped_tree_resolves_its_shared_relay() -> None:
    assert _shipped().circuits["circuit-1"].relay_shared_with == ("circuit-2",)
