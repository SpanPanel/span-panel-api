# ruff: noqa
import asyncio
import pytest
from typing import Generator

# Register pytest-asyncio plugin and provide legacy event_loop fixture
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Provide a new asyncio event loop for each test (for pytest-homeassistant compatibility)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
