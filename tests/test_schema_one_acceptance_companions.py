"""What the divergent acceptance tests assert after their divergent line.

`expected_failures.py` lists three acceptance tests as divergent: two first
assert a `device_type` this library deliberately does not report, and one a
discovery row for a property this library reads. A strict xfail stops there,
so nothing after that line ever runs. These tests assert the rest, over the
same trees, so a regression in it still fails the run. Where an assertion needs
a later change, its test is listed as pending instead.
"""

from __future__ import annotations

from span_panel_api.models import SpanPVSnapshot, is_discovery_path
from test_schema_one_other_models import KITCHEN, SOLAR, _shipped, _shipped_metadata, _snapshot, _tree


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


def test_the_shipped_tree_reports_its_unread_properties_as_discovered() -> None:
    """Every discovery row the divergent test expects, except the one this library reads."""
    discovered = {path for path in _shipped_metadata() if is_discovery_path(path)}

    for prop in ("name", "latitude", "longitude", "address-lines", "utility-meter-serial-number"):
        assert f"discovered.distribution-enclosure/info/{prop}" in discovered
    for path in ("info/tags", "info/locations", "info/dedicated", "connection/backed-up"):
        assert f"discovered.circuit/{path}" in discovered
    for prop in ("service-rating", "feeds-role"):
        assert f"discovered.lugs/connection/{prop}" in discovered
    assert "discovered.lugs/connection/overcurrent-protection" not in discovered
