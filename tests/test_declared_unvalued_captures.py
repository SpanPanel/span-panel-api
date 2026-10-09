"""Declared but unvalued properties, over every reference capture.

A panel can declare a property and never publish a value for it. Declaring it
still says something: a declared battery is there before it reports. It may
not stall connecting, and it may not be read as a value nobody published.

Properties over the whole capture set: each test selects its cases by what the
capture declares and reads the values it expects from the same capture.
"""

from __future__ import annotations

import logging

import pytest

from reference_payloads.captures import CAPTURES, CaptureTree, capture_adapter, capture_snapshot, capture_tree
from span_panel_api_schema_1.adapter import FEED_GRACE_S

_BATTERY_TYPE = "energy.ebus.device.bess"
_DER_TYPES = frozenset({_BATTERY_TYPE, "energy.ebus.device.pv", "energy.ebus.device.evse"})

_MODEL = "info/model"


def _batteries(tree: CaptureTree) -> tuple[str, ...]:
    return tuple(device_id for device_id in tree.devices if tree.device_type(device_id) == _BATTERY_TYPE)


def _unvalued_models(tree: CaptureTree) -> tuple[str, ...]:
    """Battery, inverter and charger devices that declare a model and publish none."""
    return tuple(
        device_id
        for device_id in tree.devices
        if tree.device_type(device_id) in _DER_TYPES
        and tree.declares(device_id, _MODEL)
        and tree.value(device_id, _MODEL) is None
    )


@pytest.mark.parametrize("stem", CAPTURES)
def test_a_declared_battery_is_present_before_it_reports(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    for battery_id in _batteries(tree):
        assert snapshot.battery.present is True
        assert snapshot.battery.soe_kwh == tree.number(battery_id, "soc/soe")
        assert snapshot.battery.soe_percentage == tree.number(battery_id, "soc/soc")


@pytest.mark.parametrize("stem", CAPTURES)
def test_a_tree_without_a_battery_reports_absent(stem: str) -> None:
    tree = capture_tree(stem)
    if not _batteries(tree):
        assert capture_snapshot(stem).battery.present is False


def test_the_captures_hold_a_battery_that_has_not_reported() -> None:
    """At least one capture declares a battery whose state of energy is unvalued."""
    assert any(
        capture_tree(stem).value(battery_id, "soc/soe") is None
        for stem in CAPTURES
        for battery_id in _batteries(capture_tree(stem))
    )


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_device_model_wait_releases_after_the_grace(stem: str, caplog: pytest.LogCaptureFixture) -> None:
    """A model that is declared and never published holds connecting for the grace only.

    Before the grace has run the device is still waited on, so a model landing in
    the same burst as the rest of the tree still makes the first snapshot. After
    it, the device is no longer waited on, and each is reported once at INFO.
    """
    tree = capture_tree(stem)
    unvalued = _unvalued_models(tree)
    now = [100.0]
    adapter = capture_adapter(stem, clock=lambda: now[0])

    assert set(unvalued) <= set(adapter.circuit_nodes_missing_names())

    now[0] += FEED_GRACE_S
    with caplog.at_level(logging.INFO, logger="span_panel_api_schema_1.adapter"):
        assert adapter.circuit_nodes_missing_names() == []
        assert adapter.circuit_nodes_missing_names() == []

    reported = [record for record in caplog.records if record.levelno == logging.INFO]
    assert sorted(device_id for record in reported for device_id in unvalued if device_id in record.getMessage()) == (
        sorted(unvalued)
    )
    assert len(reported) == len(unvalued)


def test_the_captures_hold_a_device_model_that_is_never_published() -> None:
    assert any(_unvalued_models(capture_tree(stem)) for stem in CAPTURES)
