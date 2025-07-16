"""Test helper functions for simulation and client testing."""

from pathlib import Path
from typing import Optional

from span_panel_api.client import SpanPanelClient


def create_yaml_sim_client(
    config_name: str = "simple_test_config.yaml",
    cache_window: float = 5.0,
    host: Optional[str] = None,
) -> SpanPanelClient:
    """Create a simulation client using YAML configuration.

    Args:
        config_name: Name of YAML config file in examples/ directory
        cache_window: Cache window duration
        host: Custom host/serial number (defaults to config value)

    Returns:
        Configured SpanPanelClient in simulation mode
    """
    config_path = Path(__file__).parent.parent / "examples" / config_name

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    return SpanPanelClient(
        host=host or "yaml-test-host",
        simulation_mode=True,
        simulation_config_path=str(config_path),
        cache_window=cache_window,
    )


def create_minimal_sim_client(cache_window: float = 5.0) -> SpanPanelClient:
    """Create a minimal simulation client for basic testing."""
    return create_yaml_sim_client("minimal_config.yaml", cache_window)


def create_behavior_sim_client(cache_window: float = 5.0) -> SpanPanelClient:
    """Create a simulation client for testing behavior patterns."""
    return create_yaml_sim_client("behavior_test_config.yaml", cache_window)


def create_simple_sim_client(cache_window: float = 5.0) -> SpanPanelClient:
    """Create a simple simulation client for general testing."""
    return create_yaml_sim_client("simple_test_config.yaml", cache_window)


def create_full_sim_client(cache_window: float = 5.0) -> SpanPanelClient:
    """Create a full 32-circuit simulation client."""
    return create_yaml_sim_client("simulation_config_32_circuit.yaml", cache_window)
