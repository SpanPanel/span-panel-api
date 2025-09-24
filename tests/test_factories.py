"""Test factories and fixtures for SPAN Panel API tests.

This module provides reusable fixtures and factories for creating test clients
in different configurations, particularly simulation mode clients.
"""

import pytest
from pathlib import Path

from span_panel_api.client import SpanPanelClient


@pytest.fixture
def sim_client() -> SpanPanelClient:
    """Factory for creating simulation mode clients with caching enabled.

    Returns:
        SpanPanelClient configured for simulation mode with 1.0 second cache window
    """
    config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
    return SpanPanelClient("yaml-sim-test", simulation_mode=True, simulation_config_path=str(config_path))


@pytest.fixture
def sim_client_no_cache() -> SpanPanelClient:
    """Factory for creating simulation mode clients with caching disabled.

    Returns:
        SpanPanelClient configured for simulation mode with no caching
    """
    config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
    return SpanPanelClient("yaml-sim-test", simulation_mode=True, simulation_config_path=str(config_path))


@pytest.fixture
def sim_client_custom_cache() -> callable:
    """Factory function for creating simulation mode clients.

    Returns:
        Function that returns configured client
    """

    def _create_client() -> SpanPanelClient:
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        return SpanPanelClient("yaml-sim-test", simulation_mode=True, simulation_config_path=str(config_path))

    return _create_client


def create_sim_client(host: str = "yaml-sim-test", **kwargs) -> SpanPanelClient:
    """Direct factory function for creating simulation mode clients.

    Args:
        host: Host address (default: "yaml-sim-test")
        **kwargs: Additional client configuration parameters

    Returns:
        SpanPanelClient configured for simulation mode with YAML config
    """
    config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
    return SpanPanelClient(host, simulation_mode=True, simulation_config_path=str(config_path), **kwargs)


def create_live_client(host: str = "192.168.1.100", **kwargs) -> SpanPanelClient:
    """Direct factory function for creating live mode clients (for testing live functionality).

    Args:
        host: Host address (default: "192.168.1.100")
        **kwargs: Additional client configuration parameters

    Returns:
        SpanPanelClient configured for live mode
    """
    return SpanPanelClient(host, simulation_mode=False, **kwargs)
