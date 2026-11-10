"""Shared meters and relays, over every reference capture.

Each circuit's peers and each group are compared with what the capture's own
`shared-with-device-ids` values list.
"""

from __future__ import annotations

import pytest

from reference_payloads.captures import CAPTURES, CaptureTree, capture_adapter, capture_snapshot, capture_tree
from span_panel_api import shared_meter_groups
from span_panel_api.models import device_id_order, is_discovery_path

_SHARED_WITH = ("meter/shared-with-device-ids", "switch/shared-with-device-ids")


def _declared_peers(tree: CaptureTree, circuit_id: str, topic: str) -> tuple[str, ...] | None:
    published = tree.value(circuit_id, topic)
    if published is None:
        return None
    circuits = set(tree.circuits())
    listed = {peer.strip() for peer in published.split(",") if peer.strip()}
    return tuple(sorted((listed & circuits) - {circuit_id}, key=device_id_order))


@pytest.mark.parametrize("stem", CAPTURES)
def test_each_circuit_names_the_peers_its_capture_lists(stem: str) -> None:
    tree, circuits = capture_tree(stem), capture_snapshot(stem).circuits
    for circuit_id in tree.circuits():
        circuit = circuits[circuit_id]
        assert circuit.meter_shared_with == _declared_peers(tree, circuit_id, _SHARED_WITH[0]), circuit_id
        assert circuit.relay_shared_with == _declared_peers(tree, circuit_id, _SHARED_WITH[1]), circuit_id


@pytest.mark.parametrize("stem", CAPTURES)
def test_shared_meters_group_exactly_their_declared_peers(stem: str) -> None:
    tree = capture_tree(stem)
    members: set[frozenset[str]] = set()
    for circuit_id in tree.circuits():
        peers = _declared_peers(tree, circuit_id, _SHARED_WITH[0])
        if peers:
            members.add(frozenset({circuit_id, *peers}))
    expected = {group[0]: group for group in (tuple(sorted(member, key=device_id_order)) for member in members)}

    assert shared_meter_groups(capture_snapshot(stem).circuits) == expected


def test_the_captures_hold_shared_meters() -> None:
    assert any(shared_meter_groups(capture_snapshot(stem).circuits) for stem in CAPTURES)


@pytest.mark.parametrize("stem", CAPTURES)
def test_shared_with_is_neither_discovered_nor_an_extension_reading(stem: str) -> None:
    adapter = capture_adapter(stem)
    discovered = [path for path in adapter.build_field_metadata() if is_discovery_path(path)]
    extension = {f"{row.node_id}/{row.property_id}" for row in adapter.build_snapshot().extension_properties}

    assert not [path for path in discovered if path.endswith(tuple(f"/{topic}" for topic in _SHARED_WITH))]
    assert not extension & set(_SHARED_WITH)
