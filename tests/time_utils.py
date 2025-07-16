"""Time utilities for SPAN Panel API testing.

Provides controlled time advancement and timing helpers for testing
time-dependent behaviors like caching, rate limiting, and energy accumulation.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional
from unittest.mock import patch


async def advance_time_async(seconds: float) -> None:
    """Advance time in async test context by yielding control to event loop.

    Use this instead of asyncio.sleep(seconds) when you need time to pass
    but want to yield control for pending coroutines. Tests can use sleep.

    Args:
        seconds: Time to advance. Use 0 to just yield control.
    """
    if seconds <= 0:
        await asyncio.sleep(0)  # Just yield control
    else:
        # For tests, we can use actual sleep
        await asyncio.sleep(seconds)


@asynccontextmanager
async def mock_time_progression(start_time: Optional[datetime] = None, time_step: float = 1.0) -> AsyncGenerator[dict, None]:
    """Mock time progression for deterministic time-based testing.

    Args:
        start_time: Starting datetime. Defaults to current time.
        time_step: Seconds to advance per step

    Yields:
        dict with 'advance' function to step time forward

    Example:
        async with mock_time_progression() as time_mock:
            # Initial state
            result1 = await client.get_panel_state()

            # Advance time and test again
            time_mock['advance'](30)  # 30 seconds
            result2 = await client.get_panel_state()
    """
    if start_time is None:
        start_time = datetime.now()

    current_time = start_time

    def mock_time():
        return current_time.timestamp()

    def mock_datetime_now():
        return current_time

    def advance_time(seconds: float) -> None:
        nonlocal current_time
        current_time += timedelta(seconds=seconds)

    with patch('time.time', side_effect=mock_time), patch('datetime.datetime.now', side_effect=mock_datetime_now):
        yield {'advance': advance_time, 'current_time': lambda: current_time}


async def wait_for_condition(
    condition_func, max_iterations: int = 100, error_message: str = "Condition not met within iterations"
) -> None:
    """Wait for a condition to become true within iteration limit.

    HA-compatible version that doesn't use real time delays.

    Args:
        condition_func: Callable that returns True when condition is met
        max_iterations: Maximum iterations to check
        error_message: Error message if max iterations reached

    Raises:
        TimeoutError: If condition is not met within iterations
    """
    for _ in range(max_iterations):
        if condition_func():
            return
        await asyncio.sleep(0)  # Yield control

    raise TimeoutError(error_message)


class TimeAccumulator:
    """Helper for testing time-based accumulations like energy totals."""

    def __init__(self, initial_value: float = 0.0):
        self.initial_value = initial_value
        self.measurements = []
        self.start_time = time.time()

    def add_measurement(self, value: float, timestamp: Optional[float] = None) -> None:
        """Add a measurement at a specific time."""
        if timestamp is None:
            timestamp = time.time()
        self.measurements.append((timestamp, value))

    def get_accumulated_value(self, duration: float) -> float:
        """Calculate accumulated value over a duration.

        For energy: power (watts) * time (hours) = energy (watt-hours)
        """
        if not self.measurements:
            return self.initial_value

        total = self.initial_value
        for i in range(len(self.measurements) - 1):
            current_time, current_value = self.measurements[i]
            next_time, _ = self.measurements[i + 1]
            time_diff = next_time - current_time
            total += current_value * (time_diff / 3600)  # Convert to hours

        # Add final measurement if within duration
        if self.measurements:
            final_time, final_value = self.measurements[-1]
            remaining_time = duration - (final_time - self.start_time)
            if remaining_time > 0:
                total += final_value * (remaining_time / 3600)

        return total

    def reset(self) -> None:
        """Reset accumulator for new measurement series."""
        self.measurements.clear()
        self.start_time = time.time()


class CacheTimer:
    """Helper for testing cache expiration and TTL behavior."""

    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self.cached_items = {}

    def cache_item(self, key: str, value: any, timestamp: Optional[float] = None) -> None:
        """Cache an item with timestamp."""
        if timestamp is None:
            timestamp = time.time()
        self.cached_items[key] = (value, timestamp)

    def is_expired(self, key: str, current_time: Optional[float] = None) -> bool:
        """Check if cached item is expired."""
        if key not in self.cached_items:
            return True

        if current_time is None:
            current_time = time.time()

        _, cache_time = self.cached_items[key]
        return (current_time - cache_time) > self.ttl_seconds

    def get_if_valid(self, key: str, current_time: Optional[float] = None) -> Optional[any]:
        """Get cached item if not expired."""
        if self.is_expired(key, current_time):
            return None
        return self.cached_items[key][0]


async def simulate_api_delay(min_delay: float = 0.1, max_delay: float = 0.5, jitter: bool = True) -> None:
    """Simulate API processing without blocking in HA context.

    In HA testing, this just yields control instead of actual delays.

    Args:
        min_delay: Ignored in HA context
        max_delay: Ignored in HA context
        jitter: Ignored in HA context
    """
    # In HA context, we just yield control instead of real delays
    await asyncio.sleep(0)
