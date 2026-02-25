"""Tests for protocol interfaces and snapshot models."""

import dataclasses

import pytest

from span_panel_api.models import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanPanelSnapshot,
)
from span_panel_api.protocol import (
    CircuitControlProtocol,
    PanelCapability,
    SpanPanelClientProtocol,
    StreamingCapableProtocol,
)


# ---------------------------------------------------------------------------
# Helpers: minimal stubs that satisfy each protocol structurally
# ---------------------------------------------------------------------------


class _MinimalClient:
    """Satisfies SpanPanelClientProtocol structurally."""

    @property
    def capabilities(self) -> PanelCapability:
        return PanelCapability.NONE

    @property
    def serial_number(self) -> str:
        return "test-serial"

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def ping(self) -> bool:
        return True

    async def get_snapshot(self) -> SpanPanelSnapshot:
        return _make_panel_snapshot()


class _MinimalCircuitControl:
    """Satisfies CircuitControlProtocol structurally."""

    async def set_circuit_relay(self, circuit_id: str, state: str) -> None:
        pass

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> None:
        pass


class _MinimalStreaming:
    """Satisfies StreamingCapableProtocol structurally."""

    def register_snapshot_callback(self, callback):
        def unregister():
            pass

        return unregister

    async def start_streaming(self) -> None:
        pass

    async def stop_streaming(self) -> None:
        pass


class _NotAClient:
    """Does NOT satisfy SpanPanelClientProtocol."""

    pass


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
        "dsm_grid_state": "DSM_GRID_UP",
        "current_run_config": "PANEL_ON_GRID",
        "door_state": "CLOSED",
        "proximity_proven": True,
        "uptime_s": 86400,
        "eth0_link": True,
        "wlan_link": True,
        "wwan_link": False,
    }
    defaults.update(overrides)
    return SpanPanelSnapshot(**defaults)


# ===================================================================
# Protocol structural conformance tests
# ===================================================================


class TestProtocolConformance:
    """Verify runtime_checkable isinstance works for protocol stubs."""

    def test_minimal_client_satisfies_protocol(self):
        client = _MinimalClient()
        assert isinstance(client, SpanPanelClientProtocol)

    def test_minimal_circuit_control_satisfies_protocol(self):
        ctrl = _MinimalCircuitControl()
        assert isinstance(ctrl, CircuitControlProtocol)

    def test_minimal_streaming_satisfies_protocol(self):
        streamer = _MinimalStreaming()
        assert isinstance(streamer, StreamingCapableProtocol)

    def test_non_client_does_not_satisfy_protocol(self):
        obj = _NotAClient()
        assert not isinstance(obj, SpanPanelClientProtocol)


# ===================================================================
# PanelCapability flag tests
# ===================================================================


class TestPanelCapability:
    def test_none_is_zero(self):
        assert PanelCapability.NONE.value == 0

    def test_flags_are_distinct(self):
        all_flags = [
            PanelCapability.PUSH_STREAMING,
            PanelCapability.EBUS_MQTT,
            PanelCapability.CIRCUIT_CONTROL,
            PanelCapability.BATTERY_SOE,
        ]
        values = [f.value for f in all_flags]
        assert len(values) == len(set(values))

    def test_flag_combination(self):
        combined = PanelCapability.EBUS_MQTT | PanelCapability.CIRCUIT_CONTROL
        assert PanelCapability.EBUS_MQTT in combined
        assert PanelCapability.CIRCUIT_CONTROL in combined
        assert PanelCapability.PUSH_STREAMING not in combined

    def test_mqtt_client_capabilities(self):
        caps = (
            PanelCapability.EBUS_MQTT
            | PanelCapability.PUSH_STREAMING
            | PanelCapability.CIRCUIT_CONTROL
            | PanelCapability.BATTERY_SOE
        )
        assert PanelCapability.EBUS_MQTT in caps


# ===================================================================
# SpanCircuitSnapshot tests
# ===================================================================


class TestSpanCircuitSnapshot:
    def test_construction_required_fields(self):
        circuit = _make_circuit_snapshot()
        assert circuit.circuit_id == "abc123"
        assert circuit.name == "Kitchen"
        assert circuit.relay_state == "CLOSED"
        assert circuit.instant_power_w == 150.0

    def test_default_values(self):
        circuit = _make_circuit_snapshot()
        assert circuit.is_240v is False
        assert circuit.current_a is None
        assert circuit.breaker_rating_a is None
        assert circuit.always_on is False
        assert circuit.relay_requester == "UNKNOWN"
        assert circuit.energy_accum_update_time_s == 0
        assert circuit.instant_power_update_time_s == 0

    def test_frozen_rejects_mutation(self):
        circuit = _make_circuit_snapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            circuit.name = "Modified"  # type: ignore[misc]

    def test_equality(self):
        a = _make_circuit_snapshot()
        b = _make_circuit_snapshot()
        assert a == b

    def test_inequality_on_field_change(self):
        a = _make_circuit_snapshot()
        b = _make_circuit_snapshot(name="Garage")
        assert a != b

    def test_v2_fields(self):
        circuit = _make_circuit_snapshot(
            always_on=True,
            relay_requester="USER",
            current_a=12.5,
            breaker_rating_a=20.0,
        )
        assert circuit.always_on is True
        assert circuit.relay_requester == "USER"
        assert circuit.current_a == 12.5
        assert circuit.breaker_rating_a == 20.0

    def test_slots(self):
        assert hasattr(SpanCircuitSnapshot, "__slots__")


# ===================================================================
# SpanBatterySnapshot tests
# ===================================================================


class TestSpanBatterySnapshot:
    def test_default_construction(self):
        battery = SpanBatterySnapshot()
        assert battery.soe_percentage is None
        assert battery.soe_kwh is None

    def test_v1_field(self):
        battery = SpanBatterySnapshot(soe_percentage=85.0)
        assert battery.soe_percentage == 85.0
        assert battery.soe_kwh is None

    def test_v2_field(self):
        battery = SpanBatterySnapshot(soe_percentage=85.0, soe_kwh=10.2)
        assert battery.soe_kwh == 10.2

    def test_frozen_rejects_mutation(self):
        battery = SpanBatterySnapshot(soe_percentage=50.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            battery.soe_percentage = 60.0  # type: ignore[misc]


# ===================================================================
# SpanPanelSnapshot tests
# ===================================================================


class TestSpanPanelSnapshot:
    def test_construction_required_fields(self):
        snapshot = _make_panel_snapshot()
        assert snapshot.serial_number == "nj-2316-XXXX"
        assert snapshot.firmware_version == "spanos2/r202603/05"
        assert snapshot.main_relay_state == "CLOSED"

    def test_default_optional_fields(self):
        snapshot = _make_panel_snapshot()
        assert snapshot.dominant_power_source is None
        assert snapshot.grid_state is None
        assert snapshot.grid_islandable is None
        assert snapshot.l1_voltage is None
        assert snapshot.l2_voltage is None
        assert snapshot.main_breaker_rating_a is None
        assert snapshot.wifi_ssid is None
        assert snapshot.vendor_cloud is None

    def test_default_collections(self):
        snapshot = _make_panel_snapshot()
        assert snapshot.circuits == {}
        assert snapshot.battery == SpanBatterySnapshot()

    def test_frozen_rejects_mutation(self):
        snapshot = _make_panel_snapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.serial_number = "changed"  # type: ignore[misc]

    def test_with_circuits(self):
        circuit = _make_circuit_snapshot()
        snapshot = _make_panel_snapshot(circuits={"abc123": circuit})
        assert len(snapshot.circuits) == 1
        assert snapshot.circuits["abc123"].name == "Kitchen"

    def test_with_battery(self):
        battery = SpanBatterySnapshot(soe_percentage=75.0, soe_kwh=9.6)
        snapshot = _make_panel_snapshot(battery=battery)
        assert snapshot.battery.soe_percentage == 75.0
        assert snapshot.battery.soe_kwh == 9.6

    def test_v2_native_fields(self):
        snapshot = _make_panel_snapshot(
            dominant_power_source="GRID",
            grid_state="ON_GRID",
            grid_islandable=True,
            l1_voltage=121.5,
            l2_voltage=120.8,
            main_breaker_rating_a=200,
            wifi_ssid="HomeNetwork",
            vendor_cloud="CONNECTED",
        )
        assert snapshot.dominant_power_source == "GRID"
        assert snapshot.grid_state == "ON_GRID"
        assert snapshot.grid_islandable is True
        assert snapshot.l1_voltage == 121.5
        assert snapshot.l2_voltage == 120.8
        assert snapshot.main_breaker_rating_a == 200
        assert snapshot.wifi_ssid == "HomeNetwork"
        assert snapshot.vendor_cloud == "CONNECTED"

    def test_equality(self):
        a = _make_panel_snapshot()
        b = _make_panel_snapshot()
        assert a == b

    def test_slots(self):
        assert hasattr(SpanPanelSnapshot, "__slots__")

    def test_collection_defaults_are_independent(self):
        """Each snapshot gets its own default collections (no shared mutable default)."""
        a = _make_panel_snapshot()
        b = _make_panel_snapshot()
        assert a.circuits is not b.circuits
