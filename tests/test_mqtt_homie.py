"""Phase 3: MQTT/Homie Transport tests.

Tests cover:
- MqttClientConfig model
- HomieDeviceConsumer message parsing and snapshot building
- Circuit ID normalization/denormalization
- Energy sign conventions (active-power negation)
- Lugs direction mapping
- Battery snapshot building
- DSM state derivation
- Property callbacks
- SpanMqttClient protocol compliance
- AsyncMqttBridge construction
- Package exports and version
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api.mqtt.const import (
    HOMIE_STATE_READY,
    MQTT_DEFAULT_MQTTS_PORT,
    MQTT_DEFAULT_WS_PORT,
    MQTT_DEFAULT_WSS_PORT,
    TOPIC_PREFIX,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_CORE,
    TYPE_LUGS,
    TYPE_LUGS_DOWNSTREAM,
    TYPE_LUGS_UPSTREAM,
    TYPE_POWER_FLOWS,
    TYPE_PV,
)
from span_panel_api.mqtt.homie import HomieDeviceConsumer
from span_panel_api.mqtt.models import MqttClientConfig
from span_panel_api.protocol import (
    CircuitControlProtocol,
    PanelCapability,
    SpanPanelClientProtocol,
    StreamingCapableProtocol,
)


SERIAL = "nj-2316-XXXX"
PREFIX = f"{TOPIC_PREFIX}/{SERIAL}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_description(nodes: dict) -> str:
    """Build a Homie $description JSON string."""
    return json.dumps({"nodes": nodes})


def _core_description() -> dict:
    return {
        "core": {"type": TYPE_CORE},
    }


def _full_description() -> dict:
    return {
        "core": {"type": TYPE_CORE},
        "lugs-upstream": {"type": TYPE_LUGS_UPSTREAM},
        "lugs-downstream": {"type": TYPE_LUGS_DOWNSTREAM},
        "aabbccdd-1122-3344-5566-778899001122": {"type": TYPE_CIRCUIT},
        "bess-0": {"type": TYPE_BESS},
    }


def _build_ready_consumer(description_nodes: dict | None = None) -> HomieDeviceConsumer:
    """Create a HomieDeviceConsumer in ready state with given description."""
    consumer = HomieDeviceConsumer(SERIAL)
    consumer.handle_message(f"{PREFIX}/$state", HOMIE_STATE_READY)
    nodes = description_nodes or _full_description()
    consumer.handle_message(f"{PREFIX}/$description", _make_description(nodes))
    return consumer


# ---------------------------------------------------------------------------
# MqttClientConfig
# ---------------------------------------------------------------------------


class TestMqttClientConfig:
    def test_defaults(self):
        config = MqttClientConfig(broker_host="broker.local", username="u", password="p")
        assert config.mqtts_port == MQTT_DEFAULT_MQTTS_PORT
        assert config.ws_port == MQTT_DEFAULT_WS_PORT
        assert config.wss_port == MQTT_DEFAULT_WSS_PORT
        assert config.transport == "tcp"
        assert config.use_tls is True

    def test_effective_port_tcp(self):
        config = MqttClientConfig(broker_host="h", username="u", password="p")
        assert config.effective_port == MQTT_DEFAULT_MQTTS_PORT

    def test_effective_port_websockets_tls(self):
        config = MqttClientConfig(broker_host="h", username="u", password="p", transport="websockets", use_tls=True)
        assert config.effective_port == MQTT_DEFAULT_WSS_PORT

    def test_effective_port_websockets_no_tls(self):
        config = MqttClientConfig(broker_host="h", username="u", password="p", transport="websockets", use_tls=False)
        assert config.effective_port == MQTT_DEFAULT_WS_PORT

    def test_frozen(self):
        config = MqttClientConfig(broker_host="h", username="u", password="p")
        with pytest.raises(AttributeError):
            config.broker_host = "other"


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — state machine
# ---------------------------------------------------------------------------


class TestHomieConsumerState:
    def test_not_ready_initially(self):
        consumer = HomieDeviceConsumer(SERIAL)
        assert not consumer.is_ready()

    def test_not_ready_state_only(self):
        consumer = HomieDeviceConsumer(SERIAL)
        consumer.handle_message(f"{PREFIX}/$state", "ready")
        assert not consumer.is_ready()

    def test_not_ready_description_only(self):
        consumer = HomieDeviceConsumer(SERIAL)
        consumer.handle_message(f"{PREFIX}/$description", _make_description(_core_description()))
        assert not consumer.is_ready()

    def test_ready_when_both(self):
        consumer = _build_ready_consumer(_core_description())
        assert consumer.is_ready()

    def test_ignores_other_serial(self):
        consumer = HomieDeviceConsumer(SERIAL)
        consumer.handle_message(f"{TOPIC_PREFIX}/other-serial/$state", "ready")
        assert not consumer.is_ready()

    def test_ignores_set_topics(self):
        consumer = _build_ready_consumer()
        # /set topics should not be handled as property values
        circuit_node = "aabbccdd-1122-3344-5566-778899001122"
        consumer.handle_message(f"{PREFIX}/{circuit_node}/relay/set", "OPEN")
        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits.get("aabbccdd11223344556677889900112" + "2")
        assert circuit is not None
        assert circuit.relay_state == "UNKNOWN"  # not set to OPEN


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — circuit snapshot
# ---------------------------------------------------------------------------


class TestHomieCircuitSnapshot:
    def test_circuit_id_normalization(self):
        from span_panel_api.mqtt.const import normalize_circuit_id

        assert normalize_circuit_id("aabbccdd-1122-3344-5566-778899001122") == "aabbccdd11223344556677889900112" + "2"

    def test_circuit_id_denormalization(self):
        from span_panel_api.mqtt.const import denormalize_circuit_id

        result = denormalize_circuit_id("aabbccdd11223344556677889900112" + "2")
        assert result == "aabbccdd-1122-3344-5566-778899001122"

    def test_denormalize_non_uuid(self):
        from span_panel_api.mqtt.const import denormalize_circuit_id

        # Non-32-char strings pass through unchanged
        assert denormalize_circuit_id("short") == "short"
        # Already dashed passes through
        assert denormalize_circuit_id("aabbccdd-1122-3344-5566-778899001122") == "aabbccdd-1122-3344-5566-778899001122"

    def test_circuit_power_negation(self):
        """active-power in W, negative=consumption → positive=consumption in snapshot."""
        consumer = _build_ready_consumer()
        node = "aabbccdd-1122-3344-5566-778899001122"
        # -150.0 W consumption → 150.0 W positive in snapshot
        consumer.handle_message(f"{PREFIX}/{node}/active-power", "-150.0")
        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.instant_power_w == 150.0

    def test_circuit_power_positive_generation(self):
        """Positive active-power (generation) → negative in snapshot."""
        consumer = _build_ready_consumer()
        node = "aabbccdd-1122-3344-5566-778899001122"
        consumer.handle_message(f"{PREFIX}/{node}/active-power", "200.0")
        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.instant_power_w == -200.0

    def test_circuit_energy_mapping(self):
        """exported-energy → consumed, imported-energy → produced."""
        consumer = _build_ready_consumer()
        node = "aabbccdd-1122-3344-5566-778899001122"
        consumer.handle_message(f"{PREFIX}/{node}/exported-energy", "12345.6")
        consumer.handle_message(f"{PREFIX}/{node}/imported-energy", "789.0")
        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.consumed_energy_wh == 12345.6
        assert circuit.produced_energy_wh == 789.0

    def test_circuit_properties(self):
        consumer = _build_ready_consumer()
        node = "aabbccdd-1122-3344-5566-778899001122"
        consumer.handle_message(f"{PREFIX}/{node}/name", "Kitchen")
        consumer.handle_message(f"{PREFIX}/{node}/relay", "CLOSED")
        consumer.handle_message(f"{PREFIX}/{node}/shed-priority", "NEVER")
        consumer.handle_message(f"{PREFIX}/{node}/space", "5")
        consumer.handle_message(f"{PREFIX}/{node}/dipole", "true")
        consumer.handle_message(f"{PREFIX}/{node}/sheddable", "true")
        consumer.handle_message(f"{PREFIX}/{node}/never-backup", "false")
        consumer.handle_message(f"{PREFIX}/{node}/always-on", "true")
        consumer.handle_message(f"{PREFIX}/{node}/current", "15.2")
        consumer.handle_message(f"{PREFIX}/{node}/breaker-rating", "20")
        consumer.handle_message(f"{PREFIX}/{node}/relay-requester", "USER")

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]

        assert circuit.name == "Kitchen"
        assert circuit.relay_state == "CLOSED"
        assert circuit.priority == "NEVER"
        assert circuit.tabs == [5, 7]  # dipole: [space, space + 2]
        assert circuit.is_240v is True
        assert circuit.is_sheddable is True
        assert circuit.is_never_backup is False
        assert circuit.always_on is True
        assert circuit.is_user_controllable is False  # not always_on → False
        assert circuit.current_a == 15.2
        assert circuit.breaker_rating_a == 20.0
        assert circuit.relay_requester == "USER"

    def test_circuit_timestamps(self):
        consumer = _build_ready_consumer()
        node = "aabbccdd-1122-3344-5566-778899001122"
        before = int(time.time())
        consumer.handle_message(f"{PREFIX}/{node}/active-power", "-1.0")
        consumer.handle_message(f"{PREFIX}/{node}/exported-energy", "100.0")
        after = int(time.time())

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert before <= circuit.instant_power_update_time_s <= after
        assert before <= circuit.energy_accum_update_time_s <= after

    def test_pv_metadata_node_annotates_circuit(self):
        """PV metadata node's feed property sets device_type and relative_position."""
        circuit_uuid = "aabbccdd-1122-3344-5566-778899001122"
        consumer = _build_ready_consumer(
            {
                "core": {"type": TYPE_CORE},
                circuit_uuid: {"type": TYPE_CIRCUIT},
                "pv": {"type": TYPE_PV},
                "bess-0": {"type": TYPE_BESS},
            }
        )
        # PV node references the circuit via feed and has relative-position
        consumer.handle_message(f"{PREFIX}/pv/feed", circuit_uuid)
        consumer.handle_message(f"{PREFIX}/pv/relative-position", "IN_PANEL")
        consumer.handle_message(f"{PREFIX}/{circuit_uuid}/name", "Solar Panels")
        consumer.handle_message(f"{PREFIX}/{circuit_uuid}/space", "30")
        consumer.handle_message(f"{PREFIX}/{circuit_uuid}/dipole", "true")

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.device_type == "pv"
        assert circuit.relative_position == "IN_PANEL"
        assert circuit.name == "Solar Panels"
        # PV metadata node itself should NOT appear in circuits
        assert "pv" not in snapshot.circuits

    def test_pv_downstream_has_breaker_position(self):
        """PV with relative-position=DOWNSTREAM indicates a breaker-connected PV."""
        circuit_uuid = "aabbccdd-1122-3344-5566-778899001122"
        consumer = _build_ready_consumer(
            {
                "core": {"type": TYPE_CORE},
                circuit_uuid: {"type": TYPE_CIRCUIT},
                "pv": {"type": TYPE_PV},
                "bess-0": {"type": TYPE_BESS},
            }
        )
        consumer.handle_message(f"{PREFIX}/pv/feed", circuit_uuid)
        consumer.handle_message(f"{PREFIX}/pv/relative-position", "DOWNSTREAM")
        consumer.handle_message(f"{PREFIX}/{circuit_uuid}/name", "Solar Breaker")
        consumer.handle_message(f"{PREFIX}/{circuit_uuid}/space", "15")

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.device_type == "pv"
        assert circuit.relative_position == "DOWNSTREAM"

    def test_pv_metadata_node_excluded_from_circuits(self):
        """PV/EVSE metadata nodes should not appear as circuit entities."""
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/pv/feed", "aabbccdd-1122-3344-5566-778899001122")

        snapshot = consumer.build_snapshot()
        # The "pv" node itself should not be in circuits
        assert "pv" not in snapshot.circuits

    def test_circuit_default_device_type(self):
        """Regular circuits without PV/EVSE feed have device_type='circuit' and no relative_position."""
        consumer = _build_ready_consumer()
        node = "aabbccdd-1122-3344-5566-778899001122"
        consumer.handle_message(f"{PREFIX}/{node}/name", "Kitchen")

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.device_type == "circuit"
        assert circuit.relative_position == ""


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — core node
# ---------------------------------------------------------------------------


class TestHomieCoreNode:
    def test_core_properties(self):
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/core/software-version", "spanos2/r202603/05")
        consumer.handle_message(f"{PREFIX}/core/door", "CLOSED")
        consumer.handle_message(f"{PREFIX}/core/relay", "CLOSED")
        consumer.handle_message(f"{PREFIX}/core/ethernet", "true")
        consumer.handle_message(f"{PREFIX}/core/wifi", "true")
        consumer.handle_message(f"{PREFIX}/core/wifi-ssid", "MyNetwork")
        consumer.handle_message(f"{PREFIX}/core/vendor-cloud", "CONNECTED")
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "GRID")
        consumer.handle_message(f"{PREFIX}/core/grid-islandable", "true")
        consumer.handle_message(f"{PREFIX}/core/l1-voltage", "121.5")
        consumer.handle_message(f"{PREFIX}/core/l2-voltage", "120.8")
        consumer.handle_message(f"{PREFIX}/core/breaker-rating", "200")

        snapshot = consumer.build_snapshot()
        assert snapshot.firmware_version == "spanos2/r202603/05"
        assert snapshot.door_state == "CLOSED"
        assert snapshot.main_relay_state == "CLOSED"
        assert snapshot.eth0_link is True
        assert snapshot.wlan_link is True
        assert snapshot.wifi_ssid == "MyNetwork"
        assert snapshot.wwan_link is True
        assert snapshot.vendor_cloud == "CONNECTED"
        assert snapshot.dominant_power_source == "GRID"
        assert snapshot.grid_islandable is True
        assert snapshot.l1_voltage == 121.5
        assert snapshot.l2_voltage == 120.8
        assert snapshot.main_breaker_rating_a == 200

    def test_proximity_proven_when_ready(self):
        consumer = _build_ready_consumer()
        snapshot = consumer.build_snapshot()
        assert snapshot.proximity_proven is True

    def test_uptime_increases(self):
        consumer = _build_ready_consumer()
        snapshot1 = consumer.build_snapshot()
        # uptime should be >= 0
        assert snapshot1.uptime_s >= 0


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — DSM derivation
# ---------------------------------------------------------------------------


class TestHomieDsmDerivation:
    """Tests for the multi-signal dsm_grid_state and tri-state run_config derivations."""

    # -- dsm_grid_state: BESS authoritative --

    def test_bess_on_grid_authoritative(self):
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/bess-0/grid-state", "ON_GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_ON_GRID"
        assert snapshot.grid_state == "ON_GRID"

    def test_bess_off_grid_authoritative(self):
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/bess-0/grid-state", "OFF_GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_OFF_GRID"

    # -- dsm_grid_state: DPS fallback (no BESS) --

    def test_dps_grid_implies_on_grid(self):
        """DPS=GRID → DSM_ON_GRID even without BESS."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_ON_GRID"

    def test_dps_battery_with_grid_power_on_grid(self):
        """DPS=BATTERY but grid still exchanging power → DSM_ON_GRID."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "lugs-upstream": {"type": TYPE_LUGS_UPSTREAM}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "BATTERY")
        consumer.handle_message(f"{PREFIX}/lugs-upstream/active-power", "500.0")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_ON_GRID"

    def test_dps_battery_zero_grid_power_off_grid(self):
        """DPS=BATTERY and zero grid power → DSM_OFF_GRID."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "lugs-upstream": {"type": TYPE_LUGS_UPSTREAM}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "BATTERY")
        consumer.handle_message(f"{PREFIX}/lugs-upstream/active-power", "0.0")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_OFF_GRID"

    def test_dps_pv_with_grid_power_on_grid(self):
        """DPS=PV but grid still exchanging → DSM_ON_GRID."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "lugs-upstream": {"type": TYPE_LUGS_UPSTREAM}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "PV")
        consumer.handle_message(f"{PREFIX}/lugs-upstream/active-power", "-200.0")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_ON_GRID"

    def test_dps_none_returns_unknown(self):
        """DPS=NONE → UNKNOWN (not a known power source)."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "NONE")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "UNKNOWN"

    def test_no_core_returns_unknown(self):
        """No core node at all → UNKNOWN."""
        consumer = _build_ready_consumer({"bess-0": {"type": TYPE_BESS}})
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "UNKNOWN"

    # -- current_run_config: tri-state derivation --

    def test_on_grid_dps_grid(self):
        """DPS=GRID → PANEL_ON_GRID."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.current_run_config == "PANEL_ON_GRID"

    def test_off_grid_battery_islandable_backup(self):
        """Off-grid + islandable + BATTERY → PANEL_BACKUP."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "bess-0": {"type": TYPE_BESS}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "BATTERY")
        consumer.handle_message(f"{PREFIX}/core/grid-islandable", "true")
        consumer.handle_message(f"{PREFIX}/bess-0/grid-state", "OFF_GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_OFF_GRID"
        assert snapshot.current_run_config == "PANEL_BACKUP"

    def test_off_grid_pv_islandable_off_grid(self):
        """Off-grid + islandable + PV → PANEL_OFF_GRID (intentional off-grid)."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "bess-0": {"type": TYPE_BESS}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "PV")
        consumer.handle_message(f"{PREFIX}/core/grid-islandable", "true")
        consumer.handle_message(f"{PREFIX}/bess-0/grid-state", "OFF_GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.dsm_grid_state == "DSM_OFF_GRID"
        assert snapshot.current_run_config == "PANEL_OFF_GRID"

    def test_off_grid_generator_islandable_off_grid(self):
        """Off-grid + islandable + GENERATOR → PANEL_OFF_GRID."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "bess-0": {"type": TYPE_BESS}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "GENERATOR")
        consumer.handle_message(f"{PREFIX}/core/grid-islandable", "true")
        consumer.handle_message(f"{PREFIX}/bess-0/grid-state", "OFF_GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.current_run_config == "PANEL_OFF_GRID"

    def test_off_grid_not_islandable_unknown(self):
        """Off-grid + not islandable → UNKNOWN (shouldn't happen)."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "bess-0": {"type": TYPE_BESS}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "BATTERY")
        consumer.handle_message(f"{PREFIX}/core/grid-islandable", "false")
        consumer.handle_message(f"{PREFIX}/bess-0/grid-state", "OFF_GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.current_run_config == "UNKNOWN"

    def test_off_grid_islandable_dps_none_unknown(self):
        """Off-grid + islandable + DPS=NONE → UNKNOWN."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}, "bess-0": {"type": TYPE_BESS}})
        consumer.handle_message(f"{PREFIX}/core/dominant-power-source", "NONE")
        consumer.handle_message(f"{PREFIX}/core/grid-islandable", "true")
        consumer.handle_message(f"{PREFIX}/bess-0/grid-state", "OFF_GRID")
        snapshot = consumer.build_snapshot()
        assert snapshot.current_run_config == "UNKNOWN"


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — lugs
# ---------------------------------------------------------------------------


class TestHomieLugs:
    def test_upstream_lugs_to_main_meter(self):
        """Test typed lugs (energy.ebus.device.lugs.upstream) map to main meter."""
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/lugs-upstream/active-power", "5000.0")
        consumer.handle_message(f"{PREFIX}/lugs-upstream/imported-energy", "100000.0")
        consumer.handle_message(f"{PREFIX}/lugs-upstream/exported-energy", "5000.0")

        snapshot = consumer.build_snapshot()
        assert snapshot.instant_grid_power_w == 5000.0
        # imported-energy = consumed from grid, exported-energy = produced (solar)
        assert snapshot.main_meter_energy_consumed_wh == 100000.0
        assert snapshot.main_meter_energy_produced_wh == 5000.0

    def test_downstream_lugs_to_feedthrough(self):
        """Test typed lugs (energy.ebus.device.lugs.downstream) map to feedthrough."""
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/lugs-downstream/active-power", "1000.0")
        consumer.handle_message(f"{PREFIX}/lugs-downstream/imported-energy", "50000.0")
        consumer.handle_message(f"{PREFIX}/lugs-downstream/exported-energy", "1000.0")

        snapshot = consumer.build_snapshot()
        assert snapshot.feedthrough_power_w == 1000.0
        assert snapshot.feedthrough_energy_consumed_wh == 50000.0
        assert snapshot.feedthrough_energy_produced_wh == 1000.0

    def test_generic_lugs_with_direction_property(self):
        """Test fallback: generic TYPE_LUGS + direction property."""
        consumer = _build_ready_consumer(
            {
                "core": {"type": TYPE_CORE},
                "upstream-lugs": {"type": TYPE_LUGS},
                "downstream-lugs": {"type": TYPE_LUGS},
                "bess-0": {"type": TYPE_BESS},
            }
        )
        consumer.handle_message(f"{PREFIX}/upstream-lugs/direction", "UPSTREAM")
        consumer.handle_message(f"{PREFIX}/upstream-lugs/active-power", "800.0")
        consumer.handle_message(f"{PREFIX}/upstream-lugs/imported-energy", "90000.0")
        consumer.handle_message(f"{PREFIX}/upstream-lugs/exported-energy", "3000.0")

        consumer.handle_message(f"{PREFIX}/downstream-lugs/direction", "DOWNSTREAM")
        consumer.handle_message(f"{PREFIX}/downstream-lugs/active-power", "200.0")
        consumer.handle_message(f"{PREFIX}/downstream-lugs/imported-energy", "40000.0")
        consumer.handle_message(f"{PREFIX}/downstream-lugs/exported-energy", "500.0")

        snapshot = consumer.build_snapshot()
        assert snapshot.instant_grid_power_w == 800.0
        assert snapshot.main_meter_energy_consumed_wh == 90000.0
        assert snapshot.main_meter_energy_produced_wh == 3000.0
        assert snapshot.feedthrough_power_w == 200.0
        assert snapshot.feedthrough_energy_consumed_wh == 40000.0
        assert snapshot.feedthrough_energy_produced_wh == 500.0


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — battery
# ---------------------------------------------------------------------------


class TestHomieBattery:
    def test_battery_soc_soe(self):
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/bess-0/soc", "85.5")
        consumer.handle_message(f"{PREFIX}/bess-0/soe", "10.2")

        snapshot = consumer.build_snapshot()
        assert snapshot.battery.soe_percentage == 85.5
        assert snapshot.battery.soe_kwh == 10.2

    def test_no_battery_node(self):
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}})
        snapshot = consumer.build_snapshot()
        assert snapshot.battery.soe_percentage is None
        assert snapshot.battery.soe_kwh is None

    def test_battery_metadata(self):
        """BESS metadata properties are parsed into the battery snapshot."""
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/bess-0/soc", "85.0")
        consumer.handle_message(f"{PREFIX}/bess-0/vendor-name", "Tesla")
        consumer.handle_message(f"{PREFIX}/bess-0/product-name", "Powerwall 3")
        consumer.handle_message(f"{PREFIX}/bess-0/nameplate-capacity", "13.5")

        snapshot = consumer.build_snapshot()
        assert snapshot.battery.vendor_name == "Tesla"
        assert snapshot.battery.product_name == "Powerwall 3"
        assert snapshot.battery.nameplate_capacity_kwh == 13.5

    def test_battery_metadata_absent(self):
        """BESS node without metadata properties has None values."""
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/bess-0/soc", "50.0")

        snapshot = consumer.build_snapshot()
        assert snapshot.battery.soe_percentage == 50.0
        assert snapshot.battery.vendor_name is None
        assert snapshot.battery.product_name is None
        assert snapshot.battery.nameplate_capacity_kwh is None


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — PV metadata
# ---------------------------------------------------------------------------


class TestHomiePVMetadata:
    def test_pv_metadata_parsed(self):
        """PV metadata properties are parsed into the pv snapshot."""
        consumer = _build_ready_consumer(
            {
                "core": {"type": TYPE_CORE},
                "bess-0": {"type": TYPE_BESS},
                "pv-0": {"type": TYPE_PV},
            }
        )
        consumer.handle_message(f"{PREFIX}/pv-0/vendor-name", "Enphase")
        consumer.handle_message(f"{PREFIX}/pv-0/product-name", "IQ8+")
        consumer.handle_message(f"{PREFIX}/pv-0/nameplate-capacity", "3960")

        snapshot = consumer.build_snapshot()
        assert snapshot.pv.vendor_name == "Enphase"
        assert snapshot.pv.product_name == "IQ8+"
        assert snapshot.pv.nameplate_capacity_kw == 3960.0

    def test_no_pv_node(self):
        """Without PV node, pv snapshot has None values."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}})
        snapshot = consumer.build_snapshot()
        assert snapshot.pv.vendor_name is None
        assert snapshot.pv.product_name is None
        assert snapshot.pv.nameplate_capacity_kw is None

    def test_pv_metadata_partial(self):
        """PV node with only some properties populated."""
        consumer = _build_ready_consumer(
            {
                "core": {"type": TYPE_CORE},
                "pv-0": {"type": TYPE_PV},
            }
        )
        consumer.handle_message(f"{PREFIX}/pv-0/vendor-name", "Other")

        snapshot = consumer.build_snapshot()
        assert snapshot.pv.vendor_name == "Other"
        assert snapshot.pv.product_name is None
        assert snapshot.pv.nameplate_capacity_kw is None


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — power flows
# ---------------------------------------------------------------------------


class TestHomiePowerFlows:
    def test_power_flows_parsed(self):
        """Power-flows node properties map to snapshot fields."""
        consumer = _build_ready_consumer(
            {
                "core": {"type": TYPE_CORE},
                "power-flows": {"type": TYPE_POWER_FLOWS},
                "bess-0": {"type": TYPE_BESS},
            }
        )
        consumer.handle_message(f"{PREFIX}/power-flows/pv", "3500.0")
        consumer.handle_message(f"{PREFIX}/power-flows/battery", "-1200.0")
        consumer.handle_message(f"{PREFIX}/power-flows/grid", "800.0")
        consumer.handle_message(f"{PREFIX}/power-flows/site", "3100.0")

        snapshot = consumer.build_snapshot()
        assert snapshot.power_flow_pv == 3500.0
        assert snapshot.power_flow_battery == -1200.0
        assert snapshot.power_flow_grid == 800.0
        assert snapshot.power_flow_site == 3100.0

    def test_no_power_flows_node(self):
        """Without power-flows node, fields are None."""
        consumer = _build_ready_consumer({"core": {"type": TYPE_CORE}})
        snapshot = consumer.build_snapshot()
        assert snapshot.power_flow_pv is None
        assert snapshot.power_flow_battery is None
        assert snapshot.power_flow_grid is None
        assert snapshot.power_flow_site is None

    def test_partial_power_flows(self):
        """Only populated properties get values; others remain None."""
        consumer = _build_ready_consumer(
            {
                "core": {"type": TYPE_CORE},
                "power-flows": {"type": TYPE_POWER_FLOWS},
            }
        )
        consumer.handle_message(f"{PREFIX}/power-flows/battery", "500.0")

        snapshot = consumer.build_snapshot()
        assert snapshot.power_flow_battery == 500.0
        assert snapshot.power_flow_pv is None
        assert snapshot.power_flow_grid is None
        assert snapshot.power_flow_site is None


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — lugs per-phase current
# ---------------------------------------------------------------------------


class TestHomieLugsCurrent:
    def test_upstream_lugs_current(self):
        """l1-current and l2-current from upstream lugs map to snapshot."""
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/lugs-upstream/l1-current", "45.2")
        consumer.handle_message(f"{PREFIX}/lugs-upstream/l2-current", "42.8")

        snapshot = consumer.build_snapshot()
        assert snapshot.upstream_l1_current_a == 45.2
        assert snapshot.upstream_l2_current_a == 42.8

    def test_no_lugs_current(self):
        """Without l1/l2-current, fields are None."""
        consumer = _build_ready_consumer()
        snapshot = consumer.build_snapshot()
        assert snapshot.upstream_l1_current_a is None
        assert snapshot.upstream_l2_current_a is None


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — panel_size
# ---------------------------------------------------------------------------


class TestHomiePanelSize:
    def test_panel_size_parsed(self):
        consumer = _build_ready_consumer()
        consumer.handle_message(f"{PREFIX}/core/panel-size", "32")
        snapshot = consumer.build_snapshot()
        assert snapshot.panel_size == 32

    def test_panel_size_none_when_missing(self):
        consumer = _build_ready_consumer()
        snapshot = consumer.build_snapshot()
        assert snapshot.panel_size is None


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — snapshot structure
# ---------------------------------------------------------------------------


class TestHomieSnapshot:
    def test_serial_number_preserved(self):
        consumer = _build_ready_consumer()
        snapshot = consumer.build_snapshot()
        assert snapshot.serial_number == SERIAL

    def test_snapshot_immutable(self):
        consumer = _build_ready_consumer()
        snapshot = consumer.build_snapshot()
        with pytest.raises(AttributeError):
            snapshot.serial_number = "other"


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — property callbacks
# ---------------------------------------------------------------------------


class TestHomieCallbacks:
    def test_property_callback_fired(self):
        consumer = _build_ready_consumer()
        calls = []
        consumer.register_property_callback(lambda n, p, v, o: calls.append((n, p, v, o)))
        consumer.handle_message(f"{PREFIX}/core/door", "OPEN")
        assert len(calls) == 1
        assert calls[0] == ("core", "door", "OPEN", None)

    def test_property_callback_with_old_value(self):
        consumer = _build_ready_consumer()
        calls = []
        consumer.register_property_callback(lambda n, p, v, o: calls.append((n, p, v, o)))
        consumer.handle_message(f"{PREFIX}/core/door", "CLOSED")
        consumer.handle_message(f"{PREFIX}/core/door", "OPEN")
        assert calls[1] == ("core", "door", "OPEN", "CLOSED")

    def test_unregister_callback(self):
        consumer = _build_ready_consumer()
        calls = []
        unregister = consumer.register_property_callback(lambda n, p, v, o: calls.append(1))
        consumer.handle_message(f"{PREFIX}/core/door", "OPEN")
        unregister()
        consumer.handle_message(f"{PREFIX}/core/door", "CLOSED")
        assert len(calls) == 1

    def test_callback_error_doesnt_crash(self):
        consumer = _build_ready_consumer()

        def bad_cb(n, p, v, o):
            raise ValueError("boom")

        consumer.register_property_callback(bad_cb)
        # Should not raise
        consumer.handle_message(f"{PREFIX}/core/door", "OPEN")


# ---------------------------------------------------------------------------
# SpanMqttClient — protocol compliance
# ---------------------------------------------------------------------------


class TestSpanMqttClientProtocol:
    def test_implements_panel_protocol(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)
        assert isinstance(client, SpanPanelClientProtocol)

    def test_implements_circuit_control(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)
        assert isinstance(client, CircuitControlProtocol)

    def test_implements_streaming(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)
        assert isinstance(client, StreamingCapableProtocol)

    def test_capabilities(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)
        caps = client.capabilities
        assert PanelCapability.EBUS_MQTT in caps
        assert PanelCapability.PUSH_STREAMING in caps
        assert PanelCapability.CIRCUIT_CONTROL in caps
        assert PanelCapability.BATTERY_SOE in caps
        assert PanelCapability.EBUS_MQTT in caps  # MQTT-only transport

    def test_serial_number(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)
        assert client.serial_number == SERIAL


# ---------------------------------------------------------------------------
# SpanMqttClient — relay and priority control
# ---------------------------------------------------------------------------


class TestSpanMqttClientControl:
    @pytest.mark.asyncio
    async def test_set_circuit_relay_publishes(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

        mock_bridge = MagicMock()
        client._bridge = mock_bridge

        await client.set_circuit_relay("aabbccdd112233445566778899001122", "OPEN")

        mock_bridge.publish.assert_called_once_with(
            f"{TOPIC_PREFIX}/{SERIAL}/aabbccdd112233445566778899001122/relay/set",
            "OPEN",
            qos=1,
        )

    @pytest.mark.asyncio
    async def test_set_circuit_priority_publishes(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

        mock_bridge = MagicMock()
        client._bridge = mock_bridge

        await client.set_circuit_priority("aabbccdd112233445566778899001122", "NEVER")

        mock_bridge.publish.assert_called_once_with(
            f"{TOPIC_PREFIX}/{SERIAL}/aabbccdd112233445566778899001122/shed-priority/set",
            "NEVER",
            qos=1,
        )


# ---------------------------------------------------------------------------
# SpanMqttClient — snapshot and ping
# ---------------------------------------------------------------------------


class TestSpanMqttClientSnapshot:
    @pytest.mark.asyncio
    async def test_get_snapshot_returns_homie_state(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

        # Manually ready the homie consumer
        client._homie.handle_message(f"{PREFIX}/$state", "ready")
        client._homie.handle_message(f"{PREFIX}/$description", _make_description(_core_description()))
        client._homie.handle_message(f"{PREFIX}/core/software-version", "test-fw")

        snapshot = await client.get_snapshot()
        assert snapshot.serial_number == SERIAL
        assert snapshot.firmware_version == "test-fw"

    @pytest.mark.asyncio
    async def test_ping_false_no_bridge(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)
        assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_ping_true_when_connected_and_ready(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

        mock_bridge = MagicMock()
        mock_bridge.is_connected.return_value = True
        client._bridge = mock_bridge

        client._homie.handle_message(f"{PREFIX}/$state", "ready")
        client._homie.handle_message(f"{PREFIX}/$description", _make_description(_core_description()))

        assert await client.ping() is True


# ---------------------------------------------------------------------------
# SpanMqttClient — streaming callbacks
# ---------------------------------------------------------------------------


class TestSpanMqttClientStreaming:
    @pytest.mark.asyncio
    async def test_register_and_unregister_snapshot_callback(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

        callback = AsyncMock()
        unregister = client.register_snapshot_callback(callback)
        assert len(client._snapshot_callbacks) == 1
        unregister()
        assert len(client._snapshot_callbacks) == 0

    @pytest.mark.asyncio
    async def test_start_stop_streaming(self):
        from span_panel_api.mqtt.client import SpanMqttClient

        config = MqttClientConfig(broker_host="h", username="u", password="p")
        client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

        assert client._streaming is False
        await client.start_streaming()
        assert client._streaming is True
        await client.stop_streaming()
        assert client._streaming is False


# ---------------------------------------------------------------------------
# AsyncMqttBridge — construction
# ---------------------------------------------------------------------------


class TestAsyncMqttBridge:
    def test_construction(self):
        from span_panel_api.mqtt.connection import AsyncMqttBridge

        bridge = AsyncMqttBridge(
            host="broker.local",
            port=8883,
            username="user",
            password="pass",
            panel_host="192.168.1.1",
            serial_number=SERIAL,
        )
        assert bridge.is_connected() is False

    def test_callbacks_default_none(self):
        from span_panel_api.mqtt.connection import AsyncMqttBridge

        bridge = AsyncMqttBridge(
            host="broker.local",
            port=8883,
            username="user",
            password="pass",
            panel_host="192.168.1.1",
            serial_number=SERIAL,
        )
        assert bridge._message_callback is None
        assert bridge._connection_callback is None

    def test_set_message_callback(self):
        from span_panel_api.mqtt.connection import AsyncMqttBridge

        bridge = AsyncMqttBridge(
            host="broker.local",
            port=8883,
            username="user",
            password="pass",
            panel_host="192.168.1.1",
            serial_number=SERIAL,
        )
        cb = MagicMock()
        bridge.set_message_callback(cb)
        assert bridge._message_callback is cb


# ---------------------------------------------------------------------------
# Package exports and version
# ---------------------------------------------------------------------------


class TestPhase3Exports:
    def test_mqtt_exports(self):
        from span_panel_api.mqtt import AsyncMqttBridge, HomieDeviceConsumer, MqttClientConfig, SpanMqttClient

        assert AsyncMqttBridge is not None
        assert HomieDeviceConsumer is not None
        assert MqttClientConfig is not None
        assert SpanMqttClient is not None

    def test_top_level_exports(self):
        import span_panel_api

        assert hasattr(span_panel_api, "SpanMqttClient")
        assert hasattr(span_panel_api, "MqttClientConfig")

    def test_version_beta(self):
        import span_panel_api

        assert span_panel_api.__version__ == "2.0.0"


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — edge cases
# ---------------------------------------------------------------------------


class TestHomieEdgeCases:
    def test_invalid_description_json(self):
        consumer = HomieDeviceConsumer(SERIAL)
        consumer.handle_message(f"{PREFIX}/$state", "ready")
        consumer.handle_message(f"{PREFIX}/$description", "not-json{{{")
        assert not consumer.is_ready()

    def test_empty_property_values(self):
        """Circuit with no properties should still build with defaults."""
        consumer = _build_ready_consumer()
        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.name == ""
        assert circuit.relay_state == "UNKNOWN"
        assert circuit.instant_power_w == 0.0
        assert circuit.tabs == []

    def test_multiple_circuits(self):
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
            "bbbbbbbb-5555-6666-7777-888888888888": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/name", "Circuit A")
        consumer.handle_message(f"{PREFIX}/bbbbbbbb-5555-6666-7777-888888888888/name", "Circuit B")

        snapshot = consumer.build_snapshot()
        assert len(snapshot.circuits) == 2
        assert snapshot.circuits["aaaaaaaa11112222333344444444444" + "4"].name == "Circuit A"
        assert snapshot.circuits["bbbbbbbb55556666777788888888888" + "8"].name == "Circuit B"

    def test_current_and_breaker_none_when_empty(self):
        consumer = _build_ready_consumer()
        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aabbccdd112233445566778899001122"]
        assert circuit.current_a is None
        assert circuit.breaker_rating_a is None


# ---------------------------------------------------------------------------
# HomieDeviceConsumer — unmapped tab synthesis
# ---------------------------------------------------------------------------


class TestUnmappedTabSynthesis:
    """Tests for _build_unmapped_tabs and dipole tab derivation."""

    def test_single_pole_tabs(self):
        """Single-pole circuit gets tabs = [space]."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        node = "aaaaaaaa-1111-2222-3333-444444444444"
        consumer.handle_message(f"{PREFIX}/{node}/space", "3")
        consumer.handle_message(f"{PREFIX}/{node}/dipole", "false")

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aaaaaaaa111122223333444444444444"]
        assert circuit.tabs == [3]

    def test_dipole_tabs(self):
        """Dipole circuit gets tabs = [space, space + 2] (same bus bar side)."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        node = "aaaaaaaa-1111-2222-3333-444444444444"
        consumer.handle_message(f"{PREFIX}/{node}/space", "11")
        consumer.handle_message(f"{PREFIX}/{node}/dipole", "true")

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aaaaaaaa111122223333444444444444"]
        assert circuit.tabs == [11, 13]

    def test_dipole_even_side(self):
        """Dipole on even bus bar: [30, 32]."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        node = "aaaaaaaa-1111-2222-3333-444444444444"
        consumer.handle_message(f"{PREFIX}/{node}/space", "30")
        consumer.handle_message(f"{PREFIX}/{node}/dipole", "true")

        snapshot = consumer.build_snapshot()
        circuit = snapshot.circuits["aaaaaaaa111122223333444444444444"]
        assert circuit.tabs == [30, 32]

    def test_unmapped_tabs_generated(self):
        """Unmapped positions should produce synthetic circuit entries."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
            "bbbbbbbb-5555-6666-7777-888888888888": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        # Circuit A at space 1 (single-pole)
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/space", "1")
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/dipole", "false")
        # Circuit B at space 3 (dipole → occupies 3 and 5)
        consumer.handle_message(f"{PREFIX}/bbbbbbbb-5555-6666-7777-888888888888/space", "3")
        consumer.handle_message(f"{PREFIX}/bbbbbbbb-5555-6666-7777-888888888888/dipole", "true")

        snapshot = consumer.build_snapshot()

        # Highest occupied tab is 5 (odd), panel_size rounds to 6
        # Occupied: {1, 3, 5}, unmapped: {2, 4, 6}
        assert "unmapped_tab_2" in snapshot.circuits
        assert "unmapped_tab_4" in snapshot.circuits
        assert "unmapped_tab_6" in snapshot.circuits
        # Occupied positions should NOT have unmapped entries
        assert "unmapped_tab_1" not in snapshot.circuits
        assert "unmapped_tab_3" not in snapshot.circuits
        assert "unmapped_tab_5" not in snapshot.circuits

    def test_unmapped_tab_properties(self):
        """Unmapped tab entries have zero power/energy and correct attributes."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/space", "1")
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/dipole", "false")

        snapshot = consumer.build_snapshot()
        unmapped = snapshot.circuits["unmapped_tab_2"]

        assert unmapped.circuit_id == "unmapped_tab_2"
        assert unmapped.name == "Unmapped Tab 2"
        assert unmapped.relay_state == "CLOSED"
        assert unmapped.instant_power_w == 0.0
        assert unmapped.produced_energy_wh == 0.0
        assert unmapped.consumed_energy_wh == 0.0
        assert unmapped.tabs == [2]
        assert unmapped.priority == "UNKNOWN"
        assert unmapped.is_user_controllable is False
        assert unmapped.is_sheddable is False
        assert unmapped.is_never_backup is False

    def test_fully_occupied_panel_no_unmapped(self):
        """When all positions are occupied, no unmapped tabs are generated."""
        # Create 4 circuits occupying spaces 1, 2, 3, 4
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
            "bbbbbbbb-5555-6666-7777-888888888888": {"type": TYPE_CIRCUIT},
            "cccccccc-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
            "dddddddd-5555-6666-7777-888888888888": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        for i, node in enumerate(
            [
                "aaaaaaaa-1111-2222-3333-444444444444",
                "bbbbbbbb-5555-6666-7777-888888888888",
                "cccccccc-1111-2222-3333-444444444444",
                "dddddddd-5555-6666-7777-888888888888",
            ],
            start=1,
        ):
            consumer.handle_message(f"{PREFIX}/{node}/space", str(i))
            consumer.handle_message(f"{PREFIX}/{node}/dipole", "false")

        snapshot = consumer.build_snapshot()
        unmapped_ids = [cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")]
        assert unmapped_ids == []

    def test_no_circuits_no_unmapped(self):
        """When no circuits exist, no unmapped tabs are generated."""
        nodes = {"core": {"type": TYPE_CORE}}
        consumer = _build_ready_consumer(nodes)
        snapshot = consumer.build_snapshot()
        unmapped_ids = [cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")]
        assert unmapped_ids == []

    def test_no_space_property_no_unmapped(self):
        """Circuits without space property don't contribute to tab tracking."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        # Don't set space property
        snapshot = consumer.build_snapshot()
        unmapped_ids = [cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")]
        assert unmapped_ids == []

    def test_panel_size_rounds_to_even(self):
        """Panel size rounds up to even when highest tab is odd."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/space", "7")
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/dipole", "false")

        snapshot = consumer.build_snapshot()
        # Highest tab=7 (odd) → panel_size=8
        # Occupied: {7}, unmapped: {1,2,3,4,5,6,8}
        unmapped_ids = sorted(cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_"))
        assert unmapped_ids == [
            "unmapped_tab_1",
            "unmapped_tab_2",
            "unmapped_tab_3",
            "unmapped_tab_4",
            "unmapped_tab_5",
            "unmapped_tab_6",
            "unmapped_tab_8",
        ]

    def test_panel_size_exact_when_even(self):
        """Panel size stays as-is when highest tab is even."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/space", "6")
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/dipole", "false")

        snapshot = consumer.build_snapshot()
        # Highest tab=6 (even) → panel_size=6
        # Occupied: {6}, unmapped: {1,2,3,4,5}
        unmapped_ids = sorted(cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_"))
        assert unmapped_ids == [
            "unmapped_tab_1",
            "unmapped_tab_2",
            "unmapped_tab_3",
            "unmapped_tab_4",
            "unmapped_tab_5",
        ]

    def test_dipole_occupies_correct_tabs_in_unmapped_calc(self):
        """Dipole circuits remove both occupied tabs from unmapped set."""
        nodes = {
            "core": {"type": TYPE_CORE},
            "aaaaaaaa-1111-2222-3333-444444444444": {"type": TYPE_CIRCUIT},
        }
        consumer = _build_ready_consumer(nodes)
        # Dipole at space 1 → occupies 1 and 3
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/space", "1")
        consumer.handle_message(f"{PREFIX}/aaaaaaaa-1111-2222-3333-444444444444/dipole", "true")

        snapshot = consumer.build_snapshot()
        # Highest tab = 3 (odd) → panel_size = 4
        # Occupied: {1, 3}, unmapped: {2, 4}
        assert "unmapped_tab_1" not in snapshot.circuits
        assert "unmapped_tab_2" in snapshot.circuits
        assert "unmapped_tab_3" not in snapshot.circuits
        assert "unmapped_tab_4" in snapshot.circuits
