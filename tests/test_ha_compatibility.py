"""Tests for Home Assistant compatibility features."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from span_panel_api import set_async_delay_func
from span_panel_api.client import _default_async_delay


class TestHACompatibility:
    """Test Home Assistant compatibility features."""

    def test_set_async_delay_func_custom(self) -> None:
        """Test setting a custom async delay function."""
        # Test that the function can be called without error
        custom_delay = AsyncMock()

        # Set the custom function
        set_async_delay_func(custom_delay)

        # Reset to default to clean up
        set_async_delay_func(None)

    def test_set_async_delay_func_none_resets_default(self) -> None:
        """Test that setting None resets to default implementation."""
        # First set a custom function
        custom_delay = AsyncMock()
        set_async_delay_func(custom_delay)

        # Reset to default
        set_async_delay_func(None)

        # Test should pass if no errors occur

    async def test_custom_delay_function_integration(self) -> None:
        """Test that custom delay function can be set and used."""
        # Create a mock delay function that tracks calls
        delay_calls = []

        async def mock_delay(delay_seconds: float) -> None:
            delay_calls.append(delay_seconds)
            # Don't actually delay in tests
            await asyncio.sleep(0)

        # Set the custom delay function
        set_async_delay_func(mock_delay)

        try:
            # Test that we can access the module's delay registry
            from span_panel_api.client import _delay_registry

            # Call the delay function directly to verify it's our custom one
            await _delay_registry.call_delay(0.5)

            # Verify our custom delay function was called
            assert len(delay_calls) == 1
            assert delay_calls[0] == 0.5

        finally:
            # Reset to default
            set_async_delay_func(None)

    async def test_default_delay_function_works(self) -> None:
        """Test that the default delay function works correctly."""
        import time

        # Ensure we're using default
        set_async_delay_func(None)

        # Time a very short delay
        start_time = time.time()
        await _default_async_delay(0.01)  # 10ms
        end_time = time.time()

        # Should have delayed at least 5ms (allowing for timing variations)
        assert end_time - start_time >= 0.005

    def test_import_from_main_module(self) -> None:
        """Test that set_async_delay_func can be imported from the main module."""
        # This test verifies the function is properly exported
        from span_panel_api import set_async_delay_func as imported_func

        assert imported_func is not None
        assert callable(imported_func)
