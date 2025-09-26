"""Shared pytest configuration and fixtures for SPAN Panel API tests."""

# Import fixtures from test_factories to make them available to all tests
from tests.test_factories import sim_client

__all__ = ["sim_client"]
