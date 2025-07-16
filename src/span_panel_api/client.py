"""SPAN Panel API Client.

This module provides a high-level async client for the SPAN Panel REST API.
It wraps the generated OpenAPI client to provide a more convenient interface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import time
from typing import Any, NoReturn, TypeVar, cast

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
from .simulation import BranchVariation, CircuitVariation, DynamicSimulationEngine, PanelVariation, StatusVariation

T = TypeVar("T")

# Constants
BEARER_TOKEN_TYPE = "Bearer"  # OAuth2 Bearer token type specification  # nosec B105

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
        Branch,
        Circuit,
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


class TimeWindowCache:
    """Time-based cache for API data to avoid redundant API calls.

    This cache implements a simple time-window based caching strategy:

    Cache Window Behavior:
    1. Cache window is created only when successful data is obtained from an API call
    2. During an active cache window, all requests return cached data (no network calls)
    3. Cache window expires after the configured duration (default 1 second)
    4. After expiration, there is a gap with no active cache window
    5. Next request goes to the network, and if successful, creates a new cache window

    Cache Lifecycle:
    - Active Window: [successful_response] ----window_duration----> [expires]
    - Gap Period: [no cache exists - network calls required]
    - New Window: [successful_response] ----window_duration----> [expires]

    Retry Interaction:
    - If network calls fail and retry, the cache window may expire during retries
    - When retry eventually succeeds, it creates a fresh cache window
    - This is acceptable behavior - slow networks may cause cache expiration

    Thread Safety:
    - This implementation is not thread-safe
    - Intended for single-threaded async usage
    """

    def __init__(self, window_duration: float = 1.0) -> None:
        """Initialize the cache.

        Args:
            window_duration: Cache window duration in seconds (default: 1.0)
                           Set to 0 to disable caching entirely
        """
        if window_duration < 0:
            raise ValueError("Cache window duration must be non-negative")

        self._window_duration = window_duration
        self._cache_entries: dict[str, tuple[Any, float]] = {}

    def get_cached_data(self, cache_key: str) -> Any | None:
        """Get cached data if within the cache window, otherwise None.

        Args:
            cache_key: Unique identifier for the cached data

        Returns:
            Cached data if valid, None if expired or not found
        """
        # If cache window is 0, caching is disabled
        if self._window_duration == 0:
            return None

        if cache_key not in self._cache_entries:
            return None

        cached_data, cache_timestamp = self._cache_entries[cache_key]

        # Check if cache window has expired
        elapsed = time.time() - cache_timestamp
        if elapsed > self._window_duration:
            # Cache expired - remove it and return None
            del self._cache_entries[cache_key]
            return None

        return cached_data

    def set_cached_data(self, cache_key: str, data: Any) -> None:
        """Store successful response data and start a new cache window.

        Args:
            cache_key: Unique identifier for the cached data
            data: Data to cache
        """
        # If cache window is 0, caching is disabled - don't store anything
        if self._window_duration == 0:
            return

        self._cache_entries[cache_key] = (data, time.time())


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
        # Cache configuration
        cache_window: float = 1.0,  # Panel data cache window in seconds
        # Simulation configuration
        simulation_mode: bool = False,  # Enable simulation mode
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
            cache_window: Panel data cache window duration in seconds (default: 1.0)
            simulation_mode: Enable simulation mode for testing (default: False)
        """
        self._host = host
        self._port = port
        self._timeout = timeout
        self._use_ssl = use_ssl
        self._simulation_mode = simulation_mode

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

        # Initialize API data cache
        self._api_cache = TimeWindowCache(cache_window)

        # Initialize simulation engine if in simulation mode
        self._simulation_engine: DynamicSimulationEngine | None = None
        self._simulation_initialized = False
        if simulation_mode:
            # In simulation mode, use the host as the serial number for device identification
            self._simulation_engine = DynamicSimulationEngine(serial_number=host)

        # Build base URL
        scheme = "https" if use_ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"

        # HTTP client - starts as unauthenticated, upgrades to authenticated after login
        self._client: Client | AuthenticatedClient | None = None
        self._access_token: str | None = None

        # Context tracking - critical for preventing "Cannot open a client instance more than once"
        self._in_context: bool = False
        self._httpx_client_owned: bool = False

    async def _ensure_simulation_initialized(self) -> None:
        """Ensure simulation engine is properly initialized asynchronously."""
        if not self._simulation_mode or self._simulation_initialized:
            return

        if self._simulation_engine is not None:
            await self._simulation_engine.initialize_async()
            self._simulation_initialized = True

    def _convert_raw_to_circuits_out(self, raw_data: dict[str, Any]) -> CircuitsOut:
        """Convert raw simulation data to CircuitsOut model."""
        # This is a simplified conversion - in reality, you'd need to properly
        # construct the CircuitsOut object from the raw data
        return CircuitsOut.from_dict(raw_data)

    def _convert_raw_to_panel_state(self, raw_data: dict[str, Any]) -> PanelState:
        """Convert raw simulation data to PanelState model."""
        return PanelState.from_dict(raw_data)

    def _convert_raw_to_status_out(self, raw_data: dict[str, Any]) -> StatusOut:
        """Convert raw simulation data to StatusOut model."""
        return StatusOut.from_dict(raw_data)

    def _convert_raw_to_battery_storage(self, raw_data: dict[str, Any]) -> BatteryStorage:
        """Convert raw simulation data to BatteryStorage model."""
        return BatteryStorage.from_dict(raw_data)

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
            if self._client is None or not isinstance(self._client, AuthenticatedClient):
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

    async def __aexit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
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
            with suppress(Exception):
                await self._client.__aexit__(None, None, None)
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
                # Store reference to existing async client before creating new authenticated client
                old_async_client = None
                with suppress(Exception):
                    # Client may not have been initialized yet
                    old_async_client = self._client.get_async_httpx_client()

                self._client = AuthenticatedClient(
                    base_url=self._base_url,
                    token=token,
                    timeout=httpx.Timeout(self._timeout),
                    verify_ssl=self._use_ssl,
                    raise_on_unexpected_status=True,
                )
                # Preserve the existing httpx async client to avoid double context issues
                if old_async_client is not None:
                    self._client.set_async_httpx_client(old_async_client)
                    # Update the Authorization header on the existing httpx client
                    header_value = f"{self._client.prefix} {self._client.token}"
                    old_async_client.headers[self._client.auth_header_name] = header_value
            else:
                # Already an AuthenticatedClient, just update the token
                self._client.token = token
                # Update the Authorization header on existing httpx clients
                header_value = f"{self._client.prefix} {self._client.token}"
                with suppress(Exception):
                    async_client = self._client.get_async_httpx_client()
                    async_client.headers[self._client.auth_header_name] = header_value
                with suppress(Exception):
                    sync_client = self._client.get_httpx_client()
                    sync_client.headers[self._client.auth_header_name] = header_value

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
        if e.status_code in RETRIABLE_ERROR_CODES:
            raise SpanPanelRetriableError(f"Retriable server error {e.status_code}: {e}", e.status_code) from e
        if e.status_code in SERVER_ERROR_CODES:
            raise SpanPanelServerError(f"Server error {e.status_code}: {e}", e.status_code) from e
        raise SpanPanelAPIError(f"HTTP {e.status_code}: {e}", e.status_code) from e

    def _get_client_for_endpoint(self, requires_auth: bool = True) -> AuthenticatedClient | Client:
        """Get the appropriate client for an endpoint.

        Args:
            requires_auth: Whether the endpoint requires authentication

        Returns:
            AuthenticatedClient if authentication is required or available,
            Client if no authentication is needed
        """
        if requires_auth and not self._access_token:
            # Endpoint requires auth but we don't have a token
            raise SpanPanelAuthError("This endpoint requires authentication. Call authenticate() first.")

        # If we're in a context, always use the existing client
        if self._in_context:
            if self._client is None:
                raise SpanPanelAPIError("Client is None while in context - this indicates a lifecycle issue")
            # Verify we have the right client type for the request
            if requires_auth and self._access_token and not isinstance(self._client, AuthenticatedClient):
                # We need auth but have wrong client type - this shouldn't happen after our fix
                raise SpanPanelAPIError("Client type mismatch: need AuthenticatedClient but have Client")
            return self._client

        # Not in context, create a client if needed
        if self._client is None:
            self._client = self._get_client()
        return self._client

    async def _retry_with_backoff(self, operation: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
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
        max_attempts = self._retries + 1  # retries=0 means 1 attempt, retries=1 means 2 attempts, etc.

        for attempt in range(max_attempts):
            try:
                return await operation(*args, **kwargs)
            except UnexpectedStatus as e:
                # Only retry specific HTTP status codes that are typically transient
                if e.status_code in retry_status_codes and attempt < max_attempts - 1:
                    delay = self._retry_timeout * (self._retry_backoff_multiplier**attempt)  # Exponential backoff
                    await asyncio.sleep(delay)
                    continue
                # Not retriable or last attempt - re-raise
                raise
            except httpx.HTTPStatusError as e:
                # Only retry specific HTTP status codes that are typically transient
                if e.response.status_code in retry_status_codes and attempt < max_attempts - 1:
                    delay = self._retry_timeout * (self._retry_backoff_multiplier**attempt)  # Exponential backoff
                    await asyncio.sleep(delay)
                    continue
                # Not retriable or last attempt - re-raise
                raise
            except (httpx.ConnectError, httpx.TimeoutException):
                # Network/timeout errors are always retriable
                if attempt < max_attempts - 1:
                    delay = self._retry_timeout * (self._retry_backoff_multiplier**attempt)  # Exponential backoff
                    await asyncio.sleep(delay)
                    continue
                # Last attempt - re-raise
                raise

        # This should never be reached, but required for mypy type checking
        raise SpanPanelAPIError("Retry operation completed without success or exception")

    # Authentication Methods
    async def authenticate(self, name: str, description: str = "") -> AuthOut:
        """Register and authenticate a new API client.

        Args:
            name: Client name
            description: Optional client description

        Returns:
            AuthOut containing access token
        """
        # In simulation mode, return a mock authentication response
        if self._simulation_mode:
            # Create a mock authentication response
            mock_token = f"sim-token-{name}-{int(time.time())}"
            current_time_ms = int(time.time() * 1000)
            auth_out = AuthOut(access_token=mock_token, token_type=BEARER_TOKEN_TYPE, iat_ms=current_time_ms)
            self.set_access_token(mock_token)
            return auth_out

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
            if isinstance(response, HTTPValidationError):
                raise SpanPanelAPIError(f"Validation error during authentication: {response}")
            if hasattr(response, "access_token"):
                # Store the token for future requests (works for both AuthOut and mocks)
                self.set_access_token(response.access_token)
                return response
            raise SpanPanelAPIError(f"Unexpected response type: {type(response)}")
        except UnexpectedStatus as e:
            # Convert UnexpectedStatus to appropriate SpanPanel exception
            # Special case for auth endpoint - 401/403 here means auth failed
            if e.status_code in AUTH_ERROR_CODES:
                raise SpanPanelAuthError("Authentication failed") from e
            if e.status_code in RETRIABLE_ERROR_CODES:
                raise SpanPanelRetriableError(f"Retriable server error {e.status_code}: {e}", e.status_code) from e
            if e.status_code in SERVER_ERROR_CODES:
                raise SpanPanelServerError(f"Server error {e.status_code}: {e}", e.status_code) from e
            raise SpanPanelAPIError(f"HTTP {e.status_code}: {e}", e.status_code) from e
        except httpx.HTTPStatusError as e:
            # Convert HTTPStatusError to UnexpectedStatus and handle appropriately
            # Special case for auth endpoint - 401/403 here means auth failed
            if e.response.status_code in AUTH_ERROR_CODES:
                raise SpanPanelAuthError("Authentication failed") from e
            if e.response.status_code in RETRIABLE_ERROR_CODES:
                raise SpanPanelRetriableError(
                    f"Retriable server error {e.response.status_code}: {e}", e.response.status_code
                ) from e
            if e.response.status_code in SERVER_ERROR_CODES:
                raise SpanPanelServerError(f"Server error {e.response.status_code}: {e}", e.response.status_code) from e
            raise SpanPanelAPIError(f"HTTP {e.response.status_code}: {e}", e.response.status_code) from e
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
        except Exception as e:
            raise SpanPanelAPIError(f"API error: {e}") from e

    # Panel Status and Info
    async def get_status(self, variations: StatusVariation | None = None) -> StatusOut:
        """Get complete panel system status (does not require authentication).

        Args:
            variations: Status field variations (simulation mode only)
        """
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_status_simulation(variations=variations)

        # In live mode, ignore variation parameters
        return await self._get_status_live()

    async def _get_status_simulation(self, variations: StatusVariation | None = None) -> StatusOut:
        """Get status data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Check cache first
        cache_key = f"status_sim_{hash(str(variations))}"
        cached_status = self._api_cache.get_cached_data(cache_key)
        if cached_status is not None:
            return cached_status

        # Get simulation data
        status_data = self._simulation_engine.get_status_data(variations=variations)

        # Convert to model object
        status_out = self._convert_raw_to_status_out(status_data)

        # Cache the result
        self._api_cache.set_cached_data(cache_key, status_out)

        return status_out

    async def _get_status_live(self) -> StatusOut:
        """Get status data from live panel."""

        async def _get_status_operation() -> StatusOut:
            client = self._get_client_for_endpoint(requires_auth=False)
            # Status endpoint works with both authenticated and unauthenticated clients
            result = await system_status_api_v1_status_get.asyncio(client=cast(AuthenticatedClient, client))
            # Since raise_on_unexpected_status=True, result should never be None
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
            return result

        # Check cache first
        cached_status = self._api_cache.get_cached_data("status")
        if cached_status is not None:
            return cached_status

        try:
            status = await self._retry_with_backoff(_get_status_operation)
            # Cache the successful response
            self._api_cache.set_cached_data("status", status)
            return status
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(e.response.status_code, e.response.content)
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
        except ValueError as e:
            # Handle Pydantic validation errors and other ValueError instances
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def get_panel_state(
        self,
        variations: dict[int, BranchVariation] | None = None,
        panel_variations: PanelVariation | None = None,
        global_power_variation: float | None = None,
    ) -> PanelState:
        """Get panel state information.

        Args:
            variations: Dict mapping branch_id to specific variations (simulation mode only)
            panel_variations: Panel-level variations (simulation mode only)
            global_power_variation: Apply to all branches (simulation mode only)
        """
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_panel_state_simulation(
                variations=variations, panel_variations=panel_variations, global_power_variation=global_power_variation
            )

        # In live mode, ignore variation parameters
        return await self._get_panel_state_live()

    async def _get_panel_state_simulation(
        self,
        variations: dict[int, BranchVariation] | None = None,
        panel_variations: PanelVariation | None = None,
        global_power_variation: float | None = None,
    ) -> PanelState:
        """Get panel state data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Check cache first
        cache_key = f"panel_sim_{hash(str(variations))}_{hash(str(panel_variations))}_{global_power_variation}"
        cached_panel = self._api_cache.get_cached_data(cache_key)
        if cached_panel is not None:
            return cached_panel

        # Get simulation data
        panel_data = self._simulation_engine.get_panel_state_data(
            variations=variations, panel_variations=panel_variations, global_power_variation=global_power_variation
        )

        # Convert to model object
        panel_state = self._convert_raw_to_panel_state(panel_data)

        # Cache the result
        self._api_cache.set_cached_data(cache_key, panel_state)

        return panel_state

    async def _get_panel_state_live(self) -> PanelState:
        """Get panel state data from live panel."""

        async def _get_panel_state_operation() -> PanelState:
            client = self._get_client_for_endpoint(requires_auth=True)
            # Type cast needed because generated API has overly strict type hints
            result = await get_panel_state_api_v1_panel_get.asyncio(client=cast(AuthenticatedClient, client))
            # Since raise_on_unexpected_status=True, result should never be None
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
            return result

        # Check cache first
        cached_state = self._api_cache.get_cached_data("panel_state")
        if cached_state is not None:
            return cached_state

        try:
            state = await self._retry_with_backoff(_get_panel_state_operation)
            # Cache the successful response
            self._api_cache.set_cached_data("panel_state", state)
            return state
        except SpanPanelAuthError:
            # Pass through auth errors directly
            raise
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(e.response.status_code, e.response.content)
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
        except ValueError as e:
            # Handle Pydantic validation errors and other ValueError instances
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            if "401 Unauthorized" in str(e):
                raise SpanPanelAuthError("Authentication required") from e
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def get_circuits(
        self,
        variations: dict[str, CircuitVariation] | None = None,
        global_power_variation: float | None = None,
        global_energy_variation: float | None = None,
        # Legacy support
        power_variation: float | None = None,
        energy_variation: float | None = None,
    ) -> CircuitsOut:
        """Get all circuits and their current state, including virtual circuits for unmapped tabs.

        Args:
            variations: Dict mapping circuit_id to specific variations (simulation mode only)
            global_power_variation: Apply to all circuits (simulation mode only)
            global_energy_variation: Apply to all circuits (simulation mode only)
            power_variation: Legacy parameter, use global_power_variation instead
            energy_variation: Legacy parameter, use global_energy_variation instead
        """
        # Handle legacy parameters
        if power_variation is not None and global_power_variation is None:
            global_power_variation = power_variation
        if energy_variation is not None and global_energy_variation is None:
            global_energy_variation = energy_variation

        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_circuits_simulation(
                variations=variations,
                global_power_variation=global_power_variation,
                global_energy_variation=global_energy_variation,
            )

        # In live mode, ignore all variation parameters and use live implementation
        return await self._get_circuits_live()

    async def _get_circuits_simulation(
        self,
        variations: dict[str, CircuitVariation] | None = None,
        global_power_variation: float | None = None,
        global_energy_variation: float | None = None,
    ) -> CircuitsOut:
        """Get circuits data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Check cache first
        cache_key = f"circuits_sim_{hash(str(variations))}_{global_power_variation}_{global_energy_variation}"
        cached_circuits = self._api_cache.get_cached_data(cache_key)
        if cached_circuits is not None:
            return cached_circuits

        # Get simulation data
        circuits_data = self._simulation_engine.get_circuits_data(
            variations=variations,
            global_power_variation=global_power_variation,
            global_energy_variation=global_energy_variation,
        )

        # Convert to model object
        circuits_out = self._convert_raw_to_circuits_out(circuits_data)

        # Apply unmapped tab logic (same as live mode)
        panel_state = await self.get_panel_state()

        # Find tabs already mapped to circuits
        mapped_tabs: set[int] = set()
        if hasattr(circuits_out, "circuits") and hasattr(circuits_out.circuits, "additional_properties"):
            for circuit in circuits_out.circuits.additional_properties.values():
                if hasattr(circuit, "tabs") and circuit.tabs is not None and str(circuit.tabs) != "UNSET":
                    if isinstance(circuit.tabs, list | tuple):
                        mapped_tabs.update(circuit.tabs)
                    elif isinstance(circuit.tabs, int):
                        mapped_tabs.add(circuit.tabs)

        # Create virtual circuits for unmapped tabs
        if hasattr(panel_state, "branches") and panel_state.branches:
            total_tabs = len(panel_state.branches)
            all_tabs = set(range(1, total_tabs + 1))
            unmapped_tabs = all_tabs - mapped_tabs

            for tab_num in unmapped_tabs:
                branch_idx = tab_num - 1
                if branch_idx < len(panel_state.branches):
                    branch = panel_state.branches[branch_idx]
                    virtual_circuit = self._create_unmapped_tab_circuit(branch, tab_num)
                    circuit_id = f"unmapped_tab_{tab_num}"
                    circuits_out.circuits.additional_properties[circuit_id] = virtual_circuit

        # Cache the result
        self._api_cache.set_cached_data(cache_key, circuits_out)

        return circuits_out

    async def _get_circuits_live(self) -> CircuitsOut:
        """Get circuits data from live panel."""

        async def _get_circuits_operation() -> CircuitsOut:
            # Get standard circuits response
            client = self._get_client_for_endpoint(requires_auth=True)
            result = await get_circuits_api_v1_circuits_get.asyncio(client=cast(AuthenticatedClient, client))
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")

            # Get panel state for branches data
            panel_state = await self.get_panel_state()

            # Find tabs already mapped to circuits
            mapped_tabs: set[int] = set()
            if hasattr(result, "circuits") and hasattr(result.circuits, "additional_properties"):
                for circuit in result.circuits.additional_properties.values():
                    if hasattr(circuit, "tabs") and circuit.tabs is not None and str(circuit.tabs) != "UNSET":
                        if isinstance(circuit.tabs, list | tuple):
                            mapped_tabs.update(circuit.tabs)
                        elif isinstance(circuit.tabs, int):
                            mapped_tabs.add(circuit.tabs)

            # Create virtual circuits for unmapped tabs
            if hasattr(panel_state, "branches") and panel_state.branches:
                total_tabs = len(panel_state.branches)
                all_tabs = set(range(1, total_tabs + 1))
                unmapped_tabs = all_tabs - mapped_tabs

                for tab_num in unmapped_tabs:
                    branch_idx = tab_num - 1
                    if branch_idx < len(panel_state.branches):
                        branch = panel_state.branches[branch_idx]
                        virtual_circuit = self._create_unmapped_tab_circuit(branch, tab_num)
                        circuit_id = f"unmapped_tab_{tab_num}"
                        result.circuits.additional_properties[circuit_id] = virtual_circuit

            return result

        # Check cache first
        cached_circuits = self._api_cache.get_cached_data("circuits")
        if cached_circuits is not None:
            return cached_circuits

        try:
            circuits = await self._retry_with_backoff(_get_circuits_operation)
            # Cache the successful response
            self._api_cache.set_cached_data("circuits", circuits)
            return circuits
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(e.response.status_code, e.response.content)
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
        except ValueError as e:
            # Handle Pydantic validation errors and other ValueError instances
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    def _create_unmapped_tab_circuit(self, branch: Branch, tab_number: int) -> Circuit:
        """Create a virtual circuit for an unmapped tab.

        Args:
            branch: The Branch object from panel state
            tab_number: The tab number (1-based)

        Returns:
            Circuit: A virtual circuit representing the unmapped tab
        """
        # Map branch data to circuit data
        # For solar inverters: imported energy = solar production, exported energy = grid export
        instant_power_w = getattr(branch, "instant_power_w", 0.0)
        imported_energy = getattr(branch, "imported_active_energy_wh", 0.0)
        exported_energy = getattr(branch, "exported_active_energy_wh", 0.0)

        # For solar tabs, imported energy represents production
        produced_energy_wh = imported_energy
        consumed_energy_wh = exported_energy

        # Get timestamps (use current time as fallback)
        current_time = int(time.time())
        instant_power_update_time_s = current_time
        energy_accum_update_time_s = current_time

        # Create the virtual circuit
        circuit = Circuit(
            id=f"unmapped_tab_{tab_number}",
            name=f"Unmapped Tab {tab_number}",
            relay_state=RelayState.UNKNOWN,
            instant_power_w=instant_power_w,
            instant_power_update_time_s=instant_power_update_time_s,
            produced_energy_wh=produced_energy_wh,
            consumed_energy_wh=consumed_energy_wh,
            energy_accum_update_time_s=energy_accum_update_time_s,
            priority=Priority.UNKNOWN,
            is_user_controllable=False,
            is_sheddable=False,
            is_never_backup=False,
            tabs=[tab_number],
        )

        return circuit

    async def get_storage_soe(self, soe_variation: float | None = None) -> BatteryStorage:
        """Get storage state of energy (SOE) data.

        Args:
            soe_variation: Battery percentage variation (simulation mode only)
        """
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_storage_soe_simulation(soe_variation=soe_variation)

        # In live mode, ignore variation parameters
        return await self._get_storage_soe_live()

    async def _get_storage_soe_simulation(self, soe_variation: float | None = None) -> BatteryStorage:
        """Get storage SOE data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Check cache first
        cache_key = f"storage_soe_sim_{soe_variation}"
        cached_storage = self._api_cache.get_cached_data(cache_key)
        if cached_storage is not None:
            return cached_storage

        # Get simulation data
        storage_data = self._simulation_engine.get_storage_soe_data(soe_variation=soe_variation)

        # Convert to model object
        battery_storage = self._convert_raw_to_battery_storage(storage_data)

        # Cache the result
        self._api_cache.set_cached_data(cache_key, battery_storage)

        return battery_storage

    async def _get_storage_soe_live(self) -> BatteryStorage:
        """Get storage SOE data from live panel."""

        async def _get_storage_soe_operation() -> BatteryStorage:
            client = self._get_client_for_endpoint(requires_auth=True)
            # Type cast needed because generated API has overly strict type hints
            result = await get_storage_soe_api_v1_storage_soe_get.asyncio(client=cast(AuthenticatedClient, client))
            # Since raise_on_unexpected_status=True, result should never be None
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
            return result

        # Check cache first
        cached_storage = self._api_cache.get_cached_data("storage_soe")
        if cached_storage is not None:
            return cached_storage

        try:
            storage = await self._retry_with_backoff(_get_storage_soe_operation)
            # Cache the successful response
            self._api_cache.set_cached_data("storage_soe", storage)
            return storage
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(e.response.status_code, e.response.content)
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
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

        async def _set_circuit_relay_operation() -> Any:
            client = self._get_client_for_endpoint(requires_auth=True)

            # Convert string to enum - explicitly handle invalid values
            try:
                relay_state = RelayState(state.upper())
            except ValueError as e:
                # Wrap ValueError in a more descriptive error
                raise SpanPanelAPIError(f"Invalid relay state '{state}'. Must be one of: OPEN, CLOSED") from e

            relay_in = RelayStateIn(relay_state=relay_state)

            # Create the body object with just the relay state
            body = BodySetCircuitStateApiV1CircuitsCircuitIdPost(relay_state_in=relay_in)

            # Type cast needed because generated API has overly strict type hints
            return await set_circuit_state_api_v_1_circuits_circuit_id_post.asyncio(
                client=cast(AuthenticatedClient, client), circuit_id=circuit_id, body=body
            )

        try:
            return await self._retry_with_backoff(_set_circuit_relay_operation)
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(e.response.status_code, e.response.content)
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
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

        async def _set_circuit_priority_operation() -> Any:
            client = self._get_client_for_endpoint(requires_auth=True)

            # Convert string to enum - explicitly handle invalid values
            try:
                priority_enum = Priority(priority.upper())
            except ValueError as e:
                # Wrap ValueError in a more descriptive error matching test expectations
                raise SpanPanelAPIError(f"API error: '{priority}' is not a valid Priority") from e

            priority_in = PriorityIn(priority=priority_enum)

            # Create the body object with just the priority
            body = BodySetCircuitStateApiV1CircuitsCircuitIdPost(priority_in=priority_in)

            # Type cast needed because generated API has overly strict type hints
            return await set_circuit_state_api_v_1_circuits_circuit_id_post.asyncio(
                client=cast(AuthenticatedClient, client), circuit_id=circuit_id, body=body
            )

        try:
            return await self._retry_with_backoff(_set_circuit_priority_operation)
        except UnexpectedStatus as e:
            self._handle_unexpected_status(e)
        except httpx.HTTPStatusError as e:
            unexpected_status = UnexpectedStatus(e.response.status_code, e.response.content)
            self._handle_unexpected_status(unexpected_status)
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
        except ValueError as e:
            # Specifically handle ValueError from enum conversion
            raise SpanPanelAPIError(f"API error: {e}") from e
        except Exception as e:
            # Catch and wrap all other exceptions
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e
