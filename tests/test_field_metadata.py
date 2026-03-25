"""Tests for field metadata building from Homie schema."""

from __future__ import annotations

import logging

from span_panel_api.models import FieldMetadata
from span_panel_api.mqtt.field_metadata import build_field_metadata, log_schema_drift


def _make_schema_types() -> dict[str, dict[str, object]]:
    """Build a realistic schema types dict for testing."""
    return {
        "energy.ebus.device.distribution-enclosure.core": {
            "software-version": {"datatype": "string"},
            "door": {"datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"},
            "relay": {"datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"},
            "ethernet": {"datatype": "boolean"},
            "wifi": {"datatype": "boolean"},
            "vendor-cloud": {"datatype": "enum", "format": "UNKNOWN,UNCONNECTED,CONNECTED"},
            "dominant-power-source": {"datatype": "enum"},
            "grid-islandable": {"datatype": "boolean"},
            "l1-voltage": {"datatype": "float", "unit": "V"},
            "l2-voltage": {"datatype": "float", "unit": "V"},
            "breaker-rating": {"datatype": "integer", "unit": "A"},
            "wifi-ssid": {"datatype": "string"},
        },
        "energy.ebus.device.lugs.upstream": {
            "active-power": {"datatype": "float", "unit": "W"},
            "imported-energy": {"datatype": "float", "unit": "Wh"},
            "exported-energy": {"datatype": "float", "unit": "Wh"},
            "l1-current": {"datatype": "float", "unit": "A"},
            "l2-current": {"datatype": "float", "unit": "A"},
        },
        "energy.ebus.device.lugs.downstream": {
            "active-power": {"datatype": "float", "unit": "W"},
            "imported-energy": {"datatype": "float", "unit": "Wh"},
            "exported-energy": {"datatype": "float", "unit": "Wh"},
            "l1-current": {"datatype": "float", "unit": "A"},
            "l2-current": {"datatype": "float", "unit": "A"},
        },
        "energy.ebus.device.circuit": {
            "active-power": {"datatype": "float", "unit": "W"},
            "exported-energy": {"datatype": "float", "unit": "Wh"},
            "imported-energy": {"datatype": "float", "unit": "Wh"},
            "name": {"datatype": "string"},
            "relay": {"datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"},
            "shed-priority": {"datatype": "enum"},
            "current": {"datatype": "float", "unit": "A"},
            "breaker-rating": {"datatype": "integer", "unit": "A"},
            "space": {"datatype": "integer", "format": "1:32:1"},
            "sheddable": {"datatype": "boolean"},
            "never-backup": {"datatype": "boolean"},
            "always-on": {"datatype": "boolean"},
            "dipole": {"datatype": "boolean"},
            "relay-requester": {"datatype": "string"},
        },
        "energy.ebus.device.bess": {
            "soc": {"datatype": "float", "unit": "%"},
            "soe": {"datatype": "float", "unit": "kWh"},
            "vendor-name": {"datatype": "string"},
            "product-name": {"datatype": "string"},
            "model": {"datatype": "string"},
            "serial-number": {"datatype": "string"},
            "software-version": {"datatype": "string"},
            "nameplate-capacity": {"datatype": "float", "unit": "kWh"},
            "connected": {"datatype": "boolean"},
            "grid-state": {"datatype": "enum"},
        },
        "energy.ebus.device.pv": {
            "vendor-name": {"datatype": "string"},
            "product-name": {"datatype": "string"},
            "nameplate-capacity": {"datatype": "float", "unit": "W"},
            "feed": {"datatype": "string"},
            "relative-position": {"datatype": "enum"},
        },
        "energy.ebus.device.evse": {
            "status": {"datatype": "enum"},
            "lock-state": {"datatype": "enum"},
            "advertised-current": {"datatype": "float", "unit": "A"},
            "vendor-name": {"datatype": "string"},
            "product-name": {"datatype": "string"},
            "part-number": {"datatype": "string"},
            "serial-number": {"datatype": "string"},
            "software-version": {"datatype": "string"},
            "feed": {"datatype": "string"},
        },
        "energy.ebus.device.power-flows": {
            "pv": {"datatype": "float", "unit": "W"},
            "battery": {"datatype": "float", "unit": "W"},
            "grid": {"datatype": "float", "unit": "W"},
            "site": {"datatype": "float", "unit": "W"},
        },
    }


class TestBuildFieldMetadata:
    """Tests for build_field_metadata()."""

    def test_panel_power_fields(self) -> None:
        """Panel power fields should have unit W and datatype float."""
        result = build_field_metadata(_make_schema_types())
        assert result["panel.instant_grid_power_w"] == FieldMetadata(unit="W", datatype="float")
        assert result["panel.feedthrough_power_w"] == FieldMetadata(unit="W", datatype="float")

    def test_panel_energy_fields(self) -> None:
        """Panel energy fields should have unit Wh."""
        result = build_field_metadata(_make_schema_types())
        assert result["panel.main_meter_energy_consumed_wh"] == FieldMetadata(unit="Wh", datatype="float")
        assert result["panel.main_meter_energy_produced_wh"] == FieldMetadata(unit="Wh", datatype="float")

    def test_panel_voltage_fields(self) -> None:
        """Voltage fields should have unit V."""
        result = build_field_metadata(_make_schema_types())
        assert result["panel.l1_voltage"] == FieldMetadata(unit="V", datatype="float")
        assert result["panel.l2_voltage"] == FieldMetadata(unit="V", datatype="float")

    def test_circuit_fields(self) -> None:
        """Circuit fields should be present with correct metadata."""
        result = build_field_metadata(_make_schema_types())
        assert result["circuit.instant_power_w"] == FieldMetadata(unit="W", datatype="float")
        assert result["circuit.consumed_energy_wh"] == FieldMetadata(unit="Wh", datatype="float")
        assert result["circuit.current_a"] == FieldMetadata(unit="A", datatype="float")
        assert result["circuit.breaker_rating_a"] == FieldMetadata(unit="A", datatype="integer")

    def test_battery_fields(self) -> None:
        """Battery fields should be present with correct units."""
        result = build_field_metadata(_make_schema_types())
        assert result["battery.soe_percentage"] == FieldMetadata(unit="%", datatype="float")
        assert result["battery.nameplate_capacity_kwh"] == FieldMetadata(unit="kWh", datatype="float")
        assert result["battery.soe_kwh"] == FieldMetadata(unit="kWh", datatype="float")
        # grid-state comes from BESS node but is stored on panel snapshot
        assert result["panel.grid_state"] == FieldMetadata(unit=None, datatype="enum")

    def test_pv_fields(self) -> None:
        """PV fields should have correct units and datatypes."""
        result = build_field_metadata(_make_schema_types())
        assert result["pv.nameplate_capacity_w"] == FieldMetadata(unit="W", datatype="float")
        assert result["pv.feed_circuit_id"] == FieldMetadata(unit=None, datatype="string")
        assert result["pv.relative_position"] == FieldMetadata(unit=None, datatype="enum")

    def test_evse_fields(self) -> None:
        """EVSE fields should be present."""
        result = build_field_metadata(_make_schema_types())
        assert result["evse.advertised_current_a"] == FieldMetadata(unit="A", datatype="float")
        assert result["evse.status"] == FieldMetadata(unit=None, datatype="enum")

    def test_power_flow_fields(self) -> None:
        """Power flow fields should map to panel namespace."""
        result = build_field_metadata(_make_schema_types())
        assert result["panel.power_flow_pv"] == FieldMetadata(unit="W", datatype="float")
        assert result["panel.power_flow_battery"] == FieldMetadata(unit="W", datatype="float")
        assert result["panel.power_flow_site"] == FieldMetadata(unit="W", datatype="float")

    def test_enum_fields_have_no_unit(self) -> None:
        """Enum properties should have unit=None."""
        result = build_field_metadata(_make_schema_types())
        assert result["panel.door_state"].unit is None
        assert result["panel.door_state"].datatype == "enum"
        assert result["panel.main_relay_state"].unit is None

    def test_boolean_fields(self) -> None:
        """Boolean properties should have datatype boolean and no unit."""
        result = build_field_metadata(_make_schema_types())
        assert result["panel.eth0_link"] == FieldMetadata(unit=None, datatype="boolean")
        assert result["circuit.is_sheddable"] == FieldMetadata(unit=None, datatype="boolean")

    def test_empty_schema_returns_empty(self) -> None:
        """Empty schema should produce no metadata."""
        result = build_field_metadata({})
        assert result == {}

    def test_field_path_convention(self) -> None:
        """All field paths should follow the type.field convention."""
        valid_prefixes = {"panel", "circuit", "battery", "pv", "evse"}
        result = build_field_metadata(_make_schema_types())
        for path in result:
            parts = path.split(".", 1)
            assert len(parts) == 2, f"Bad path: {path}"
            assert parts[0] in valid_prefixes, f"Bad prefix in {path}"


class TestLugsFallback:
    """Tests for generic lugs type fallback."""

    def test_generic_lugs_type_works(self) -> None:
        """When schema uses TYPE_LUGS instead of typed variants, should still resolve."""
        schema: dict[str, dict[str, object]] = {
            "energy.ebus.device.lugs": {
                "active-power": {"datatype": "float", "unit": "W"},
                "imported-energy": {"datatype": "float", "unit": "Wh"},
                "exported-energy": {"datatype": "float", "unit": "Wh"},
                "l1-current": {"datatype": "float", "unit": "A"},
                "l2-current": {"datatype": "float", "unit": "A"},
            },
        }
        result = build_field_metadata(schema)
        assert result["panel.instant_grid_power_w"] == FieldMetadata(unit="W", datatype="float")
        assert result["panel.feedthrough_power_w"] == FieldMetadata(unit="W", datatype="float")
        assert result["panel.upstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")
        assert result["panel.downstream_l2_current_a"] == FieldMetadata(unit="A", datatype="float")

    def test_typed_lugs_preferred_over_generic(self) -> None:
        """When both typed and generic lugs exist, typed should be used."""
        schema: dict[str, dict[str, object]] = {
            "energy.ebus.device.lugs": {
                "active-power": {"datatype": "float", "unit": "kW"},
            },
            "energy.ebus.device.lugs.upstream": {
                "active-power": {"datatype": "float", "unit": "W"},
            },
        }
        result = build_field_metadata(schema)
        assert result["panel.instant_grid_power_w"].unit == "W"


class TestLogSchemaDrift:
    """Tests for log_schema_drift diagnostic logging."""

    def test_new_node_type(self, caplog: logging.LogCaptureFixture) -> None:
        """New node types should be logged."""
        previous: dict[str, dict[str, object]] = {}
        current: dict[str, dict[str, object]] = {"energy.new.type": {"prop": {}}}
        with caplog.at_level(logging.DEBUG):
            log_schema_drift(previous, current)
        assert "new node type 'energy.new.type'" in caplog.text

    def test_removed_node_type(self, caplog: logging.LogCaptureFixture) -> None:
        """Removed node types should be logged."""
        previous: dict[str, dict[str, object]] = {"energy.old.type": {"prop": {}}}
        current: dict[str, dict[str, object]] = {}
        with caplog.at_level(logging.DEBUG):
            log_schema_drift(previous, current)
        assert "removed node type 'energy.old.type'" in caplog.text

    def test_new_property(self, caplog: logging.LogCaptureFixture) -> None:
        """New properties within an existing node type should be logged."""
        previous: dict[str, dict[str, object]] = {"core": {"door": {}}}
        current: dict[str, dict[str, object]] = {"core": {"door": {}, "wifi": {}}}
        with caplog.at_level(logging.DEBUG):
            log_schema_drift(previous, current)
        assert "new property 'core/wifi'" in caplog.text

    def test_removed_property(self, caplog: logging.LogCaptureFixture) -> None:
        """Removed properties within an existing node type should be logged."""
        previous: dict[str, dict[str, object]] = {"core": {"door": {}, "wifi": {}}}
        current: dict[str, dict[str, object]] = {"core": {"door": {}}}
        with caplog.at_level(logging.DEBUG):
            log_schema_drift(previous, current)
        assert "removed property 'core/wifi'" in caplog.text

    def test_changed_attribute(self, caplog: logging.LogCaptureFixture) -> None:
        """Changed property attributes should be logged."""
        previous: dict[str, dict[str, object]] = {
            "core": {"voltage": {"datatype": "float", "unit": "V"}},
        }
        current: dict[str, dict[str, object]] = {
            "core": {"voltage": {"datatype": "float", "unit": "kV"}},
        }
        with caplog.at_level(logging.DEBUG):
            log_schema_drift(previous, current)
        assert "unit changed: 'V'" in caplog.text
        assert "'kV'" in caplog.text

    def test_no_drift(self, caplog: logging.LogCaptureFixture) -> None:
        """Identical schemas should produce no log output."""
        schema: dict[str, dict[str, object]] = {
            "core": {"door": {"datatype": "enum"}},
        }
        with caplog.at_level(logging.DEBUG):
            log_schema_drift(schema, schema)
        assert "Schema drift" not in caplog.text

    def test_non_dict_props_skipped(self, caplog: logging.LogCaptureFixture) -> None:
        """Non-dict property values should be silently skipped."""
        previous: dict[str, dict[str, object]] = {"core": {"door": "not_a_dict"}}
        current: dict[str, dict[str, object]] = {"core": {"door": "not_a_dict"}}
        with caplog.at_level(logging.DEBUG):
            log_schema_drift(previous, current)
        assert "Schema drift" not in caplog.text
