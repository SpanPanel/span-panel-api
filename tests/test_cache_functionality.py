"""Test the TimeWindowCache functionality."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from span_panel_api.client import SpanPanelClient, TimeWindowCache


def test_cache_basic_functionality():
    """Test basic cache window behavior."""
    cache = TimeWindowCache(window_duration=1.0)

    # Initially empty
    assert cache.get_cached_data("test_key") is None

    # Set data
    test_data = {"test": "data"}
    cache.set_cached_data("test_key", test_data)

    # Should return cached data immediately
    assert cache.get_cached_data("test_key") == test_data

    # Should still be cached after short delay
    time.sleep(0.1)
    assert cache.get_cached_data("test_key") == test_data


def test_cache_expiration():
    """Test cache window expiration."""
    cache = TimeWindowCache(window_duration=0.1)  # Very short window

    test_data = {"test": "data"}
    cache.set_cached_data("test_key", test_data)

    # Should be cached initially
    assert cache.get_cached_data("test_key") == test_data

    # Wait for expiration
    time.sleep(0.15)

    # Should be expired now
    assert cache.get_cached_data("test_key") is None


def test_cache_multiple_keys():
    """Test cache with multiple keys."""
    cache = TimeWindowCache(window_duration=1.0)

    data1 = {"key1": "data1"}
    data2 = {"key2": "data2"}

    cache.set_cached_data("key1", data1)
    cache.set_cached_data("key2", data2)

    assert cache.get_cached_data("key1") == data1
    assert cache.get_cached_data("key2") == data2
    assert cache.get_cached_data("nonexistent") is None


def test_cache_validation():
    """Test cache parameter validation."""
    # 0 should be allowed (disables cache)
    cache = TimeWindowCache(window_duration=0)
    assert cache._window_duration == 0

    # Negative values should be rejected
    with pytest.raises(ValueError, match="Cache window duration must be non-negative"):
        TimeWindowCache(window_duration=-1)


@pytest.mark.asyncio
async def test_client_cache_integration():
    """Test that the client properly integrates the cache."""
    # Test that cache window parameter is passed correctly
    client = SpanPanelClient("192.168.1.100", cache_window=0.5)

    # Verify cache is initialized
    assert hasattr(client, "_api_cache")
    assert client._api_cache._window_duration == 0.5


@pytest.mark.asyncio
async def test_cache_prevents_redundant_calls():
    """Test that cache prevents redundant API calls."""
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_status_api:
        # Mock the API response
        mock_response = AsyncMock()
        mock_response.return_value = {"status": "mock_data"}
        mock_status_api.asyncio = mock_response

        client = SpanPanelClient("192.168.1.100", cache_window=1.0)

        async with client:
            # First call should hit the API
            result1 = await client.get_status()

            # Second call should use cache (no additional API call)
            result2 = await client.get_status()

            # Should be the same object
            assert result1 is result2

            # API should have been called only once
            assert mock_status_api.asyncio.call_count == 1


if __name__ == "__main__":
    # Run basic functionality tests
    test_cache_basic_functionality()
    test_cache_expiration()
    test_cache_multiple_keys()
    test_cache_validation()
