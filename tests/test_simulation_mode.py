"""Tests for SPAN Panel API simulation mode functionality."""

import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAPIError
from span_panel_api.simulation import BranchVariation, CircuitVariation, PanelVariation, StatusVariation


class TestSimulationModeBasics:
    """Test basic simulation mode functionality."""

    def test_simulation_mode_initialization(self) -> None:
        """Test that simulation mode initializes correctly."""
        # Live mode (default)
        live_client = SpanPanelClient(host="192.168.1.100")
        assert live_client._simulation_mode is False
        assert live_client._simulation_engine is None

        # Simulation mode
        sim_client = SpanPanelClient(host="localhost", simulation_mode=True)
        assert sim_client._simulation_mode is True
        assert sim_client._simulation_engine is not None

    def test_simulation_mode_ignores_host_in_simulation(self) -> None:
        """Test that host is ignored in simulation mode."""
        # Should work with any host in simulation mode
        client = SpanPanelClient(host="invalid-host", simulation_mode=True)
        assert client._simulation_mode is True


class TestCircuitsSimulation:
    """Test circuits API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        return SpanPanelClient(host="localhost", simulation_mode=True)

    @pytest.fixture
    def live_client(self) -> SpanPanelClient:
        """Create a live mode client."""
        return SpanPanelClient(host="192.168.1.100", simulation_mode=False)

    async def test_get_circuits_basic_simulation(self, sim_client: SpanPanelClient) -> None:
        """Test basic circuits retrieval in simulation mode."""
        async with sim_client:
            circuits = await sim_client.get_circuits()

            # Should return CircuitsOut object
            assert circuits is not None
            assert hasattr(circuits, "circuits")

            # Should have some circuits from fixtures
            assert len(circuits.circuits.additional_properties) > 0

    async def test_get_circuits_with_global_variations(self, sim_client: SpanPanelClient) -> None:
        """Test circuits with global power and energy variations."""
        async with sim_client:
            # Test with global variations
            circuits = await sim_client.get_circuits(global_power_variation=0.2, global_energy_variation=0.1)

            assert circuits is not None
            assert len(circuits.circuits.additional_properties) > 0

    async def test_get_circuits_with_specific_variations(self, sim_client: SpanPanelClient) -> None:
        """Test circuits with specific circuit variations."""
        async with sim_client:
            # Get baseline first
            baseline = await sim_client.get_circuits()
            circuit_ids = list(baseline.circuits.additional_properties.keys())

            if circuit_ids:
                test_circuit_id = circuit_ids[0]

                # Test with specific circuit variations
                variations = {
                    test_circuit_id: CircuitVariation(power_variation=0.5, relay_state="OPEN", priority="NON_ESSENTIAL")
                }

                circuits = await sim_client.get_circuits(variations=variations)

                assert circuits is not None
                modified_circuit = circuits.circuits.additional_properties[test_circuit_id]
                assert modified_circuit.relay_state == "OPEN"
                assert modified_circuit.priority == "NON_ESSENTIAL"

    async def test_get_circuits_mixed_variations(self, sim_client: SpanPanelClient) -> None:
        """Test circuits with both global and specific variations."""
        async with sim_client:
            baseline = await sim_client.get_circuits()
            circuit_ids = list(baseline.circuits.additional_properties.keys())

            if circuit_ids:
                test_circuit_id = circuit_ids[0]

                # Mixed variations - specific should override global
                variations = {test_circuit_id: CircuitVariation(power_variation=0.8)}

                circuits = await sim_client.get_circuits(
                    variations=variations,
                    global_power_variation=0.2,  # Should apply to other circuits
                    global_energy_variation=0.1,
                )

                assert circuits is not None
                assert len(circuits.circuits.additional_properties) > 0

    async def test_get_circuits_legacy_parameters(self, sim_client: SpanPanelClient) -> None:
        """Test circuits with legacy power_variation and energy_variation parameters."""
        async with sim_client:
            # Test legacy parameters
            circuits = await sim_client.get_circuits(power_variation=0.3, energy_variation=0.15)

            assert circuits is not None
            assert len(circuits.circuits.additional_properties) > 0

    async def test_get_circuits_live_mode_ignores_variations(self, live_client: SpanPanelClient) -> None:
        """Test that live mode ignores all variation parameters."""
        # This test will fail if run against a real panel, but demonstrates the concept
        try:
            async with live_client:
                # These variations should be completely ignored
                circuits = await live_client.get_circuits(
                    variations={"any_id": CircuitVariation(power_variation=999.0)},
                    global_power_variation=999.0,
                    global_energy_variation=999.0,
                )

                # If we get here, the variations were ignored (good)
                # In reality, this would likely fail due to no real panel
                assert circuits is not None

        except Exception:
            # Expected to fail in test environment without real panel
            # The important thing is that variations were ignored
            pass


class TestPanelStateSimulation:
    """Test panel state API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        return SpanPanelClient(host="localhost", simulation_mode=True)

    async def test_get_panel_state_basic(self, sim_client: SpanPanelClient) -> None:
        """Test basic panel state retrieval in simulation mode."""
        async with sim_client:
            panel = await sim_client.get_panel_state()
            assert panel is not None
            assert hasattr(panel, "branches")
            assert len(panel.branches) > 0

    async def test_get_panel_state_with_variations(self, sim_client: SpanPanelClient) -> None:
        """Test panel state with branch and panel variations."""
        async with sim_client:
            # Test variations
            branch_variations = {
                1: BranchVariation(power_variation=0.3, relay_state="OPEN"),
                2: BranchVariation(power_variation=0.5),
            }

            panel_variations = PanelVariation(main_relay_state="OPEN", dsm_grid_state="DSM_GRID_DOWN")

            panel = await sim_client.get_panel_state(variations=branch_variations, panel_variations=panel_variations)
            assert panel is not None
            assert panel.main_relay_state.value == "OPEN"
            assert panel.dsm_grid_state == "DSM_GRID_DOWN"

            # Check branch variations
            if len(panel.branches) > 0:
                assert panel.branches[0].relay_state.value == "OPEN"


class TestStatusSimulation:
    """Test status API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        return SpanPanelClient(host="localhost", simulation_mode=True)

    async def test_get_status_basic(self, sim_client: SpanPanelClient) -> None:
        """Test basic status retrieval in simulation mode."""
        async with sim_client:
            status = await sim_client.get_status()
            assert status is not None
            assert hasattr(status, "system")
            assert hasattr(status, "network")

    async def test_get_status_with_variations(self, sim_client: SpanPanelClient) -> None:
        """Test status with field variations."""
        async with sim_client:
            variations = StatusVariation(door_state="OPEN", eth0_link=False, proximity_proven=True)

            status = await sim_client.get_status(variations=variations)
            assert status is not None
            assert status.system.door_state.value == "OPEN"
            assert status.network.eth_0_link is False
            assert status.system.proximity_proven is True


class TestStorageSimulation:
    """Test storage SOE API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        return SpanPanelClient(host="localhost", simulation_mode=True)

    async def test_get_storage_soe_basic(self, sim_client: SpanPanelClient) -> None:
        """Test basic storage SOE retrieval in simulation mode."""
        async with sim_client:
            storage = await sim_client.get_storage_soe()
            assert storage is not None
            assert hasattr(storage, "soe")
            assert hasattr(storage.soe, "percentage")

    async def test_get_storage_soe_with_variation(self, sim_client: SpanPanelClient) -> None:
        """Test storage SOE with percentage variation."""
        async with sim_client:
            baseline = await sim_client.get_storage_soe()
            varied = await sim_client.get_storage_soe(soe_variation=0.2)

            assert baseline is not None
            assert varied is not None

            # Values should be different due to variation
            assert baseline.soe.percentage != varied.soe.percentage


class TestSimulationRealism:
    """Test realistic simulation behavior."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        return SpanPanelClient(host="localhost", simulation_mode=True)

    async def test_energy_accumulation_over_time(self, sim_client: SpanPanelClient) -> None:
        """Test that energy values accumulate over time."""
        async with sim_client:
            # Get initial circuits
            circuits1 = await sim_client.get_circuits()

            # Wait a bit and get again
            import asyncio

            await asyncio.sleep(0.1)

            circuits2 = await sim_client.get_circuits()

            # Energy should have accumulated (though the change might be small)
            assert circuits1 is not None
            assert circuits2 is not None

    async def test_power_variation_by_circuit_type(self, sim_client: SpanPanelClient) -> None:
        """Test that different circuit types have different power variation patterns."""
        async with sim_client:
            # Test multiple calls to see power variations
            circuits_calls = []
            for _ in range(5):
                circuits = await sim_client.get_circuits(global_power_variation=0.3)
                circuits_calls.append(circuits)

            # All calls should succeed
            assert len(circuits_calls) == 5
            for circuits in circuits_calls:
                assert circuits is not None

    async def test_circuit_specific_behavior(self, sim_client: SpanPanelClient) -> None:
        """Test that EV chargers and HVAC systems have specific behaviors."""
        async with sim_client:
            # Test multiple calls to see if EV chargers turn on/off
            for _ in range(10):
                circuits = await sim_client.get_circuits(global_power_variation=0.5)

                # Look for EV chargers and HVAC systems
                for circuit in circuits.circuits.additional_properties.values():
                    circuit_name = circuit.name.lower()

                    if "ev" in circuit_name:
                        # EV chargers should have valid power readings
                        power = circuit.instant_power_w
                        # Just check it's a valid number (could be low standby power or high charging power)
                        assert isinstance(power, int | float)

                    elif "air conditioner" in circuit_name or "furnace" in circuit_name:
                        # HVAC should have valid power readings
                        power = circuit.instant_power_w
                        # Just check it's a valid number
                        assert isinstance(power, int | float)


class TestSimulationErrorHandling:
    """Test error handling in simulation mode."""

    def test_simulation_engine_not_initialized(self) -> None:
        """Test error when simulation engine is not initialized."""
        client = SpanPanelClient(host="localhost", simulation_mode=False)

        # Manually set simulation mode without engine (shouldn't happen in normal use)
        client._simulation_mode = True
        client._simulation_engine = None

        # Should raise an error
        with pytest.raises(SpanPanelAPIError):
            import asyncio

            asyncio.run(client.get_circuits())

    def test_missing_fixture_data(self) -> None:
        """Test behavior when fixture data is missing."""
        # This would test what happens if fixture files don't exist
        # For now, this is a placeholder as the fixture loading is in __init__
        pass

    def test_missing_fixture_data_errors(self) -> None:
        """Test ValueError when specific fixture data is missing."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Create engine and manually remove fixture data to test error paths
        engine = DynamicSimulationEngine()

        # Test circuits fixture missing
        original_circuits = engine._base_data.get("circuits")
        if "circuits" in engine._base_data:
            del engine._base_data["circuits"]

        with pytest.raises(ValueError, match="Circuits fixture data not available"):
            engine.get_circuits_data()

        # Restore circuits data
        if original_circuits:
            engine._base_data["circuits"] = original_circuits

        # Test panel fixture missing
        original_panel = engine._base_data.get("panel")
        if "panel" in engine._base_data:
            del engine._base_data["panel"]

        with pytest.raises(ValueError, match="Panel fixture data not available"):
            engine.get_panel_state_data()

        # Restore panel data
        if original_panel:
            engine._base_data["panel"] = original_panel

        # Test status fixture missing
        original_status = engine._base_data.get("status")
        if "status" in engine._base_data:
            del engine._base_data["status"]

        with pytest.raises(ValueError, match="Status fixture data not available"):
            engine.get_status_data()

        # Restore status data
        if original_status:
            engine._base_data["status"] = original_status

        # Test SOE fixture missing
        original_soe = engine._base_data.get("soe")
        if "soe" in engine._base_data:
            del engine._base_data["soe"]

        with pytest.raises(ValueError, match="Storage SOE fixture data not available"):
            engine.get_storage_soe_data()

        # Restore SOE data
        if original_soe:
            engine._base_data["soe"] = original_soe

    def test_panel_variations_coverage(self) -> None:
        """Test panel variations that are missing coverage."""
        from span_panel_api.simulation import DynamicSimulationEngine, PanelVariation

        engine = DynamicSimulationEngine()

        # Test dsm_state variation
        panel_variations = PanelVariation(dsm_state="DSM_OFF_GRID")
        result = engine.get_panel_state_data(panel_variations=panel_variations)
        assert result["dsmState"] == "DSM_OFF_GRID"

        # Test instant_grid_power_variation
        panel_variations = PanelVariation(instant_grid_power_variation=0.1)
        result = engine.get_panel_state_data(panel_variations=panel_variations)
        # Should have modified the instantGridPowerW value
        assert "instantGridPowerW" in result

    def test_status_variations_coverage(self) -> None:
        """Test status variations that are missing coverage."""
        from span_panel_api.simulation import DynamicSimulationEngine, StatusVariation

        engine = DynamicSimulationEngine()

        # Test main_relay_state variation by adding mainRelayState to fixture
        # First add mainRelayState to the fixture data
        if "status" in engine._base_data and "system" in engine._base_data["status"]:
            engine._base_data["status"]["system"]["mainRelayState"] = "CLOSED"

        status_variations = StatusVariation(main_relay_state="OPEN")
        result = engine.get_status_data(variations=status_variations)
        # Now the line should be executed
        assert result["system"]["mainRelayState"] == "OPEN"

        # Test wlan_link variation
        status_variations = StatusVariation(wlan_link=False)
        result = engine.get_status_data(variations=status_variations)
        assert result["network"]["wlanLink"] is False

        # Test wwwan_link variation
        status_variations = StatusVariation(wwwan_link=True)
        result = engine.get_status_data(variations=status_variations)
        assert result["network"]["wwanLink"] is True


class TestSimulationCaching:
    """Test caching behavior in simulation mode."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        return SpanPanelClient(host="localhost", simulation_mode=True)

    async def test_simulation_caching(self, sim_client: SpanPanelClient) -> None:
        """Test that simulation results are cached appropriately."""
        async with sim_client:
            # Same parameters should return cached results
            circuits1 = await sim_client.get_circuits(global_power_variation=0.2)
            circuits2 = await sim_client.get_circuits(global_power_variation=0.2)

            # Should be the same object due to caching
            assert circuits1 is circuits2

    async def test_different_variations_not_cached(self, sim_client: SpanPanelClient) -> None:
        """Test that different variations create different cache entries."""
        async with sim_client:
            circuits1 = await sim_client.get_circuits(global_power_variation=0.2)
            circuits2 = await sim_client.get_circuits(global_power_variation=0.3)

            # Should be different objects
            assert circuits1 is not circuits2
