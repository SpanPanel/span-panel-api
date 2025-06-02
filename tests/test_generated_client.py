"""Test the generated httpx-based client."""

import pytest

from span_panel_api.generated_client import AuthenticatedClient, Client
from span_panel_api.generated_client.api.default import system_status_api_v1_status_get


def test_client_import():
    """Test that the generated client can be imported."""
    # This should not raise any import errors
    assert Client is not None
    assert AuthenticatedClient is not None


def test_client_creation():
    """Test that clients can be created."""
    client = Client(base_url="https://test.example.com")
    assert client is not None

    auth_client = AuthenticatedClient(
        base_url="https://test.example.com", token="test-token"
    )
    assert auth_client is not None


def test_api_functions_import():
    """Test that API functions can be imported."""
    # This should not raise import errors
    assert system_status_api_v1_status_get is not None


@pytest.mark.asyncio
async def test_client_context_manager():
    """Test that the client works as an async context manager."""
    async with Client(base_url="https://test.example.com") as client:
        # Just test that the context manager works
        assert client is not None
