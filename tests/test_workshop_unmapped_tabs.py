"""Test workshop panel unmapped tab detection with realistic circuits."""

import pytest
from span_panel_api import SpanPanelClient


@pytest.fixture
def workshop_client():
    """Create client with workshop simulation."""
    config_path = "examples/simulation_config_8_tab_workshop.yaml"
    return SpanPanelClient("workshop-host", simulation_mode=True, simulation_config_path=config_path)


class TestWorkshopUnmappedTabs:
    """Test unmapped tab detection in realistic workshop scenario."""

    @pytest.mark.asyncio
    async def test_workshop_circuit_assignments(self, workshop_client):
        """Test that workshop circuits are properly assigned."""
        circuits = await workshop_client.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Should have 8 circuits total: 4 assigned + 4 unmapped
        assert len(circuit_data) == 8

        # Verify assigned circuits
        expected_circuits = {
            "workshop_led_lighting": "Workshop LED Lighting",
            "table_saw_planer": "Table Saw & Planer",
            "power_tool_outlets": "Power Tool Outlets",
            "workshop_hvac": "Workshop HVAC",
        }

        for circuit_id, expected_name in expected_circuits.items():
            circuit = circuit_data[circuit_id]
            assert circuit.name == expected_name
            assert circuit.id == circuit_id

    @pytest.mark.asyncio
    async def test_workshop_unmapped_tabs_detection(self, workshop_client):
        """Test that tabs 5-8 are automatically detected as unmapped."""
        circuits = await workshop_client.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Find unmapped circuits (tabs 5-8)
        unmapped_circuit_ids = [
            cid for cid in circuit_data.keys() if isinstance(cid, str) and cid.startswith("unmapped_tab_")
        ]

        # Should have exactly 4 unmapped circuits
        assert len(unmapped_circuit_ids) == 4

        # Verify specific unmapped tabs
        expected_unmapped = ["unmapped_tab_5", "unmapped_tab_6", "unmapped_tab_7", "unmapped_tab_8"]
        for expected_id in expected_unmapped:
            assert expected_id in unmapped_circuit_ids

            # Check circuit details
            circuit = circuit_data[expected_id]
            expected_name = f"Unmapped Tab {expected_id.split('_')[-1]}"
            assert circuit.name == expected_name
            assert circuit.id == expected_id

    @pytest.mark.asyncio
    async def test_workshop_power_ranges(self, workshop_client):
        """Test that workshop circuits have realistic power consumption."""
        circuits = await workshop_client.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Test power ranges for each circuit type
        power_checks = {
            "workshop_led_lighting": (20.0, 80.0),  # Workshop LED Lighting
            "table_saw_planer": (0.0, 3500.0),  # Table Saw & Planer
            "power_tool_outlets": (0.0, 1800.0),  # Power Tool Outlets
            "workshop_hvac": (0.0, 2400.0),  # Workshop HVAC
        }

        for circuit_id, (min_power, max_power) in power_checks.items():
            circuit = circuit_data[circuit_id]
            power = circuit.instant_power_w
            assert (
                min_power <= power <= max_power
            ), f"Circuit {circuit_id} power {power}W outside range {min_power}-{max_power}W"

    @pytest.mark.asyncio
    async def test_workshop_panel_branches(self, workshop_client):
        """Test that panel state shows all 8 branches."""
        panel_state = await workshop_client.get_panel_state()

        # Should have 8 branches total
        assert len(panel_state.branches) == 8

        # All branches should have valid IDs
        for i, branch in enumerate(panel_state.branches, 1):
            assert branch.id == f"branch_{i}"

    @pytest.mark.asyncio
    async def test_workshop_circuit_control(self, workshop_client):
        """Test controlling both assigned and unmapped circuits."""
        circuits = await workshop_client.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Test controlling an assigned circuit (Table Saw)
        table_saw = circuit_data["table_saw_planer"]
        original_state = table_saw.relay_state
        new_state = "OPEN" if original_state == "CLOSED" else "CLOSED"

        result = await workshop_client.set_circuit_relay("table_saw_planer", new_state)
        assert result["status"] == "success"
        assert result["circuit_id"] == "table_saw_planer"
        assert result["relay_state"] == new_state

        # Test controlling an unmapped circuit
        unmapped_circuit = circuit_data["unmapped_tab_5"]
        original_state = unmapped_circuit.relay_state
        new_state = "OPEN" if original_state == "CLOSED" else "CLOSED"

        result = await workshop_client.set_circuit_relay("unmapped_tab_5", new_state)
        assert result["status"] == "success"
        assert result["circuit_id"] == "unmapped_tab_5"
        assert result["relay_state"] == new_state

    @pytest.mark.asyncio
    async def test_workshop_circuit_priorities(self, workshop_client):
        """Test that workshop circuits have appropriate priority levels."""
        circuits = await workshop_client.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Verify circuit priorities match workshop needs
        priority_checks = {
            "workshop_led_lighting": "MUST_HAVE",  # Workshop LED Lighting - essential
            "table_saw_planer": "NON_ESSENTIAL",  # Table Saw & Planer - can be shed
            "power_tool_outlets": "NON_ESSENTIAL",  # Power Tool Outlets - can be shed
            "workshop_hvac": "NICE_TO_HAVE",  # Workshop HVAC - comfort item
        }

        for circuit_id, expected_priority in priority_checks.items():
            circuit = circuit_data[circuit_id]
            assert circuit.priority.value == expected_priority
