#!/usr/bin/env python3
"""
Test the enhanced get_circuits method with unmapped tab virtual circuits.
"""

import asyncio
import logging

import pytest

from span_panel_api import SpanPanelClient

# Panel credentials
PANEL_HOST = "192.168.65.70"
PANEL_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJob21lLWFzc2lzdGFudC1qYXZhNjU2NTY1NjY1IiwiaWF0IjoxNzA2OTA4OTEwfQ.nMbv3zkNTm4l8BIvhOQy1xTU6lP2FpKGNEQFnZ2QCT4"

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_enhanced_circuits():
    """Test the enhanced get_circuits method."""
    logger.info("Testing enhanced get_circuits method...")
    logger.info(f"Host: {PANEL_HOST}")

    client = SpanPanelClient(host=PANEL_HOST)
    client.set_access_token(PANEL_TOKEN)

    try:
        async with client:
            # Get circuits with unmapped tab virtual circuits
            circuits_response = await client.get_circuits()

            # Verify we got a response
            assert circuits_response is not None
            logger.info(f"Circuits response type: {type(circuits_response)}")

            if hasattr(circuits_response, "circuits") and hasattr(circuits_response.circuits, "additional_properties"):
                circuit_dict = circuits_response.circuits.additional_properties
                logger.info(f"Total circuits (including virtual): {len(circuit_dict)}")

                # Separate real and virtual circuits
                real_circuits = []
                virtual_circuits = []

                for circuit_id, circuit in circuit_dict.items():
                    if circuit_id.startswith("unmapped_tab_"):
                        virtual_circuits.append((circuit_id, circuit))
                    else:
                        real_circuits.append((circuit_id, circuit))

                logger.info(f"Real circuits: {len(real_circuits)}")
                logger.info(f"Virtual circuits: {len(virtual_circuits)}")

                # Verify we have both real and virtual circuits
                assert len(real_circuits) > 0, "Should have at least one real circuit"
                assert len(virtual_circuits) > 0, "Should have at least one virtual circuit"

                # Verify virtual circuits structure
                if virtual_circuits:
                    logger.info("Virtual Circuits (Unmapped Tabs):")
                    for circuit_id, circuit in virtual_circuits:
                        tab_num = circuit_id.replace("unmapped_tab_", "")
                        name = getattr(circuit, "name", "N/A")
                        power = getattr(circuit, "instant_power_w", "N/A")
                        produced = getattr(circuit, "produced_energy_wh", "N/A")
                        consumed = getattr(circuit, "consumed_energy_wh", "N/A")
                        tabs = getattr(circuit, "tabs", "N/A")

                        logger.info(f"  Tab {tab_num}: {name}")
                        logger.info(f"    Power: {power}W")
                        logger.info(f"    Produced: {produced}Wh")
                        logger.info(f"    Consumed: {consumed}Wh")
                        logger.info(f"    Tabs: {tabs}")

                        # Verify virtual circuit structure
                        assert circuit_id.startswith("unmapped_tab_"), f"Invalid virtual circuit ID: {circuit_id}"
                        assert hasattr(circuit, "name"), "Virtual circuit should have name"
                        assert hasattr(circuit, "instant_power_w"), "Virtual circuit should have power"

                # Verify real circuits structure
                logger.info("Sample Real Circuits:")
                for _circuit_id, circuit in real_circuits[:3]:
                    name = getattr(circuit, "name", "N/A")
                    power = getattr(circuit, "instant_power_w", "N/A")
                    tabs = getattr(circuit, "tabs", "N/A")
                    logger.info(f"  {name}: {power}W (tabs: {tabs})")

                logger.info("Enhanced get_circuits test completed successfully!")
            else:
                pytest.fail("Unexpected circuits response structure")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        pytest.fail(f"Test failed with exception: {e}")


if __name__ == "__main__":
    asyncio.run(test_enhanced_circuits())
