"""Tests for protocol interfaces and snapshot models."""

import dataclasses

import pytest

from span_panel_api.models import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanPanelSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers: snapshot factory functions
# ---------------------------------------------------------------------------


def _make_circuit_snapshot(**overrides) -> SpanCircuitSnapshot:
    defaults = {
        "circuit_id": "abc123",
        "name": "Kitchen",
        "relay_state": "CLOSED",
        "instant_power_w": 150.0,
        "produced_energy_wh": 0.0,
        "consumed_energy_wh": 500.0,
        "tabs": [1],
        "priority": "MUST_HAVE",
        "is_user_controllable": True,
        "is_sheddable": False,
        "is_never_backup": False,
    }
    defaults.update(overrides)
    return SpanCircuitSnapshot(**defaults)


def _make_panel_snapshot(**overrides) -> SpanPanelSnapshot:
    defaults = {
        "serial_number": "nj-2316-XXXX",
        "firmware_version": "spanos2/r202603/05",
        "main_relay_state": "CLOSED",
        "instant_grid_power_w": 3500.0,
        "feedthrough_power_w": 0.0,
        "main_meter_energy_consumed_wh": 10000.0,
        "main_meter_energy_produced_wh": 500.0,
        "feedthrough_energy_consumed_wh": 0.0,
        "feedthrough_energy_produced_wh": 0.0,
        "dsm_state": "DSM_ON_GRID",
        "current_run_config": "PANEL_ON_GRID",
        "door_state": "CLOSED",
        "proximity_proven": True,
        "uptime_s": 86400,
        "eth0_link": True,
        "wlan_link": True,
        "wwan_link": False,
        "panel_size": 32,
    }
    defaults.update(overrides)
    return SpanPanelSnapshot(**defaults)


# ===================================================================
# SpanCircuitSnapshot tests
# ===================================================================


class TestSpanCircuitSnapshot:
    def test_frozen_rejects_mutation(self):
        circuit = _make_circuit_snapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            circuit.name = "Modified"  # type: ignore[misc]


# ===================================================================
# SpanBatterySnapshot tests
# ===================================================================


class TestSpanBatterySnapshot:
    def test_frozen_rejects_mutation(self):
        battery = SpanBatterySnapshot(soe_percentage=50.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            battery.soe_percentage = 60.0  # type: ignore[misc]


# ===================================================================
# SpanPanelSnapshot tests
# ===================================================================


class TestSpanPanelSnapshot:
    def test_frozen_rejects_mutation(self):
        snapshot = _make_panel_snapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.serial_number = "changed"  # type: ignore[misc]

    def test_collection_defaults_are_independent(self):
        """Each snapshot gets its own default collections (no shared mutable default)."""
        a = _make_panel_snapshot()
        b = _make_panel_snapshot()
        assert a.circuits is not b.circuits
