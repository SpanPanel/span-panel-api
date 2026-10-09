"""Every circuit's feeds role equals what its capture declares, over every reference capture."""

from __future__ import annotations

import pytest

from reference_payloads.captures import CAPTURES, capture_snapshot, capture_tree
from span_panel_api import FEEDS_ROLES


@pytest.mark.parametrize("stem", CAPTURES)
def test_each_circuit_reports_its_declared_feeds_role(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    for circuit_id in tree.circuits():
        published = tree.value(circuit_id, "connection/feeds-role")
        expected = published if published in FEEDS_ROLES else None
        assert snapshot.circuits[circuit_id].feeds_role == expected, circuit_id


def test_the_captures_declare_feeds_roles() -> None:
    assert any(
        capture_tree(stem).declares(circuit_id, "connection/feeds-role")
        for stem in CAPTURES
        for circuit_id in capture_tree(stem).circuits()
    )
