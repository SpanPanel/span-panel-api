"""Every charger whose lock is declared settable has a target for it, over every reference capture."""

from __future__ import annotations

import json

import pytest

from reference_payloads.captures import CAPTURES, CaptureTree, capture_snapshot, capture_tree

_EVSE_TYPE = "energy.ebus.device.evse"


def _lock_settable(tree: CaptureTree, device_id: str) -> bool:
    description = json.loads(tree.devices[device_id].description)
    declaration = description["nodes"].get("switch", {}).get("properties", {}).get("lock-state", {})
    return declaration.get("settable") is True


@pytest.mark.parametrize("stem", CAPTURES)
def test_a_settable_lock_is_addressed_on_its_own_charger(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    chargers = [device_id for device_id in tree.devices if tree.device_type(device_id) == _EVSE_TYPE]
    by_device = {}
    for charger in snapshot.evse.values():
        target = charger.lock_control
        if target is not None:
            by_device[target.device_id] = target

    assert set(by_device) == {device_id for device_id in chargers if _lock_settable(tree, device_id)}
    for device_id, target in by_device.items():
        assert target.topic == f"ebus/5/{device_id}/switch/lock-state/set"
        assert (target.node_id, target.property_id) == ("switch", "lock-state")


def test_the_captures_hold_a_settable_lock() -> None:
    assert any(
        _lock_settable(capture_tree(stem), device_id)
        for stem in CAPTURES
        for device_id in capture_tree(stem).devices
        if capture_tree(stem).device_type(device_id) == _EVSE_TYPE
    )
