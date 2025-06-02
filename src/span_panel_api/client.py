"""SPAN Panel API Client.

This module provides a high-level async client for the SPAN Panel REST API.
It wraps the generated OpenAPI client to provide a more convenient interface.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from .exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
)

try:
    from .generated_client import AuthenticatedClient, Client
    from .generated_client.api.default import (
        generate_jwt_api_v1_auth_register_post,
        get_circuits_api_v1_circuits_get,
        get_panel_state_api_v1_panel_get,
        get_storage_soe_api_v1_storage_soe_get,
        set_circuit_state_api_v_1_circuits_circuit_id_post,
        system_status_api_v1_status_get,
    )
    from .generated_client.models import (
        AuthIn,
        AuthOut,
        BatteryStorage,
        BodySetCircuitStateApiV1CircuitsCircuitIdPost,
        CircuitsOut,
        PanelState,
        Priority,
        PriorityIn,
        RelayState,
        RelayStateIn,
        StatusOut,
    )
    from .generated_client.models.http_validation_error import HTTPValidationError
except ImportError as e:
    raise ImportError(
        f"Could not import the generated client: {e}. "
        "Make sure the generated_client is properly installed as part of span_panel_api."
    ) from e


class SpanPanelClient:
    """Modern async client for SPAN Panel REST API.

    This client provides a clean, async interface to the SPAN Panel API
    using the generated httpx-based OpenAPI client as the underlying transport.

    Example:
        async with SpanPanelClient("192.168.1.100") as client:
            # Authenticate
            auth = await client.authenticate("my-app", "My Application")

            # Get panel status
            status = await client.get_status()
            print(f"Panel: {status.system.manufacturer}")

            # Get circuits
            circuits = await client.get_circuits()
            for circuit_id, circuit in circuits.circuits.additional_properties.items():
                print(f"{circuit.name}: {circuit.instant_power_w}W")
    """

    def __init__(
        self,
        host: str,
        port: int = 80,
        timeout: float = 30.0,
        use_ssl: bool = False,
    ) -> None:
        """Initialize the SPAN Panel client.

        Args:
            host: IP address or hostname of the SPAN Panel
            port: Port number (default: 80)
            timeout: Request timeout in seconds (default: 30.0)
            use_ssl: Whether to use HTTPS (default: False)
        """
        self._host = host
        self._port = port
        self._timeout = timeout
        self._use_ssl = use_ssl

        # Build base URL
        scheme = "https" if use_ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"

        # HTTP client - starts as unauthenticated, upgrades to authenticated after login
        self._client: Client | AuthenticatedClient | None = None
        self._access_token: str | None = None

    def _get_client(self) -> AuthenticatedClient | Client:
        """Get the appropriate HTTP client based on whether we have an access token."""
        if self._access_token:
            # We have a token, use authenticated client
            if self._client is None or not isinstance(
                self._client, AuthenticatedClient
            ):
                self._client = AuthenticatedClient(
                    base_url=self._base_url,
                    token=self._access_token,
                    timeout=httpx.Timeout(self._timeout),
                    verify_ssl=self._use_ssl,
                )
            return self._client
        else:
            # No token, use unauthenticated client
            return self._get_unauthenticated_client()

    def _get_unauthenticated_client(self) -> Client:
        """Get an unauthenticated client for operations that don't require auth."""
        return Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            verify_ssl=self._use_ssl,
        )

    async def __aenter__(self) -> SpanPanelClient:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        if self._client:
            # The generated client has async context manager support
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:  # nosec B110
                # Ignore errors during cleanup
                pass
            self._client = None

    def set_access_token(self, token: str) -> None:
        """Set the access token for API authentication."""
        self._access_token = token
        # Reset client to force recreation with new token
        self._client = None

    def _get_client_for_endpoint(
        self, requires_auth: bool = True
    ) -> AuthenticatedClient | Client:
        """Get the appropriate client for an endpoint.

        Args:
            requires_auth: Whether the endpoint requires authentication

        Returns:
            AuthenticatedClient if authentication is required or available,
            Client if no authentication is needed
        """
        if requires_auth and not self._access_token:
            # Endpoint requires auth but we don't have a token
            raise SpanPanelAuthError(
                "This endpoint requires authentication. Call authenticate() first."
            )

        return self._get_client()

    # Authentication Methods
    async def authenticate(self, name: str, description: str = "") -> AuthOut:
        """Register and authenticate a new API client.

        Args:
            name: Client name
            description: Optional client description

        Returns:
            AuthOut containing access token
        """
        # Use authenticated client (with empty token) for registration
        client = self._get_client()
        async with client:
            auth_in = AuthIn(name=name, description=description)
            try:
                # Type cast needed because generated API has overly strict type hints
                response = await generate_jwt_api_v1_auth_register_post.asyncio(
                    client=cast(AuthenticatedClient, client), body=auth_in
                )
                # Handle response - could be AuthOut, HTTPValidationError, or None
                if response is None:
                    raise SpanPanelAPIError("Authentication failed - no response")
                elif isinstance(response, HTTPValidationError):
                    raise SpanPanelAPIError(
                        f"Validation error during authentication: {response}"
                    )
                elif hasattr(response, "access_token"):
                    # Store the token for future requests (works for both AuthOut and mocks)
                    self.set_access_token(response.access_token)
                    return response
                else:
                    raise SpanPanelAPIError(
                        f"Unexpected response type: {type(response)}"
                    )
            except httpx.ConnectError as e:
                raise SpanPanelConnectionError(
                    f"Failed to connect to {self._host}"
                ) from e
            except httpx.TimeoutException as e:
                raise SpanPanelTimeoutError(
                    f"Request timed out after {self._timeout}s"
                ) from e
            except Exception as e:
                if "401" in str(e) or "Unauthorized" in str(e):
                    raise SpanPanelAuthError("Authentication failed") from e
                raise SpanPanelAPIError(f"API error: {e}") from e

    # Panel Status and Info
    async def get_status(self) -> StatusOut | None:
        """Get complete panel system status (does not require authentication)."""
        client = self._get_client_for_endpoint(requires_auth=False)
        async with client:
            try:
                # Status endpoint works with both authenticated and unauthenticated clients
                return await system_status_api_v1_status_get.asyncio(
                    client=cast(AuthenticatedClient, client)
                )
            except httpx.ConnectError as e:
                raise SpanPanelConnectionError(
                    f"Failed to connect to {self._host}"
                ) from e
            except httpx.TimeoutException as e:
                raise SpanPanelTimeoutError(
                    f"Request timed out after {self._timeout}s"
                ) from e
            except Exception as e:
                if "401" in str(e) or "Unauthorized" in str(e):
                    raise SpanPanelAuthError("Authentication required") from e
                raise SpanPanelAPIError(f"API error: {e}") from e

    async def get_panel_state(self) -> PanelState | None:
        """Get panel state information."""
        client = self._get_client()
        async with client:
            try:
                # Type cast needed because generated API has overly strict type hints
                return await get_panel_state_api_v1_panel_get.asyncio(
                    client=cast(AuthenticatedClient, client)
                )
            except httpx.ConnectError as e:
                raise SpanPanelConnectionError(
                    f"Failed to connect to {self._host}"
                ) from e
            except httpx.TimeoutException as e:
                raise SpanPanelTimeoutError(
                    f"Request timed out after {self._timeout}s"
                ) from e
            except Exception as e:
                if "401" in str(e) or "Unauthorized" in str(e):
                    raise SpanPanelAuthError("Authentication required") from e
                raise SpanPanelAPIError(f"API error: {e}") from e

    async def get_circuits(self) -> CircuitsOut | None:
        """Get all circuits and their current state."""
        client = self._get_client()
        async with client:
            try:
                # Type cast needed because generated API has overly strict type hints
                return await get_circuits_api_v1_circuits_get.asyncio(
                    client=cast(AuthenticatedClient, client)
                )
            except httpx.ConnectError as e:
                raise SpanPanelConnectionError(
                    f"Failed to connect to {self._host}"
                ) from e
            except httpx.TimeoutException as e:
                raise SpanPanelTimeoutError(
                    f"Request timed out after {self._timeout}s"
                ) from e
            except Exception as e:
                if "401" in str(e) or "Unauthorized" in str(e):
                    raise SpanPanelAuthError("Authentication required") from e
                raise SpanPanelAPIError(f"API error: {e}") from e

    async def get_storage_soe(self) -> BatteryStorage | None:
        """Get storage state of energy (SOE) data."""
        client = self._get_client()
        async with client:
            try:
                # Type cast needed because generated API has overly strict type hints
                return await get_storage_soe_api_v1_storage_soe_get.asyncio(
                    client=cast(AuthenticatedClient, client)
                )
            except httpx.ConnectError as e:
                raise SpanPanelConnectionError(
                    f"Failed to connect to {self._host}"
                ) from e
            except httpx.TimeoutException as e:
                raise SpanPanelTimeoutError(
                    f"Request timed out after {self._timeout}s"
                ) from e
            except Exception as e:
                if "401" in str(e) or "Unauthorized" in str(e):
                    raise SpanPanelAuthError("Authentication required") from e
                raise SpanPanelAPIError(f"API error: {e}") from e

    async def set_circuit_relay(self, circuit_id: str, state: str) -> Any:
        """Control circuit relay state.

        Args:
            circuit_id: Circuit identifier
            state: Relay state ("OPEN" or "CLOSED")
        """
        client = self._get_client()
        async with client:
            try:
                # Convert string to enum
                relay_state = (
                    RelayState.OPEN if state.upper() == "OPEN" else RelayState.CLOSED
                )
                relay_in = RelayStateIn(relay_state=relay_state)

                # Create the body object with just the relay state
                body = BodySetCircuitStateApiV1CircuitsCircuitIdPost(
                    relay_state_in=relay_in
                )

                # Type cast needed because generated API has overly strict type hints
                return await set_circuit_state_api_v_1_circuits_circuit_id_post.asyncio(
                    client=cast(AuthenticatedClient, client),
                    circuit_id=circuit_id,
                    body=body,
                )
            except httpx.ConnectError as e:
                raise SpanPanelConnectionError(
                    f"Failed to connect to {self._host}"
                ) from e
            except httpx.TimeoutException as e:
                raise SpanPanelTimeoutError(
                    f"Request timed out after {self._timeout}s"
                ) from e
            except Exception as e:
                if "401" in str(e) or "Unauthorized" in str(e):
                    raise SpanPanelAuthError("Authentication required") from e
                raise SpanPanelAPIError(f"API error: {e}") from e

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> Any:
        """Set circuit priority.

        Args:
            circuit_id: Circuit identifier
            priority: Priority level
        """
        client = self._get_client()
        async with client:
            try:
                # Convert string to enum - handle various priority formats
                priority_enum = Priority(priority.upper())
                priority_in = PriorityIn(priority=priority_enum)

                # Create the body object with just the priority
                body = BodySetCircuitStateApiV1CircuitsCircuitIdPost(
                    priority_in=priority_in
                )

                # Type cast needed because generated API has overly strict type hints
                return await set_circuit_state_api_v_1_circuits_circuit_id_post.asyncio(
                    client=cast(AuthenticatedClient, client),
                    circuit_id=circuit_id,
                    body=body,
                )
            except httpx.ConnectError as e:
                raise SpanPanelConnectionError(
                    f"Failed to connect to {self._host}"
                ) from e
            except httpx.TimeoutException as e:
                raise SpanPanelTimeoutError(
                    f"Request timed out after {self._timeout}s"
                ) from e
            except Exception as e:
                if "401" in str(e) or "Unauthorized" in str(e):
                    raise SpanPanelAuthError("Authentication required") from e
                raise SpanPanelAPIError(f"API error: {e}") from e
