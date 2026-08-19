"""Tests for field metadata building from Homie schema."""

from __future__ import annotations

import logging

from span_panel_api.models import FieldMetadata
from span_panel_api_schema_0.field_metadata import build_field_metadata
from span_panel_api.schema_drift import log_schema_drift


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


# ---------------------------------------------------------------------------
# Resolved vs. absent — the three-way contract
#
# A field path missing from the metadata used to be ambiguous: it could mean
# "this panel has no such hardware" or "the mapping dropped the property".
# `FieldMetadata.resolved` splits the two so consumers stop reconstructing the
# difference from telemetry.
# ---------------------------------------------------------------------------


def _device(
    device_id: str,
    type_: str,
    nodes: dict[str, object],
    values: dict[str, dict[str, str]] | None = None,
) -> object:
    """Minimal stand-in for ebus_sdk.DiscoveredDevice.

    `build_field_metadata` reads declarations via `.description`, but the
    downstream-lugs path resolves the device by its published `info/direction`
    *value*, which is a different thing from declaring the property. `values`
    supplies those readings, keyed node → property; anything unlisted reads as
    unpublished.
    """

    class _D:
        def __init__(self) -> None:
            self.id = device_id
            self.description = {"type": type_, "nodes": nodes}

        def get_property(self, node: str, prop: str) -> str | None:
            return (values or {}).get(node, {}).get(prop)

    return _D()


_FULL_LUGS_METER: dict[str, object] = {
    "properties": {
        "active-power": {"datatype": "float", "unit": "W"},
        "imported-energy": {"datatype": "float", "unit": "Wh"},
        "exported-energy": {"datatype": "float", "unit": "Wh"},
        "current-a": {"datatype": "float", "unit": "A"},
        "current-b": {"datatype": "float", "unit": "A"},
    }
}


def _lugs(device_id: str, direction: str, meter: dict[str, object] | None) -> object:
    """A lugs device that publishes its direction, with `meter` as given.

    `meter=None` means the device declares no meter node at all — which is a
    different claim from declaring one that lists nothing.
    """
    nodes: dict[str, object] = {"info": {"properties": {"direction": {"datatype": "string"}}}}
    if meter is not None:
        nodes["meter"] = meter
    return _device(device_id, "energy.ebus.device.lugs", nodes, values={"info": {"direction": direction}})


def test_present_device_missing_property_is_unresolved() -> None:
    """A circuit device that declares no `meter` power property is a real gap,
    not absent hardware — the integration must be able to tell them apart."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    circuit = _device(
        device_id="c1",
        type_="energy.ebus.device.circuit",
        nodes={"info": {"properties": {"name": {"datatype": "string"}}}, "meter": {"properties": {}}},
    )
    metadata = build_schema_one([circuit])

    entry = metadata["circuit.instant_power_w"]
    assert entry.resolved is False
    assert entry.unit is None


def test_a_node_declaring_no_properties_at_all_is_still_present() -> None:
    """The boundary the presence rule has to get right in both directions.

    A `meter` node with an empty property set is the strongest form of the gap
    — the node is there and declares nothing — while a circuit with no `meter`
    node at all is a circuit that does not meter. Deriving presence from the
    declared-property map would collapse the two, since neither contributes a
    property to read back.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    unmetered = _device(
        device_id="c1",
        type_="energy.ebus.device.circuit",
        nodes={"info": {"properties": {"name": {"datatype": "string"}}}},
    )

    assert "circuit.instant_power_w" not in build_schema_one([unmetered])


def test_absent_device_type_yields_no_entry() -> None:
    """No BESS device means no battery entry at all — not an unresolved one."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    circuit = _device(
        device_id="c1",
        type_="energy.ebus.device.circuit",
        nodes={"info": {"properties": {"name": {"datatype": "string"}}}},
    )
    metadata = build_schema_one([circuit])

    assert "battery.soe_percentage" not in metadata


def test_absent_node_on_present_device_yields_no_entry() -> None:
    """The panel device is always present, but a panel with no power-flows node
    has no power-flow hardware — that must not read as degradation."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    panel = _device(
        device_id="p1",
        type_="energy.ebus.device.distribution-enclosure",
        nodes={"info": {"properties": {"serial-number": {"datatype": "string"}}}},
    )
    metadata = build_schema_one([panel])

    assert "panel.power_flow_pv" not in metadata


def test_present_node_missing_property_on_a_subtyped_device_is_unresolved() -> None:
    """The subtype rule, which survived the move to a direction-resolved lookup.

    Firmware may declare `…device.lugs.upstream` where the code says
    `…device.lugs`. That used to be `_lookup`'s prefix fallback; the lugs paths
    no longer go through the table, so the rule now lives in the
    `startswith(TYPE_LUGS)` filter that feeds `find_lugs`. Either way an
    exact-match test would read a dropped property on typed-lugs firmware as
    absent hardware, which is the misclassification this field exists to
    prevent — so the expectation is unchanged and only its mechanism moved.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    lugs = _device(
        device_id="lugs-upstream",
        type_="energy.ebus.device.lugs.upstream",
        nodes={
            "info": {"properties": {"direction": {"datatype": "string"}}},
            "meter": {"properties": {"active-power": {"datatype": "float", "unit": "W"}}},
        },
        values={"info": {"direction": "UPSTREAM"}},
    )
    metadata = build_schema_one([lugs])

    assert metadata["panel.instant_grid_power_w"] == FieldMetadata(unit="W", datatype="float")
    assert metadata["panel.upstream_l1_current_a"].resolved is False


def test_the_subtype_rule_holds_for_both_lugs_directions() -> None:
    """Both halves, since each resolves its own device through the filter."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    def _typed(device_id: str, type_: str, direction: str) -> object:
        return _device(
            device_id,
            type_,
            {
                "info": {"properties": {"direction": {"datatype": "string"}}},
                "meter": {"properties": {"current-a": {"datatype": "float", "unit": "A"}}},
            },
            values={"info": {"direction": direction}},
        )

    metadata = build_schema_one(
        [
            _typed("u", "energy.ebus.device.lugs.upstream", "UPSTREAM"),
            _typed("d", "energy.ebus.device.lugs.downstream", "DOWNSTREAM"),
        ]
    )

    assert metadata["panel.upstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")
    assert metadata["panel.downstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")
    # And the drop classification still reaches subtyped devices.
    assert metadata["panel.instant_grid_power_w"].resolved is False
    assert metadata["panel.feedthrough_power_w"].resolved is False


def test_resolved_defaults_true() -> None:
    """Existing construction sites keep working unchanged."""
    assert FieldMetadata(unit="W", datatype="float").resolved is True


def test_downstream_lugs_missing_properties_are_unresolved_not_absent() -> None:
    """The downstream lugs answer to the same contract as everything else.

    These five paths bypass `_PROPERTY_FIELD_MAP` — both lugs devices share
    type, node and properties, so the table can only address one of them — and
    resolve through a direction-matched lookup instead. That second path had
    kept the pre-change `continue`, so a downstream device that was plainly in
    the tree, and already resolving `feedthrough_power_w` from the very same
    `meter` node, reported its dropped properties as absent hardware.

    The asymmetry is the sharper half of the defect: in this one tree the
    upstream paths report a dropped property as `resolved=False` while the
    downstream paths reported nothing, so a consumer applying one rule to
    `panel.*` lugs fields got different semantics by direction.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    upstream = _lugs("lugs-upstream", "UPSTREAM", _FULL_LUGS_METER)
    downstream = _lugs(
        "lugs-downstream",
        "DOWNSTREAM",
        {
            "properties": {
                "active-power": {"datatype": "float", "unit": "W"},
                "exported-energy": {"datatype": "float", "unit": "Wh"},
            }
        },
    )
    metadata = build_schema_one([upstream, downstream])

    # Present on both devices: unchanged, and still carrying real units.
    assert metadata["panel.instant_grid_power_w"] == FieldMetadata(unit="W", datatype="float")
    assert metadata["panel.upstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")
    assert metadata["panel.feedthrough_power_w"] == FieldMetadata(unit="W", datatype="float")
    assert metadata["panel.feedthrough_energy_produced_wh"] == FieldMetadata(unit="Wh", datatype="float")

    # Dropped by a device that is present and declares the node: degradation.
    for degraded in (
        "panel.feedthrough_energy_consumed_wh",
        "panel.downstream_l1_current_a",
        "panel.downstream_l2_current_a",
    ):
        assert degraded in metadata, f"{degraded} read as absent hardware for a device in the tree"
        assert metadata[degraded] == FieldMetadata(unit=None, datatype="unknown", resolved=False)


def test_the_two_lugs_directions_classify_the_same_drop_the_same_way() -> None:
    """Stated as an equality rather than two separate expectations.

    The two halves reach their metadata through different code — the table for
    upstream, a direction-matched lookup for downstream — so nothing structural
    keeps them agreeing. This fails if either side drifts.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    bare_meter: dict[str, object] = {"properties": {}}
    metadata = build_schema_one(
        [_lugs("lugs-upstream", "UPSTREAM", bare_meter), _lugs("lugs-downstream", "DOWNSTREAM", bare_meter)]
    )

    assert metadata["panel.upstream_l1_current_a"] == metadata["panel.downstream_l1_current_a"]
    assert metadata["panel.upstream_l1_current_a"].resolved is False


def test_downstream_lugs_without_a_meter_node_yields_no_entry() -> None:
    """The other side of the boundary, and the reason the node is fetched
    rather than defaulted.

    A device with no `meter` node does not meter, so its paths are absent
    hardware. Reading properties out of a `.get(NODE_METER, {})` default would
    make that indistinguishable from a `meter` node listing nothing, and this
    whole distinction turns on telling those apart.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    metadata = build_schema_one(
        [_lugs("lugs-upstream", "UPSTREAM", _FULL_LUGS_METER), _lugs("lugs-downstream", "DOWNSTREAM", None)]
    )

    for absent in (
        "panel.feedthrough_power_w",
        "panel.feedthrough_energy_consumed_wh",
        "panel.feedthrough_energy_produced_wh",
        "panel.downstream_l1_current_a",
        "panel.downstream_l2_current_a",
    ):
        assert absent not in metadata, f"{absent} was described with no meter node to describe"

    assert metadata["panel.upstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")


def test_an_upstream_drop_is_not_masked_by_the_downstream_device() -> None:
    """The failure mode `resolved` exists to prevent, in the one place the
    lookup could not see it.

    `_lookup` keys on (type, node, property) and the two lugs devices match on
    all three, so a property the *upstream* device dropped was still answered —
    with a real unit — by the downstream device that still declared it. A gap
    reported as fine, which is strictly worse than the inverse: a false
    `resolved=False` shows up as a repair someone can see, while a false
    `resolved=True` lets the sensor die silently with nothing to flag it.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    upstream = _lugs(
        "lugs-upstream",
        "UPSTREAM",
        {
            "properties": {
                "active-power": {"datatype": "float", "unit": "W"},
                "imported-energy": {"datatype": "float", "unit": "Wh"},
                "exported-energy": {"datatype": "float", "unit": "Wh"},
                "current-b": {"datatype": "float", "unit": "A"},
            }
        },
    )
    downstream = _lugs("lugs-downstream", "DOWNSTREAM", _FULL_LUGS_METER)
    metadata = build_schema_one([upstream, downstream])

    assert metadata["panel.upstream_l1_current_a"] == FieldMetadata(unit=None, datatype="unknown", resolved=False)
    # The downstream device still declares it, and still resolves it — the point
    # is that its declaration must not answer for the other device.
    assert metadata["panel.downstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")
    # Everything the upstream device does declare is untouched.
    assert metadata["panel.upstream_l2_current_a"] == FieldMetadata(unit="A", datatype="float")
    assert metadata["panel.instant_grid_power_w"] == FieldMetadata(unit="W", datatype="float")


def test_a_downstream_drop_is_not_masked_by_the_upstream_device() -> None:
    """The mirror, held separately because the two directions reach their
    metadata through the same helper only after this change — and a later edit
    that re-tables one direction would break exactly one of the pair."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    upstream = _lugs("lugs-upstream", "UPSTREAM", _FULL_LUGS_METER)
    downstream = _lugs(
        "lugs-downstream",
        "DOWNSTREAM",
        {
            "properties": {
                "active-power": {"datatype": "float", "unit": "W"},
                "imported-energy": {"datatype": "float", "unit": "Wh"},
                "exported-energy": {"datatype": "float", "unit": "Wh"},
                "current-b": {"datatype": "float", "unit": "A"},
            }
        },
    )
    metadata = build_schema_one([upstream, downstream])

    assert metadata["panel.downstream_l1_current_a"] == FieldMetadata(unit=None, datatype="unknown", resolved=False)
    assert metadata["panel.upstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")


def test_no_upstream_device_yields_no_entry() -> None:
    """The upstream half of the contract's third case, matching the downstream
    one: absent hardware is absent, not degraded."""
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    metadata = build_schema_one([_lugs("lugs-downstream", "DOWNSTREAM", _FULL_LUGS_METER)])

    for absent in (
        "panel.instant_grid_power_w",
        "panel.main_meter_energy_consumed_wh",
        "panel.main_meter_energy_produced_wh",
        "panel.upstream_l1_current_a",
        "panel.upstream_l2_current_a",
    ):
        assert absent not in metadata, f"{absent} was described with no upstream device to describe"

    assert metadata["panel.downstream_l1_current_a"] == FieldMetadata(unit="A", datatype="float")


def test_the_subtype_rule_applies_beyond_lugs() -> None:
    """`_lookup` and `_node_declared` keep a general subtype rule, so cover it
    generally.

    A device typed `X.Y` satisfies a row written for `X`, because eBus types are
    hierarchical and a subtype carries its parent's properties. Lugs were the
    only instance exercising it until they moved to a direction-resolved
    lookup; without a non-lugs case the rule would now be both untested and
    invisible, and the next reader would be entitled to delete it.

    Both halves are asserted together on purpose: resolution and presence have
    to agree about which devices answer for a row, or a subtyped device that
    dropped a property resolves through one and misclassifies through the other.
    """
    from span_panel_api_schema_1.field_metadata import build_field_metadata as build_schema_one

    subtyped_circuit = _device(
        device_id="c1",
        type_="energy.ebus.device.circuit.branch",
        nodes={
            "meter": {"properties": {"active-power": {"datatype": "float", "unit": "W"}}},
            "breaker": {"properties": {}},
        },
    )
    metadata = build_schema_one([subtyped_circuit])

    # Resolution reaches the subtype.
    assert metadata["circuit.instant_power_w"] == FieldMetadata(unit="W", datatype="float")
    # Presence reaches it too: declared nodes that omit a property are gaps...
    assert metadata["circuit.current_a"].resolved is False
    assert metadata["circuit.breaker_rating_a"].resolved is False
    # ...while a node the subtype never declares stays absent.
    assert "circuit.relay_state" not in metadata
