"""Test factories and fixtures for SPAN Panel API tests.

This module provides reusable fixtures and factories for creating test clients
in different configurations, particularly simulation mode clients.
"""

import pytest

from span_panel_api.client import SpanPanelClient


@pytest.fixture
def sim_client() -> SpanPanelClient:
    """Factory for creating simulation mode clients with caching enabled.

    Returns:
        SpanPanelClient configured for simulation mode with 1.0 second cache window
    """
    return SpanPanelClient("192.168.1.100", simulation_mode=True, cache_window=1.0)


@pytest.fixture
def sim_client_no_cache() -> SpanPanelClient:
    """Factory for creating simulation mode clients with caching disabled.

    Returns:
        SpanPanelClient configured for simulation mode with no caching
    """
    return SpanPanelClient("192.168.1.100", simulation_mode=True, cache_window=0)


@pytest.fixture
def sim_client_custom_cache() -> callable:
    """Factory function for creating simulation mode clients with custom cache windows.

    Returns:
        Function that takes cache_window parameter and returns configured client
    """

    def _create_client(cache_window: float = 1.0) -> SpanPanelClient:
        return SpanPanelClient("192.168.1.100", simulation_mode=True, cache_window=cache_window)

    return _create_client


def create_sim_client(cache_window: float = 1.0, host: str = "192.168.1.100", **kwargs) -> SpanPanelClient:
    """Direct factory function for creating simulation mode clients.

    Args:
        cache_window: Cache window duration in seconds (default: 1.0)
        host: Host address (default: "192.168.1.100")
        **kwargs: Additional client configuration parameters

    Returns:
        SpanPanelClient configured for simulation mode
    """
    return SpanPanelClient(host, simulation_mode=True, cache_window=cache_window, **kwargs)


def create_live_client(cache_window: float = 1.0, host: str = "192.168.1.100", **kwargs) -> SpanPanelClient:
    """Direct factory function for creating live mode clients (for testing live functionality).

    Args:
        cache_window: Cache window duration in seconds (default: 1.0)
        host: Host address (default: "192.168.1.100")
        **kwargs: Additional client configuration parameters

    Returns:
        SpanPanelClient configured for live mode
    """
    return SpanPanelClient(host, simulation_mode=False, cache_window=cache_window, **kwargs)
