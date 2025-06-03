"""SPAN Panel API Client.

This module provides a high-level async client for the SPAN Panel REST API.
It wraps the generated OpenAPI client to provide a more convenient interface.
"""

from __future__ import annotations

import asyncio
from typing import Any, NoReturn, cast

import httpx

from .const import AUTH_ERROR_CODES, RETRIABLE_ERROR_CODES, SERVER_ERROR_CODES
from .exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
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
    from .generated_client.errors import UnexpectedStatus
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


# Remove the RetryConfig class - using simple parameters instead


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
        # Retry configuration - simple parameters
        retries: int = 0,  # Default to 0 retries for simplicity
        retry_timeout: float = 0.5,  # How long to wait between retry attempts
        retry_backoff_multiplier: float = 2.0,
    ) -> None:
        """Initialize the SPAN Panel client.

        Args:
            host: IP address or hostname of the SPAN Panel
            port: Port number (default: 80)
            timeout: Request timeout in seconds (default: 30.0)
            use_ssl: Whether to use HTTPS (default: False)
            retries: Number of retries (0 = no retries, 1 = 1 retry, etc.)
            retry_timeout: Timeout between retry attempts in seconds
            retry_backoff_multiplier: Exponential backoff multiplier
        """
        self._host = host
        self._port = port
        self._timeout = timeout
        self._use_ssl = use_ssl

        # Simple retry configuration - validate and store
        if retries < 0:
            raise ValueError("retries must be non-negative")
        if retry_timeout < 0:
            raise ValueError("retry_timeout must be non-negative")
        if retry_backoff_multiplier < 1:
            raise ValueError("retry_backoff_multiplier must be at least 1")

        self._retries = retries
        self._retry_timeout = retry_timeout
        self._retry_backoff_multiplier = retry_backoff_multiplier

        # Build base URL
        scheme = "https" if use_ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"

        # HTTP client - starts as unauthenticated, upgrades to authenticated after login
        self._client: Client | AuthenticatedClient | None = None
        self._access_token: str | None = None

        # Context tracking - critical for preventing "Cannot open a client instance more than once"
        self._in_context: bool = False
        self._httpx_client_owned: bool = False

    # Properties for querying and setting retry configuration
    @property
    def retries(self) -> int:
        """Get the number of retries."""
        return self._retries

    @retries.setter
    def retries(self, value: int) -> None:
        """Set the number of retries."""
        if value < 0:
            raise ValueError("retries must be non-negative")
        self._retries = value

    @property
    def retry_timeout(self) -> float:
        """Get the timeout between retries in seconds."""
        return self._retry_timeout

    @retry_timeout.setter
    def retry_timeout(self, value: float) -> None:
        """Set the timeout between retries in seconds."""
        if value < 0:
            raise ValueError("retry_timeout must be non-negative")
        self._retry_timeout = value

    @property
    def retry_backoff_multiplier(self) -> float:
        """Get the exponential backoff multiplier."""
        return self._retry_backoff_multiplier

    @retry_backoff_multiplier.setter
    def retry_backoff_multiplier(self, value: float) -> None:
        """Set the exponential backoff multiplier."""
        if value < 1:
            raise ValueError("retry_backoff_multiplier must be at least 1")
        self._retry_backoff_multiplier = value

    def _get_client(self) -> AuthenticatedClient | Client:
        """Get the appropriate HTTP client based on whether we have an access token."""
        if self._access_token:
            # We have a token, use authenticated client
            if self._client is None or not isinstance(
                self._client, AuthenticatedClient
            ):
                # Create a new authenticated client
                self._client = AuthenticatedClient(
                    base_url=self._base_url,
                    token=self._access_token,
                    timeout=httpx.Timeout(self._timeout),
                    verify_ssl=self._use_ssl,
                    raise_on_unexpected_status=True,
                )
                # Only set _httpx_client_owned if we're not in a context
                # This prevents us from managing a client that's already managed by a context
                self._httpx_client_owned = not self._in_context
            return self._client
        else:
            # No token, use unauthenticated client
            return self._get_unauthenticated_client()

    def _get_unauthenticated_client(self) -> Client:
        """Get an unauthenticated client for operations that don't require auth."""
        client = Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            verify_ssl=self._use_ssl,
            raise_on_unexpected_status=True,
        )
        # Only set _httpx_client_owned if we're not in a context
        if not self._in_context and self._client is None:
            self._client = client
            self._httpx_client_owned = True
        return client

    async def __aenter__(self) -> SpanPanelClient:
        """Async context manager entry."""
        if self._in_context:
            # Already in context, this is a programming error
            raise RuntimeError("Cannot open a client instance more than once")

        self._in_context = True

        # Initialize the client when entering context
        if self._client is None:
            self._client = self._get_client()

        # Initialize the underlying httpx client
        try:
            await self._client.__aenter__()
            # Mark that we own the httpx client lifecycle
            self._httpx_client_owned = True
        except Exception as e:
            # On context entry failure, reset state
            self._in_context = False
            self._httpx_client_owned = False
            # Re-raise so caller knows context entry failed
            raise RuntimeError(f"Failed to enter client context: {e}") from e

        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        try:
            await self.close()
        finally:
            # Always mark as out of context, even if close() fails
            self._in_context = False

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
        self._in_context = False

    def set_access_token(self, token: str) -> None:
        """Set the access token for API authentication.

        Updates the client's authentication token. If the client is already in a
        context manager, it will safely upgrade the client from unauthenticated
        to authenticated without disrupting the context.

        Args:
            token: The JWT access token for API authentication
        """
        if token == self._access_token:
            # Token hasn't changed, nothing to do
            return

        self._access_token = token

        # Handle token change based on context state
        if not self._in_context:
            # Outside context: safe to reset client completely
            if self._client is not None:
                # Clear client so it will be recreated on next use
                self._client = None
                self._httpx_client_owned = False
        elif self._client is not None:
            # Inside context: need to carefully upgrade client while preserving httpx instance
            if not isinstance(self._client, AuthenticatedClient):
                # Need to upgrade from Client to AuthenticatedClient
                old_async_client = getattr(self._client, "_async_client", None)
                self._client = AuthenticatedClient(
                    base_url=self._base_url,
                    token=token,
                    timeout=httpx.Timeout(self._timeout),
                    verify_ssl=self._use_ssl,
                    raise_on_unexpected_status=True,
                )
                # Preserve the existing httpx async client to avoid double context issues
                if old_async_client is not None:
                    self._client._async_client = old_async_client
                    # Update the Authorization header on the existing httpx client
                    header_value = f"{self._client.prefix} {self._client.token}"
                    old_async_client.headers[self._client.auth_header_name] = (
                        header_value
                    )
            else:
                # Already an AuthenticatedClient, just update the token
                self._client.token = token
                # Update the Authorization header on existing httpx clients
                header_value = f"{self._client.prefix} {self._client.token}"
                if self._client._async_client is not None:
                    self._client._async_client.headers[
                        self._client.auth_header_name
                    ] = header_value
                if self._client._client is not None:
                    self._client._client.headers[self._client.auth_header_name] = (
                        header_value
                    )

    def _handle_unexpected_status(self, e: UnexpectedStatus) -> NoReturn:
        """Convert UnexpectedStatus to appropriate SpanPanel exception.

        Args:
            e: The UnexpectedStatus to convert

        Raises:
            SpanPanelAuthError: For 401/403 errors
            SpanPanelRetriableError: For 502/503/504 errors (retriable)
            SpanPanelServerError: For 500 errors (non-retriable)
            SpanPanelAPIError: For all other HTTP errors
        """
        if e.status_code in AUTH_ERROR_CODES:
            raise SpanPanelAuthError("Authentication required") from e
        elif e.status_code in RETRIABLE_ERROR_CODES:
            raise SpanPanelRetriableError(
                f"Retriable server error {e.status_code}: {e}", e.status_code
            ) from e
        elif e.status_code in SERVER_ERROR_CODES:
            raise SpanPanelServerError(
                f"Server error {e.status_code}: {e}", e.status_code
            ) from e
        else:
            raise SpanPanelAPIError(f"HTTP {e.status_code}: {e}", e.status_code) from e

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

        # If we're in a context, always use the existing client
        if self._in_context:
            if self._client is None:
                raise SpanPanelAPIError(
                    "Client is None while in context - this indicates a lifecycle issue"
                )
            # Verify we have the right client type for the request
            if (
                requires_auth
                and self._access_token
                and not isinstance(self._client, AuthenticatedClient)
            ):
                # We need auth but have wrong client type - this shouldn't happen after our fix
                raise SpanPanelAPIError(
                    "Client type mismatch: need AuthenticatedClient but have Client"
                )
            return self._client

        # Not in context, create a client if needed
        if self._client is None:
            self._client = self._get_client()
        return self._client

    async def _retry_with_backoff(self, operation, *args, **kwargs):
        """Execute an operation with retry logic and exponential backoff.

        Args:
            operation: The async function to call
            *args: Arguments for the operation
            **kwargs: Keyword arguments for the operation

        Returns:
            The result of the operation

        Raises:
            The final exception if all retries are exhausted
        """
        retry_status_codes = set(RETRIABLE_ERROR_CODES)  # Retriable HTTP status codes
        max_attempts = (
            self._retries + 1
        )  # retries=0 means 1 attempt, retries=1 means 2 attempts, etc.

        for attempt in range(max_attempts):
            try:
                return await operation(*args, **kwargs)
            except UnexpectedStatus as e:
                # Only retry specific HTTP status codes that are typically transient
                if e.status_code in retry_status_codes and attempt < max_attempts - 1:
                    delay = self._retry_timeout * (
                        self._retry_backoff_multiplier**attempt
                    )  # Exponential backoff
                    await asyncio.sleep(delay)
                    continue
                # Not retriable or last attempt - re-raise
                raise
            except httpx.HTTPStatusError as e:
                # Only retry specific HTTP status codes that are typically transient
                if (
                    e.response.status_code in retry_status_codes
                    and attempt < max_attempts - 1
                ):
                    delay = self._retry_timeout * (
                        self._retry_backoff_multiplier**attempt
                    )  # Exponential backoff
                    await asyncio.sleep(delay)
                    continue
                # Not retriable or last attempt - re-raise
                raise
            except (httpx.ConnectError, httpx.TimeoutException):
                # Network/timeout errors are always retriable
                if attempt < max_attempts - 1:
                    delay = self._retry_timeout * (
                        self._retry_backoff_multiplier**attempt
                    )  # Exponential backoff
                    await asyncio.sleep(delay)
                    continue
                # Last attempt - re-raise
                raise

    # Authentication Methods
    async def authenticate(self, name: str, description: str = "") -> AuthOut:
        """Register and authenticate a new API client.

        Args:
            name: Client name
            description: Optional client description

        Returns:
            AuthOut containing access token
        """
        # Use unauthenticated client for registration
        client = self._get_unauthenticated_client()
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
                raise SpanPanelAPIError(f"Unexpected response type: {type(response)}")
        except UnexpectedStatus as e:
            # Convert UnexpectedStatus to appropriate SpanPanel exception
            # Special case for auth endpoint - 401/403 here means auth failed
            if e.status_code in AUTH_ERROR_CODES:
                raise SpanPanelAuthError("Authentication failed") from e
            elif e.status_code in RETRIABLE_ERROR_CODES:
                raise SpanPanelRetriableError(
                    f"Retriable server error {e.status_code}: {e}", e.status_code
                ) from e
            elif e.status_code in SERVER_ERROR_CODES:
                raise SpanPanelServerError(
                    f"Server error {e.status_code}: {e}", e.status_code
                ) from e
            else:
                raise SpanPanelAPIError(
                    f"HTTP {e.status_code}: {e}", e.status_code
                ) from e
        except httpx.HTTPStatusError as e:
            # Convert HTTPStatusError to UnexpectedStatus and handle appropriately
            # Special case for auth endpoint - 401/403 here means auth failed
            if e.response.status_code in AUTH_ERROR_CODES:
                raise SpanPanelAuthError("Authentication failed") from e
            elif e.response.status_code in RETRIABLE_ERROR_CODES:
                raise SpanPanelRetriableError(
                    f"Retriable server error {e.response.status_code}: {e}",
                    e.response.status_code,
                ) from e
            elif e.response.status_code in SERVER_ERROR_CODES:
                raise SpanPanelServerError(
                    f"Server error {e.response.status_code}: {e}",
                    e.response.status_code,
                ) from e
            else:
                raise SpanPanelAPIError(
                    f"HTTP {e.response.status_code}: {e}", e.response.status_code
                ) from e
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except Exception as e:
            raise SpanPanelAPIError(f"API error: {e}") from e

    # Panel Status and Info
    async def get_status(self) -> StatusOut:
        """Get complete panel system status (does not require authentication)."""

        async def _get_status_operation():
            client = self._get_client_for_endpoint(requires_auth=False)
            # Status endpoint works with both authenticated and unauthenticated clients
            return await system_status_api_v1_status_get.asyncio(
                client=cast(AuthenticatedClient, client)
            )

        try:
            return await self._retry_with_backoff(_get_status_operation)
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(
                e.response.status_code, e.response.content
            )
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except ValueError as e:
            # Handle Pydantic validation errors and other ValueError instances
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def get_panel_state(self) -> PanelState:
        """Get panel state information."""

        async def _get_panel_state_operation():
            client = self._get_client_for_endpoint(requires_auth=True)
            # Type cast needed because generated API has overly strict type hints
            return await get_panel_state_api_v1_panel_get.asyncio(
                client=cast(AuthenticatedClient, client)
            )

        try:
            return await self._retry_with_backoff(_get_panel_state_operation)
        except SpanPanelAuthError:
            # Pass through auth errors directly
            raise
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(
                e.response.status_code, e.response.content
            )
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except ValueError as e:
            # Handle Pydantic validation errors and other ValueError instances
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            if "401 Unauthorized" in str(e):
                raise SpanPanelAuthError("Authentication required") from e
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def get_circuits(self) -> CircuitsOut:
        """Get all circuits and their current state."""

        async def _get_circuits_operation():
            client = self._get_client_for_endpoint(requires_auth=True)
            # Type cast needed because generated API has overly strict type hints
            return await get_circuits_api_v1_circuits_get.asyncio(
                client=cast(AuthenticatedClient, client)
            )

        try:
            return await self._retry_with_backoff(_get_circuits_operation)
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(
                e.response.status_code, e.response.content
            )
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except ValueError as e:
            # Handle Pydantic validation errors and other ValueError instances
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def get_storage_soe(self) -> BatteryStorage:
        """Get storage state of energy (SOE) data."""

        async def _get_storage_soe_operation():
            client = self._get_client_for_endpoint(requires_auth=True)
            # Type cast needed because generated API has overly strict type hints
            return await get_storage_soe_api_v1_storage_soe_get.asyncio(
                client=cast(AuthenticatedClient, client)
            )

        try:
            return await self._retry_with_backoff(_get_storage_soe_operation)
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(
                e.response.status_code, e.response.content
            )
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except ValueError as e:
            # Handle Pydantic validation errors and other ValueError instances
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def set_circuit_relay(self, circuit_id: str, state: str) -> Any:
        """Control circuit relay state.

        Args:
            circuit_id: Circuit identifier
            state: Relay state ("OPEN" or "CLOSED")

        Returns:
            Response from the API

        Raises:
            SpanPanelAPIError: For validation or API errors
            SpanPanelAuthError: If authentication is required
            SpanPanelConnectionError: For connection failures
            SpanPanelTimeoutError: If the request times out
            SpanPanelServerError: For 5xx server errors
            SpanPanelRetriableError: For transient server errors
        """

        async def _set_circuit_relay_operation():
            client = self._get_client_for_endpoint(requires_auth=True)

            # Convert string to enum - explicitly handle invalid values
            try:
                relay_state = RelayState(state.upper())
            except ValueError as e:
                # Wrap ValueError in a more descriptive error
                raise SpanPanelAPIError(
                    f"Invalid relay state '{state}'. Must be one of: OPEN, CLOSED",
                ) from e

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

        try:
            return await self._retry_with_backoff(_set_circuit_relay_operation)
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(
                e.response.status_code, e.response.content
            )
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except ValueError as e:
            # Specifically handle ValueError from enum conversion
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> Any:
        """Set circuit priority.

        Args:
            circuit_id: Circuit identifier
            priority: Priority level (MUST_HAVE, NICE_TO_HAVE)

        Returns:
            Response from the API

        Raises:
            SpanPanelAPIError: For validation or API errors
            SpanPanelAuthError: If authentication is required
            SpanPanelConnectionError: For connection failures
            SpanPanelTimeoutError: If the request times out
            SpanPanelServerError: For 5xx server errors
            SpanPanelRetriableError: For transient server errors
        """

        async def _set_circuit_priority_operation():
            client = self._get_client_for_endpoint(requires_auth=True)

            # Convert string to enum - explicitly handle invalid values
            try:
                priority_enum = Priority(priority.upper())
            except ValueError as e:
                # Wrap ValueError in a more descriptive error matching test expectations
                raise SpanPanelAPIError(
                    f"API error: '{priority}' is not a valid Priority"
                ) from e

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

        try:
            return await self._retry_with_backoff(_set_circuit_priority_operation)
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(
                e.response.status_code, e.response.content
            )
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except ValueError as e:
            # Specifically handle ValueError from enum conversion
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e
