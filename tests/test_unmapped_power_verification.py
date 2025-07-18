"""Test to verify unmapped tabs show realistic power consumption."""

import pytest
from span_panel_api import SpanPanelClient


class TestUnmappedTabPowerConsumption:
    """Test unmapped tab power consumption behavior."""

    @pytest.mark.asyncio
    async def test_unmapped_tabs_show_realistic_power(self):
        """Test that unmapped tabs now show realistic power consumption."""
        config_path = "examples/simulation_config_8_tab_workshop.yaml"
        client = SpanPanelClient("test-host", simulation_mode=True, simulation_config_path=config_path)

        # Get circuits data
        circuits = await client.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Get panel state to check branch power
        panel_state = await client.get_panel_state()

        print("\n=== Circuit Power ===")
        for circuit_id, circuit in circuit_data.items():
            print(f"Circuit {circuit_id} ({circuit.name}): {circuit.instant_power_w:.1f}W")

        print("\n=== Branch Power ===")
        for i, branch in enumerate(panel_state.branches, 1):
            print(f"Branch {i} (Tab {i}): {branch.instant_power_w:.1f}W")

        # Verify unmapped tabs (5-8) have realistic power
        for tab_num in [5, 6, 7, 8]:
            branch = panel_state.branches[tab_num - 1]  # 0-indexed
            power = branch.instant_power_w
            assert 10.0 <= power <= 200.0, f"Tab {tab_num} power {power}W not in expected range 10-200W"
            print(f"✓ Unmapped Tab {tab_num}: {power:.1f}W (realistic baseline power)")

        # Verify unmapped circuits also reflect this power
        for tab_num in [5, 6, 7, 8]:
            circuit_id = f"unmapped_tab_{tab_num}"
            circuit = circuit_data[circuit_id]
            power = circuit.instant_power_w
            assert 10.0 <= power <= 200.0, f"Unmapped circuit {circuit_id} power {power}W not in expected range"
            print(f"✓ Unmapped Circuit {circuit_id}: {power:.1f}W")

        print("\n✅ All unmapped tabs now show realistic power consumption!")
