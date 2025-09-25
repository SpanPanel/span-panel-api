"""Tests for client caching functionality."""

import pytest
from src.span_panel_api.client import SpanPanelClient


class TestClientCaching:
    """Tests for client caching behavior."""

    async def test_simulation_status_cache_hit(self):
        """Test simulation status method with cache hit."""
        client = SpanPanelClient(
            host="test-serial",
            simulation_mode=True,
            simulation_config_path="examples/simple_test_config.yaml",
        )

        async with client:
            # First call to populate cache
            status1 = await client.get_status()

            # Second call should hit cache
            status2 = await client.get_status()

            # Should be the same object from cache
            assert status1 is status2
