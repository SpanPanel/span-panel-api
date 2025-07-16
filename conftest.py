import asyncio
from collections.abc import Generator

import pytest

from span_panel_api.client import SpanPanelClient
from tests.test_helpers import (
    create_behavior_sim_client,
    create_full_sim_client,
    create_minimal_sim_client,
    create_simple_sim_client,
)

# Register pytest-asyncio plugin and provide legacy event_loop fixture
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Provide a new asyncio event loop for each test (for pytest-homeassistant compatibility)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sim_client() -> SpanPanelClient:
    """Provide a simple YAML-based simulation client for testing."""
    return create_simple_sim_client()


@pytest.fixture
def sim_client_no_cache() -> SpanPanelClient:
    """Provide a simple YAML-based simulation client with no caching."""
    return create_simple_sim_client(cache_window=0)


@pytest.fixture
def minimal_sim_client() -> SpanPanelClient:
    """Provide a minimal YAML-based simulation client for basic testing."""
    return create_minimal_sim_client()


@pytest.fixture
def behavior_sim_client() -> SpanPanelClient:
    """Provide a behavior-testing YAML-based simulation client."""
    return create_behavior_sim_client()


@pytest.fixture
def full_sim_client() -> SpanPanelClient:
    """Provide a full 32-circuit YAML-based simulation client."""
    return create_full_sim_client()
