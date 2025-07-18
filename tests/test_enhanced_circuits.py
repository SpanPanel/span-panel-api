"""
Test the enhanced get_circuits method with unmapped tab virtual circuits.
"""

from unittest.mock import Mock, patch

import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.generated_client.models import Branch, Circuit, CircuitsOut, CircuitsOutCircuits, Priority, RelayState


class TestEnhancedCircuits:
    """Test enhanced circuits functionality."""

    @pytest.mark.asyncio
    async def test_get_circuits_with_virtual_circuits(self):
        """Test get_circuits includes virtual circuits for unmapped tabs."""
        client = SpanPanelClient(host="test-panel")
        client.set_access_token("test-token")

        # Mock the real circuits response
        mock_circuit = Circuit(
            id="real_circuit_1",
            name="Kitchen Outlet",
            relay_state=RelayState.CLOSED,
            instant_power_w=100.0,
            instant_power_update_time_s=1234567890,
            produced_energy_wh=1000.0,
            consumed_energy_wh=2000.0,
            energy_accum_update_time_s=1234567890,
            priority=Priority.MUST_HAVE,
            is_user_controllable=True,
            is_sheddable=False,
            is_never_backup=False,
            tabs=[1, 2],  # This circuit uses tabs 1 and 2
        )

        mock_circuits = CircuitsOutCircuits()
        mock_circuits.additional_properties = {"real_circuit_1": mock_circuit}

        mock_circuits_response = CircuitsOut(circuits=mock_circuits)

        # Mock the panel state with branches
        mock_branch1 = Branch(
            id=1,
            relay_state=RelayState.CLOSED,
            instant_power_w=50.0,
            imported_active_energy_wh=1500.0,
            exported_active_energy_wh=500.0,
            measure_start_ts_ms=1234567890000,
            measure_duration_ms=1000,
            is_measure_valid=True,
        )
        mock_branch2 = Branch(
            id=2,
            relay_state=RelayState.CLOSED,
            instant_power_w=75.0,
            imported_active_energy_wh=2500.0,
            exported_active_energy_wh=300.0,
            measure_start_ts_ms=1234567890000,
            measure_duration_ms=1000,
            is_measure_valid=True,
        )
        mock_branch3 = Branch(
            id=3,
            relay_state=RelayState.OPEN,
            instant_power_w=200.0,  # This looks like solar production
            imported_active_energy_wh=3360000.0,  # 3.36M Wh - major solar inverter
            exported_active_energy_wh=100000.0,
            measure_start_ts_ms=1234567890000,
            measure_duration_ms=1000,
            is_measure_valid=True,
        )
        mock_branch4 = Branch(
            id=4,
            relay_state=RelayState.OPEN,
            instant_power_w=190.0,  # This looks like solar production
            imported_active_energy_wh=3350000.0,  # 3.35M Wh - major solar inverter
            exported_active_energy_wh=95000.0,
            measure_start_ts_ms=1234567890000,
            measure_duration_ms=1000,
            is_measure_valid=True,
        )

        # Mock panel state - just create a mock object with branches attribute
        mock_panel_state = Mock()
        mock_panel_state.branches = [mock_branch1, mock_branch2, mock_branch3, mock_branch4]

        with patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_get_circuits:
            # Mock the async function to return an awaitable
            async def mock_async_circuits(*args, **kwargs):
                return mock_circuits_response

            mock_get_circuits.asyncio = mock_async_circuits

            # Mock get_panel_state to return an awaitable
            async def mock_async_panel_state():
                return mock_panel_state

            with patch.object(client, "get_panel_state", side_effect=mock_async_panel_state):
                async with client:
                    result = await client.get_circuits()

                    # Verify we got the response
                    assert result is not None
                    assert hasattr(result, "circuits")
                    assert hasattr(result.circuits, "additional_properties")

                    circuit_dict = result.circuits.additional_properties

                    # Should have 1 real circuit + 2 virtual circuits (tabs 3 and 4 are unmapped)
                    assert len(circuit_dict) == 3

                    # Verify real circuit is still there
                    assert "real_circuit_1" in circuit_dict
                    real_circuit = circuit_dict["real_circuit_1"]
                    assert real_circuit.name == "Kitchen Outlet"
                    assert real_circuit.tabs == [1, 2]

                    # Verify virtual circuits were created for unmapped tabs
                    assert "unmapped_tab_3" in circuit_dict
                    assert "unmapped_tab_4" in circuit_dict

                    virtual_circuit_3 = circuit_dict["unmapped_tab_3"]
                    virtual_circuit_4 = circuit_dict["unmapped_tab_4"]

                    # Verify virtual circuit 3 structure
                    assert virtual_circuit_3.id == "unmapped_tab_3"
                    assert virtual_circuit_3.name == "Unmapped Tab 3"
                    assert virtual_circuit_3.relay_state == RelayState.UNKNOWN
                    assert virtual_circuit_3.instant_power_w == 200.0
                    assert virtual_circuit_3.produced_energy_wh == 3360000.0  # Solar production
                    assert virtual_circuit_3.consumed_energy_wh == 100000.0
                    assert virtual_circuit_3.priority == Priority.UNKNOWN
                    assert virtual_circuit_3.is_user_controllable is False
                    assert virtual_circuit_3.is_sheddable is False
                    assert virtual_circuit_3.tabs == [3]

                    # Verify virtual circuit 4 structure
                    assert virtual_circuit_4.id == "unmapped_tab_4"
                    assert virtual_circuit_4.name == "Unmapped Tab 4"
                    assert virtual_circuit_4.instant_power_w == 190.0
                    assert virtual_circuit_4.produced_energy_wh == 3350000.0  # Solar production
                    assert virtual_circuit_4.consumed_energy_wh == 95000.0
                    assert virtual_circuit_4.tabs == [4]

    @pytest.mark.asyncio
    async def test_get_circuits_no_unmapped_tabs(self):
        """Test get_circuits when all tabs are mapped to circuits."""
        client = SpanPanelClient(host="test-panel")
        client.set_access_token("test-token")

        # Mock circuit that uses all available tabs
        mock_circuit = Circuit(
            id="real_circuit_1",
            name="Main Circuit",
            relay_state=RelayState.CLOSED,
            instant_power_w=100.0,
            instant_power_update_time_s=1234567890,
            produced_energy_wh=1000.0,
            consumed_energy_wh=2000.0,
            energy_accum_update_time_s=1234567890,
            priority=Priority.MUST_HAVE,
            is_user_controllable=True,
            is_sheddable=False,
            is_never_backup=False,
            tabs=[1, 2],  # Uses all tabs
        )

        mock_circuits = CircuitsOutCircuits()
        mock_circuits.additional_properties = {"real_circuit_1": mock_circuit}

        mock_circuits_response = CircuitsOut(circuits=mock_circuits)

        # Mock panel state with only 2 branches (all mapped)
        mock_branch1 = Branch(
            id=1,
            relay_state=RelayState.CLOSED,
            instant_power_w=50.0,
            imported_active_energy_wh=1500.0,
            exported_active_energy_wh=500.0,
            measure_start_ts_ms=1234567890000,
            measure_duration_ms=1000,
            is_measure_valid=True,
        )
        mock_branch2 = Branch(
            id=2,
            relay_state=RelayState.CLOSED,
            instant_power_w=75.0,
            imported_active_energy_wh=2500.0,
            exported_active_energy_wh=300.0,
            measure_start_ts_ms=1234567890000,
            measure_duration_ms=1000,
            is_measure_valid=True,
        )

        # Mock panel state - just create a mock object with branches attribute
        mock_panel_state = Mock()
        mock_panel_state.branches = [mock_branch1, mock_branch2]

        with patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_get_circuits:
            # Mock the async function to return an awaitable
            async def mock_async_circuits(*args, **kwargs):
                return mock_circuits_response

            mock_get_circuits.asyncio = mock_async_circuits

            # Mock get_panel_state to return an awaitable
            async def mock_async_panel_state():
                return mock_panel_state

            with patch.object(client, "get_panel_state", side_effect=mock_async_panel_state):
                async with client:
                    result = await client.get_circuits()

                    # Should only have the original circuit, no virtual ones
                    circuit_dict = result.circuits.additional_properties
                    assert len(circuit_dict) == 1
                    assert "real_circuit_1" in circuit_dict
                    # No virtual circuits should be created
                    assert not any(cid.startswith("unmapped_tab_") for cid in circuit_dict)

    def test_create_unmapped_tab_circuit(self):
        """Test the _create_unmapped_tab_circuit method."""
        client = SpanPanelClient(host="test-panel")

        # Mock branch data
        mock_branch = Branch(
            id=30,
            relay_state=RelayState.OPEN,
            instant_power_w=250.0,
            imported_active_energy_wh=3400000.0,  # 3.4M Wh
            exported_active_energy_wh=120000.0,
            measure_start_ts_ms=1234567890000,
            measure_duration_ms=1000,
            is_measure_valid=True,
        )

        # Create virtual circuit
        circuit = client._create_unmapped_tab_circuit(mock_branch, 30)

        # Verify all circuit properties
        assert circuit.id == "unmapped_tab_30"
        assert circuit.name == "Unmapped Tab 30"
        assert circuit.relay_state == RelayState.UNKNOWN
        assert circuit.instant_power_w == 250.0
        assert circuit.produced_energy_wh == 3400000.0  # imported energy -> production
        assert circuit.consumed_energy_wh == 120000.0  # exported energy -> consumption
        assert circuit.priority == Priority.UNKNOWN
        assert circuit.is_user_controllable is False
        assert circuit.is_sheddable is False
        assert circuit.is_never_backup is False
        assert circuit.tabs == [30]

        # Verify timestamps are set (should be recent)
        import time

        current_time = int(time.time())
        assert abs(circuit.instant_power_update_time_s - current_time) < 5
        assert abs(circuit.energy_accum_update_time_s - current_time) < 5

    def test_create_unmapped_tab_circuit_missing_attributes(self):
        """Test _create_unmapped_tab_circuit with missing branch attributes."""
        client = SpanPanelClient(host="test-panel")

        # Create a mock branch where getattr will return None for missing attributes
        # and our code will use the default value
        mock_branch = Mock(spec=Branch)
        # Clear all attributes so getattr returns None
        del mock_branch.instant_power_w
        del mock_branch.imported_active_energy_wh
        del mock_branch.exported_active_energy_wh

        circuit = client._create_unmapped_tab_circuit(mock_branch, 15)

        # Should use default values
        assert circuit.id == "unmapped_tab_15"
        assert circuit.name == "Unmapped Tab 15"
        assert circuit.instant_power_w == 0.0
        assert circuit.produced_energy_wh == 0.0
        assert circuit.consumed_energy_wh == 0.0
        assert circuit.tabs == [15]

    @pytest.mark.asyncio
    async def test_create_unmapped_tab_circuit_coverage(self):
        """Test the _create_unmapped_tab_circuit method coverage (from missing coverage)."""
        from unittest.mock import MagicMock

        from tests.test_factories import create_live_client

        client = create_live_client(cache_window=0)
        client.set_access_token("test-token")

        # Create a mock branch for testing
        mock_branch = MagicMock()
        mock_branch.id = 1
        mock_branch.relay_state = "CLOSED"

        # Test the _create_unmapped_tab_circuit method
        circuit = client._create_unmapped_tab_circuit(mock_branch, 2)

        assert circuit.id == "unmapped_tab_2"
        assert circuit.name == "Unmapped Tab 2"
        assert circuit.tabs == [2]
        assert circuit.relay_state.value == "UNKNOWN"  # Default value for unmapped circuits
        assert circuit.priority.value == "UNKNOWN"
        assert circuit.is_user_controllable is False

    @pytest.mark.asyncio
    async def test_get_circuits_unmapped_tabs_simulation_edge_case(self):
        """Test unmapped tab handling edge case in simulation where tab index exceeds branches."""
        client = SpanPanelClient(
            host="test-unmapped-edge-case", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # Get circuits normally first
            circuits = await client.get_circuits()

            # All configured circuits should be present plus any unmapped tabs
            assert len(circuits.circuits.additional_properties) >= 4  # At least the 4 configured circuits

            # Verify circuit IDs exist
            circuit_ids = list(circuits.circuits.additional_properties.keys())
            expected_ids = ["living_room_lights", "kitchen_outlets", "main_hvac", "solar_inverter"]

            for expected_id in expected_ids:
                assert expected_id in circuit_ids, f"Expected circuit {expected_id} not found in {circuit_ids}"

            # If there are unmapped tabs, they should follow the correct naming pattern
            unmapped_circuits = [cid for cid in circuit_ids if cid.startswith("unmapped_tab_")]
            for unmapped_id in unmapped_circuits:
                # Should be in format unmapped_tab_N where N is a valid tab number
                tab_num = int(unmapped_id.split("_")[-1])
                assert tab_num > 0, f"Invalid tab number in {unmapped_id}"

    @pytest.mark.asyncio
    async def test_get_circuits_handles_circuit_tabs_as_single_int(self):
        """Test that circuits with single int tabs (not list) are handled correctly."""
        # This test ensures coverage of the isinstance(circuit.tabs, int) branch
        client = SpanPanelClient(
            host="test-single-int-tabs", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # Use the simulation directly to trigger the edge case logic
            circuits = await client.get_circuits()

            # This will exercise the unmapped tab logic including bounds checking
            circuit_ids = list(circuits.circuits.additional_properties.keys())

            # Verify the expected simulation circuits exist
            expected_ids = ["living_room_lights", "kitchen_outlets", "main_hvac", "solar_inverter"]
            for expected_id in expected_ids:
                assert expected_id in circuit_ids
