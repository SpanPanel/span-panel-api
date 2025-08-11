"""Test the TimeWindowCache functionality."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from span_panel_api.client import SpanPanelClient, TimeWindowCache
from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelAuthError, SpanPanelConnectionError, SpanPanelTimeoutError
from span_panel_api.generated_client.errors import UnexpectedStatus
from tests.test_factories import create_sim_client


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
    client = create_sim_client(cache_window=0.5)

    # Verify cache is initialized
    assert hasattr(client, "_api_cache")
    assert client._api_cache._window_duration == 0.5


@pytest.mark.asyncio
async def test_cache_prevents_redundant_calls():
    """Test that cache prevents redundant API calls using simulation mode."""
    client = create_sim_client(cache_window=1.0)

    # Track simulation engine calls to verify caching behavior
    original_get_status = client._simulation_engine.get_status
    call_count = 0

    async def track_status_data(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_get_status(*args, **kwargs)

    client._simulation_engine.get_status = track_status_data

    # First call should hit the simulation engine
    result1 = await client.get_status()
    assert call_count == 1

    # Second call should use cache (no additional simulation engine call)
    result2 = await client.get_status()
    assert call_count == 1  # Still only 1 call
    assert result1 == result2


@pytest.mark.asyncio
async def test_cache_hit_paths(sim_client: SpanPanelClient):
    """Test cache hit paths for all API methods using simulation mode."""
    # Track simulation engine calls to verify caching behavior
    original_get_panel_data = sim_client._simulation_engine.get_panel_data
    original_get_status = sim_client._simulation_engine.get_status
    original_get_soe = sim_client._simulation_engine.get_soe

    call_counts = {"circuits": 0, "panel_state": 0, "status": 0, "storage_soe": 0}

    async def track_panel_data(*args, **kwargs):
        call_counts["circuits"] += 1
        call_counts["panel_state"] += 1
        return await original_get_panel_data(*args, **kwargs)

    async def track_status_data(*args, **kwargs):
        call_counts["status"] += 1
        return await original_get_status(*args, **kwargs)

    async def track_soe_data(*args, **kwargs):
        call_counts["storage_soe"] += 1
        return await original_get_soe(*args, **kwargs)

    sim_client._simulation_engine.get_panel_data = track_panel_data
    sim_client._simulation_engine.get_status = track_status_data
    sim_client._simulation_engine.get_soe = track_soe_data

    # Test status cache hit
    await sim_client.get_status()
    assert call_counts["status"] == 1

    await sim_client.get_status()  # Should hit cache
    assert call_counts["status"] == 1  # No additional call

    # Test panel state cache hit
    await sim_client.get_panel_state()
    assert call_counts["panel_state"] == 1

    await sim_client.get_panel_state()  # Should hit cache
    assert call_counts["panel_state"] == 1  # No additional call

    # Test circuits cache hit
    # Note: get_circuits() now uses shared caching with panel state for better consistency
    await sim_client.get_circuits()
    assert call_counts["circuits"] == 1  # Called once, shared cache ensures consistency

    await sim_client.get_circuits()  # Should hit cache
    assert call_counts["circuits"] == 1  # No additional calls

    # Test storage SOE cache hit
    await sim_client.get_storage_soe()
    assert call_counts["storage_soe"] == 1

    await sim_client.get_storage_soe()  # Should hit cache
    assert call_counts["storage_soe"] == 1  # No additional call


@pytest.mark.asyncio
async def test_cache_disabled_behavior():
    """Test that cache_window=0 disables caching entirely."""
    cache = TimeWindowCache(window_duration=0)

    # Setting data should do nothing
    cache.set_cached_data("test", "data")

    # Getting data should always return None
    assert cache.get_cached_data("test") is None

    # Test with simulation client
    client = create_sim_client(cache_window=0)

    # Track simulation engine calls to verify no caching
    original_get_status = client._simulation_engine.get_status
    call_count = 0

    async def track_status_data(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_get_status(*args, **kwargs)

    client._simulation_engine.get_status = track_status_data

    # Each call should hit the simulation engine (no caching)
    await client.get_status()
    await client.get_status()
    assert call_count == 2


@pytest.mark.asyncio
async def test_error_handling_paths():
    """Test error handling paths in live API methods."""

    # Test UnexpectedStatus error handling
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=UnexpectedStatus(404, b"Not Found"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)

        with pytest.raises(SpanPanelAPIError):
            await client.get_status()

    # Test httpx.HTTPStatusError handling
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.content = b"Server Error"
        mock_api.asyncio = AsyncMock(
            side_effect=httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_response)
        )

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)

        with pytest.raises(SpanPanelAPIError):
            await client.get_status()

    # Test httpx.ConnectError handling
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)

        with pytest.raises(SpanPanelConnectionError):
            await client.get_status()

    # Test httpx.TimeoutException handling
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)

        with pytest.raises(SpanPanelTimeoutError):
            await client.get_status()

    # Test ValueError handling
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=ValueError("Validation error"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)

        with pytest.raises(SpanPanelAPIError):
            await client.get_status()

    # Test generic Exception handling
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)

        with pytest.raises(SpanPanelAPIError):
            await client.get_status()


@pytest.mark.asyncio
async def test_panel_state_auth_error_passthrough():
    """Test that panel state passes through auth errors directly."""
    with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)
        client.set_access_token("test_token")

        with pytest.raises(SpanPanelAuthError):
            await client.get_panel_state()


@pytest.mark.asyncio
async def test_panel_state_401_error_handling():
    """Test that panel state handles 401 errors in generic exception handler."""
    with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=RuntimeError("401 Unauthorized"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)
        client.set_access_token("test_token")

        with pytest.raises(SpanPanelAuthError):
            await client.get_panel_state()


@pytest.mark.asyncio
async def test_retry_logic_with_backoff():
    """Test retry logic with exponential backoff."""
    call_count = 0

    async def failing_operation(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:  # Fail first 2 times
            raise UnexpectedStatus(503, b"Service Unavailable")
        return {"status": "ok"}

    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=failing_operation)

        client = SpanPanelClient("192.168.1.100", cache_window=0, retries=2, retry_timeout=0.01, simulation_mode=False)

        # Should succeed after retries
        result = await client.get_status()
        assert result == {"status": "ok"}
        assert call_count == 3  # Initial + 2 retries


@pytest.mark.asyncio
async def test_retry_logic_max_attempts_exceeded():
    """Test retry logic when max attempts are exceeded."""
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=UnexpectedStatus(503, b"Service Unavailable"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, retries=1, retry_timeout=0.01, simulation_mode=False)

        with pytest.raises(SpanPanelAPIError):
            await client.get_status()

        assert mock_api.asyncio.call_count == 2  # Initial + 1 retry


@pytest.mark.asyncio
async def test_retry_logic_non_retriable_error():
    """Test that non-retriable errors are not retried."""
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(side_effect=UnexpectedStatus(404, b"Not Found"))

        client = SpanPanelClient("192.168.1.100", cache_window=0, retries=2, simulation_mode=False)

        with pytest.raises(SpanPanelAPIError):
            await client.get_status()

        assert mock_api.asyncio.call_count == 1  # No retries for 404


@pytest.mark.asyncio
async def test_api_result_none_error():
    """Test handling of None API results."""
    with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_api:
        mock_api.asyncio = AsyncMock(return_value=None)

        client = SpanPanelClient("192.168.1.100", cache_window=0, simulation_mode=False)

        with pytest.raises(SpanPanelAPIError, match="API result is None despite raise_on_unexpected_status=True"):
            await client.get_status()


@pytest.mark.asyncio
async def test_cache_disabled_with_simulation(sim_client_no_cache: SpanPanelClient):
    """Test that cache_window=0 disables caching in simulation mode."""
    # Track simulation engine calls to verify no caching
    original_get_status = sim_client_no_cache._simulation_engine.get_status
    call_count = 0

    async def track_status_data(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_get_status(*args, **kwargs)

    sim_client_no_cache._simulation_engine.get_status = track_status_data

    # Each call should hit the simulation engine (no caching)
    await sim_client_no_cache.get_status()
    assert call_count == 1

    await sim_client_no_cache.get_status()
    assert call_count == 2  # Should increment since cache is disabled


@pytest.mark.asyncio
async def test_cache_hit_coverage():
    """Test cache hit paths to ensure they're covered (lines 721, 829, 976)."""
    from tests.test_factories import create_live_client

    client = create_live_client(cache_window=1.0)  # Enable caching
    client.set_access_token("test-token")

    # Test panel state cache hit (line 721)
    with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel:
        mock_response = MagicMock()
        mock_panel.asyncio = AsyncMock(return_value=mock_response)

        # First call - should hit API and cache result
        result1 = await client.get_panel_state()
        assert mock_panel.asyncio.call_count == 1

        # Second call - should hit cache (line 721)
        result2 = await client.get_panel_state()
        assert mock_panel.asyncio.call_count == 1  # No additional call
        assert result1 == result2

    # Test circuits cache hit (line 829)
    with (
        patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_circuits,
        patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel_for_circuits,
    ):
        mock_circuits_response = MagicMock()
        mock_circuits_response.circuits = MagicMock()
        # Add some unmapped circuits to prevent cache clearing
        mock_circuits_response.circuits.additional_properties = {
            "unmapped_tab_1": MagicMock(),
            "unmapped_tab_2": MagicMock(),
        }
        mock_circuits.asyncio = AsyncMock(return_value=mock_circuits_response)

        mock_panel_response = MagicMock()
        mock_panel_response.branches = [MagicMock(), MagicMock()]  # Add branches to match unmapped circuits
        mock_panel_for_circuits.asyncio = AsyncMock(return_value=mock_panel_response)

        # First call - should hit API and cache result
        result1 = await client.get_circuits()
        circuits_call_count = mock_circuits.asyncio.call_count

        # Second call - should hit cache (line 829)
        result2 = await client.get_circuits()
        assert mock_circuits.asyncio.call_count == circuits_call_count  # No additional call

    # Test storage SOE cache hit (line 976)
    with patch("span_panel_api.client.get_storage_soe_api_v1_storage_soe_get") as mock_storage:
        mock_storage_response = MagicMock()
        mock_storage.asyncio = AsyncMock(return_value=mock_storage_response)

        # First call - should hit API and cache result
        result1 = await client.get_storage_soe()
        assert mock_storage.asyncio.call_count == 1

        # Second call - should hit cache (line 976)
        result2 = await client.get_storage_soe()
        assert mock_storage.asyncio.call_count == 1  # No additional call
        assert result1 == result2


if __name__ == "__main__":
    test_cache_basic_functionality()
    test_cache_expiration()
    test_cache_multiple_keys()
    test_cache_validation()
