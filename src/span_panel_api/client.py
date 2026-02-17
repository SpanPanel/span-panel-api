"""SPAN Panel API Client.

This module provides a high-level async client for the SPAN Panel REST API.
It wraps the generated OpenAPI client to provide a more convenient interface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
import logging
import time
from typing import Any, NoReturn, TypeVar

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
from .models import PanelCapability, PanelGeneration, SpanCircuitSnapshot, SpanPanelSnapshot
from .simulation import DynamicSimulationEngine

T = TypeVar("T")

# Logger for this module
_LOGGER = logging.getLogger(__name__)


# Default async delay implementation
async def _default_async_delay(delay_seconds: float) -> None:
    """Default async delay implementation using asyncio.sleep."""
    await asyncio.sleep(delay_seconds)


class _DelayFunctionRegistry:
    """Registry for managing the async delay function."""

    def __init__(self) -> None:
        self._delay_func: Callable[[float], Awaitable[None]] = _default_async_delay

    def set_delay_func(self, delay_func: Callable[[float], Awaitable[None]] | None) -> None:
        """Set the delay function."""
        self._delay_func = delay_func if delay_func is not None else _default_async_delay

    async def call_delay(self, delay_seconds: float) -> None:
        """Call the current delay function."""
        await self._delay_func(delay_seconds)


# Module-level registry instance
_delay_registry = _DelayFunctionRegistry()


def set_async_delay_func(delay_func: Callable[[float], Awaitable[None]] | None) -> None:
    """Set a custom async delay function for HA compatibility.

    This allows HA integrations to provide their own delay implementation
    that works with HA's time simulation and event loop management.

    Args:
        delay_func: Custom delay function that takes delay_seconds as float,
                   or None to use the default asyncio.sleep implementation.

    Example for HA integrations:
        ```python
        import span_panel_api.client as span_client

        async def ha_compatible_delay(delay_seconds: float) -> None:
            # Use HA's event loop utilities
            await hass.helpers.event.async_call_later(delay_seconds, lambda: None)
            # Or just yield: await asyncio.sleep(0)

        # Set the custom delay function
        span_client.set_async_delay_func(ha_compatible_delay)
        ```
    """
    _delay_registry.set_delay_func(delay_func)


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
        retries: int = 0,  # Default to 0 retries for fast feedback
        retry_timeout: float = 0.5,  # How long to wait between retry attempts
        retry_backoff_multiplier: float = 2.0,
        # Cache configuration - using persistent cache (no time window)
        # Simulation configuration
        simulation_mode: bool = False,  # Enable simulation mode
        simulation_config_path: str | None = None,  # Path to YAML simulation config
        simulation_start_time: str | None = None,  # Override simulation start time (ISO format)
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
            (cache uses persistent object cache, no time window)
            simulation_mode: Enable simulation mode for testing (default: False)
            simulation_config_path: Path to YAML simulation configuration file
            simulation_start_time: Override simulation start time (ISO format, e.g., "2024-06-15T12:00:00")
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

        # Track background refresh tasks
        self._background_tasks: set[asyncio.Task[None]] = set()

        # Object pools for reuse (not caching - just object instances to avoid creation overhead)
        self._status_object: StatusOut | None = None
        self._panel_state_object: PanelState | None = None
        self._circuits_object: CircuitsOut | None = None
        self._battery_object: BatteryStorage | None = None

        # Initialize simulation engine if in simulation mode
        self._simulation_engine: DynamicSimulationEngine | None = None
        self._simulation_initialized = False
        self._simulation_start_time_override = simulation_start_time
        if simulation_mode:
            # In simulation mode, use the host as the serial number for device identification
            self._simulation_engine = DynamicSimulationEngine(serial_number=host, config_path=simulation_config_path)

        # Build base URL
        scheme = "https" if use_ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"

        # HTTP client - starts as unauthenticated, upgrades to authenticated after login
        self._client: Client | AuthenticatedClient | None = None
        self._access_token: str | None = None

        # Context tracking - critical for preventing "Cannot open a client instance more than once"
        self._in_context: bool = False
        self._httpx_client_owned: bool = False

    async def __aenter__(self) -> SpanPanelClient:
        """Enter async context manager - opens the underlying httpx client for connection pooling."""
        if self._in_context:
            raise RuntimeError("Cannot open a client instance more than once")

        # Create client if it doesn't exist
        if self._client is None:
            if self._access_token:
                self._client = self._get_authenticated_client()
            else:
                self._client = self._get_unauthenticated_client()

        # Enter the httpx client context
        # Must manually call __aenter__ - can't use async with because we need the client
        # to stay open until __aexit__ is called (split context management pattern)
        try:
            await self._client.__aenter__()  # pylint: disable=unnecessary-dunder-call
        except Exception as e:
            # Reset state on failure
            self._client = None
            raise RuntimeError(f"Failed to enter client context: {e}") from e

        self._in_context = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager - closes the underlying httpx client."""
        if not self._in_context:
            return

        try:
            if self._client is not None:
                with suppress(Exception):
                    await self._client.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            self._in_context = False
            self._client = None

    def _create_background_task(self, coro: Coroutine[Any, Any, None]) -> None:
        """Create a background task and track it for cleanup."""
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _ensure_simulation_initialized(self) -> None:
        """Ensure simulation engine is properly initialized asynchronously."""
        if not self._simulation_mode or self._simulation_initialized:
            return

        if self._simulation_engine is not None:
            await self._simulation_engine.initialize_async()

            # Override simulation start time if provided
            if self._simulation_start_time_override:
                self._simulation_engine.override_simulation_start_time(self._simulation_start_time_override)

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

    def _update_status_in_place(self, existing: StatusOut, fresh: StatusOut) -> None:
        """Update existing StatusOut object with fresh data to avoid object creation."""
        # Update software attributes individually to preserve references
        existing.software.firmware_version = fresh.software.firmware_version
        existing.software.update_status = fresh.software.update_status
        existing.software.env = fresh.software.env
        existing.software.additional_properties.clear()
        existing.software.additional_properties.update(fresh.software.additional_properties)

        # Update system attributes individually to preserve references
        existing.system.manufacturer = fresh.system.manufacturer
        existing.system.serial = fresh.system.serial
        existing.system.model = fresh.system.model
        existing.system.door_state = fresh.system.door_state
        existing.system.proximity_proven = fresh.system.proximity_proven
        existing.system.uptime = fresh.system.uptime
        existing.system.additional_properties.clear()
        existing.system.additional_properties.update(fresh.system.additional_properties)

        # Update network attributes individually to preserve references
        existing.network.eth_0_link = fresh.network.eth_0_link
        existing.network.wlan_link = fresh.network.wlan_link
        existing.network.wwan_link = fresh.network.wwan_link
        existing.network.additional_properties.clear()
        existing.network.additional_properties.update(fresh.network.additional_properties)

        # Update additional_properties
        existing.additional_properties.clear()
        existing.additional_properties.update(fresh.additional_properties)

    def _update_panel_state_in_place(self, existing: PanelState, fresh: PanelState) -> None:
        """Update existing PanelState object with fresh data to avoid object creation."""
        # Update simple attributes
        existing.main_relay_state = fresh.main_relay_state
        existing.instant_grid_power_w = fresh.instant_grid_power_w
        existing.feedthrough_power_w = fresh.feedthrough_power_w
        existing.grid_sample_start_ms = fresh.grid_sample_start_ms
        existing.grid_sample_end_ms = fresh.grid_sample_end_ms
        existing.dsm_grid_state = fresh.dsm_grid_state
        existing.dsm_state = fresh.dsm_state
        existing.current_run_config = fresh.current_run_config

        # Update main_meter_energy attributes individually to preserve references
        existing.main_meter_energy.produced_energy_wh = fresh.main_meter_energy.produced_energy_wh
        existing.main_meter_energy.consumed_energy_wh = fresh.main_meter_energy.consumed_energy_wh
        existing.main_meter_energy.additional_properties.clear()
        existing.main_meter_energy.additional_properties.update(fresh.main_meter_energy.additional_properties)

        # Update feedthrough_energy attributes individually to preserve references
        existing.feedthrough_energy.produced_energy_wh = fresh.feedthrough_energy.produced_energy_wh
        existing.feedthrough_energy.consumed_energy_wh = fresh.feedthrough_energy.consumed_energy_wh
        existing.feedthrough_energy.additional_properties.clear()
        existing.feedthrough_energy.additional_properties.update(fresh.feedthrough_energy.additional_properties)

        # Update branches - if same length, update existing branch objects; otherwise replace list
        if len(existing.branches) == len(fresh.branches):
            for i, fresh_branch in enumerate(fresh.branches):
                existing_branch = existing.branches[i]
                existing_branch.id = fresh_branch.id
                existing_branch.relay_state = fresh_branch.relay_state
                existing_branch.instant_power_w = fresh_branch.instant_power_w
                existing_branch.imported_active_energy_wh = fresh_branch.imported_active_energy_wh
                existing_branch.exported_active_energy_wh = fresh_branch.exported_active_energy_wh
                existing_branch.measure_start_ts_ms = fresh_branch.measure_start_ts_ms
                existing_branch.measure_duration_ms = fresh_branch.measure_duration_ms
                existing_branch.is_measure_valid = fresh_branch.is_measure_valid
                existing_branch.additional_properties.clear()
                existing_branch.additional_properties.update(fresh_branch.additional_properties)
        else:
            # Different number of branches - replace the entire list
            existing.branches = fresh.branches

        # Update additional_properties
        existing.additional_properties.clear()
        existing.additional_properties.update(fresh.additional_properties)

    def _update_circuits_in_place(self, existing: CircuitsOut, fresh: CircuitsOut) -> None:
        """Update existing CircuitsOut object with fresh data to avoid object creation."""
        # Update circuits.additional_properties (the circuit dictionary) to preserve references
        existing.circuits.additional_properties.clear()
        existing.circuits.additional_properties.update(fresh.circuits.additional_properties)

        # Update additional_properties
        existing.additional_properties.clear()
        existing.additional_properties.update(fresh.additional_properties)

    def _update_battery_storage_in_place(self, existing: BatteryStorage, fresh: BatteryStorage) -> None:
        """Update existing BatteryStorage object with fresh data to avoid object creation."""
        # Update soe attributes individually to preserve references
        existing.soe.percentage = fresh.soe.percentage
        existing.soe.additional_properties.clear()
        existing.soe.additional_properties.update(fresh.soe.additional_properties)

        # Update additional_properties
        existing.additional_properties.clear()
        existing.additional_properties.update(fresh.additional_properties)

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

    async def _ensure_client_opened(self, client: AuthenticatedClient | Client) -> None:
        """Ensure the httpx client is opened for connection pooling."""
        # Check if the async client is already opened by trying to access it
        with suppress(Exception):
            client.get_async_httpx_client()
            # If we can get it without error, it's already available
            # The httpx.AsyncClient will handle connection pooling automatically

    def _get_client(self) -> AuthenticatedClient | Client:
        """Get the appropriate HTTP client based on whether we have an access token."""
        if self._access_token:
            # We have a token, use authenticated client
            if self._client is None or not isinstance(self._client, AuthenticatedClient):
                # Configure httpx for better connection pooling and persistence
                httpx_args = {
                    "limits": httpx.Limits(
                        max_keepalive_connections=5,  # Keep connections alive
                        max_connections=10,  # Allow multiple connections
                        keepalive_expiry=4.0,  # Close before server's 5s keep-alive timeout
                    ),
                }

                # Create a new authenticated client
                self._client = AuthenticatedClient(
                    base_url=self._base_url,
                    token=self._access_token,
                    timeout=httpx.Timeout(
                        connect=5.0,  # Connection timeout
                        read=self._timeout,  # Read timeout
                        write=5.0,  # Write timeout
                        pool=2.0,  # Pool timeout
                    ),
                    verify_ssl=self._use_ssl,
                    raise_on_unexpected_status=True,
                    httpx_args=httpx_args,
                )
                # Only set _httpx_client_owned if we're not in a context
                # This prevents us from managing a client that's already managed by a context
                self._httpx_client_owned = not self._in_context
            return self._client
        # No token, use unauthenticated client
        return self._get_unauthenticated_client()

    def _get_unauthenticated_client(self) -> Client:
        """Get an unauthenticated client for operations that don't require auth."""
        # Configure httpx for better connection pooling and persistence
        httpx_args = {
            "limits": httpx.Limits(
                max_keepalive_connections=5,  # Keep connections alive
                max_connections=10,  # Allow multiple connections
                keepalive_expiry=4.0,  # Close before server's 5s keep-alive timeout
            ),
        }

        client = Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            verify_ssl=self._use_ssl,
            raise_on_unexpected_status=True,
            httpx_args=httpx_args,
        )
        # Only set _httpx_client_owned if we're not in a context
        if not self._in_context and self._client is None:
            self._client = client
            self._httpx_client_owned = True
        return client

    def _get_authenticated_client(self) -> AuthenticatedClient:
        """Get an authenticated client for operations that require auth."""
        if not self._access_token:
            raise SpanPanelAuthError("No access token available for authenticated operations")
        # Configure httpx for better connection pooling and persistence
        httpx_args = {
            "limits": httpx.Limits(
                max_keepalive_connections=5,  # Keep connections alive
                max_connections=10,  # Allow multiple connections
                keepalive_expiry=4.0,  # Close before server's 5s keep-alive timeout
            ),
        }

        client = AuthenticatedClient(
            base_url=self._base_url,
            token=self._access_token,
            timeout=httpx.Timeout(self._timeout),
            verify_ssl=self._use_ssl,
            raise_on_unexpected_status=True,
            httpx_args=httpx_args,
        )
        # Only set _httpx_client_owned if we're not in a context
        if not self._in_context and self._client is None:
            self._client = client
            self._httpx_client_owned = True
        return client

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
                    timeout=httpx.Timeout(
                        connect=5.0,  # Connection timeout
                        read=self._timeout,  # Read timeout
                        write=5.0,  # Write timeout
                        pool=2.0,  # Pool timeout
                    ),
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
            # If we have a token but got 401/403, authentication failed
            # If we don't have a token, authentication is required
            if self._access_token:
                raise SpanPanelAuthError(f"Authentication failed: Status {e.status_code}") from e
            raise SpanPanelAuthError("Authentication required") from e
        if e.status_code in RETRIABLE_ERROR_CODES:
            raise SpanPanelRetriableError(f"Retriable server error {e.status_code}: {e}") from e
        if e.status_code in SERVER_ERROR_CODES:
            raise SpanPanelServerError(f"Server error {e.status_code}: {e}") from e
        raise SpanPanelAPIError(f"HTTP {e.status_code}: {e}") from e

    def _get_client_for_endpoint(self, requires_auth: bool = True) -> AuthenticatedClient | Client:
        """Get the appropriate client for an endpoint with automatic connection management.

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

        # Not in context, get appropriate client type based on auth requirement
        if not requires_auth:
            # For endpoints that don't require auth, always use unauthenticated client
            # This prevents mixing client types which can cause connection issues
            return self._get_unauthenticated_client()

        # For endpoints that require auth, use the main authenticated client
        if self._client is None:
            self._client = self._get_client()

        # Ensure the underlying httpx client is accessible for connection pooling
        # This doesn't open a context, just ensures the client is ready to use
        with suppress(Exception):
            self._client.get_async_httpx_client()

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
                    await _delay_registry.call_delay(delay)
                    continue
                # Not retriable or last attempt - re-raise
                raise
            except httpx.HTTPStatusError as e:
                # Only retry specific HTTP status codes that are typically transient
                if e.response.status_code in retry_status_codes and attempt < max_attempts - 1:
                    delay = self._retry_timeout * (self._retry_backoff_multiplier**attempt)  # Exponential backoff
                    await _delay_registry.call_delay(delay)
                    continue
                # Not retriable or last attempt - re-raise
                raise
            except (httpx.ConnectError, httpx.TimeoutException):
                # Network/timeout errors are always retriable
                if attempt < max_attempts - 1:
                    delay = self._retry_timeout * (self._retry_backoff_multiplier**attempt)  # Exponential backoff
                    await _delay_registry.call_delay(delay)
                    continue
                # Last attempt - re-raise
                raise
            except httpx.RemoteProtocolError:
                # Server closed connection (stale keep-alive) - all pooled connections likely dead
                # Destroy client to force fresh connection pool on retry
                if self._client is not None:
                    with suppress(Exception):
                        await self._client.__aexit__(None, None, None)
                    self._client = None

                # If in context mode, recreate client to maintain invariant that _client is not None
                if self._in_context:
                    if self._access_token:
                        self._client = self._get_authenticated_client()
                    else:
                        self._client = self._get_unauthenticated_client()
                    # Must manually enter context - can't use async with here as we're already in a context
                    # and need to keep client alive for retry. This matches the pattern in __aenter__ (line 239).
                    await self._client.__aenter__()  # pylint: disable=unnecessary-dunder-call

                if attempt < max_attempts - 1:
                    continue  # Immediate retry - no delay needed
                raise

        # This should never be reached, but required for mypy type checking
        raise SpanPanelAPIError("Retry operation completed without success or exception")

    # Authentication Methods
    async def authenticate(
        self, name: str, description: str = "", otp: str | None = None, dashboard_password: str | None = None
    ) -> AuthOut:
        """Register and authenticate a new API client.

        Args:
            name: Client name
            description: Optional client description
            otp: Optional One-Time Password for enhanced security
            dashboard_password: Optional dashboard password for authentication

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

        # Create auth input with all provided parameters
        auth_in = AuthIn(name=name, description=description)
        if otp is not None:
            auth_in.otp = otp
        if dashboard_password is not None:
            auth_in.dashboard_password = dashboard_password

        try:
            # Use the client directly - auth registration works with unauthenticated clients
            # Cast to AuthenticatedClient to satisfy type checker (even though it's not actually authenticated)
            response = await generate_jwt_api_v1_auth_register_post.asyncio(client=client, body=auth_in)
            # Handle response - could be AuthOut, HTTPValidationError, or None
            if response is None:
                raise SpanPanelAPIError("Authentication failed - no response from server")
            if isinstance(response, HTTPValidationError):
                error_details = getattr(response, "detail", "Unknown validation error")
                raise SpanPanelAPIError(f"Validation error during authentication: {error_details}")
            if hasattr(response, "access_token"):
                # Store the token for future requests (works for both AuthOut and mocks)
                self.set_access_token(response.access_token)
                return response
            raise SpanPanelAPIError(f"Unexpected response type: {type(response)}, response: {response}")
        except UnexpectedStatus as e:
            # Convert UnexpectedStatus to appropriate SpanPanel exception
            # Special case for auth endpoint - 401/403 here means auth failed
            error_text = f"Status {e.status_code}"

            if e.status_code in AUTH_ERROR_CODES:
                raise SpanPanelAuthError(f"Authentication failed: {error_text}") from e
            if e.status_code in RETRIABLE_ERROR_CODES:
                raise SpanPanelRetriableError(f"Retriable server error {e.status_code}: {error_text}", e.status_code) from e
            if e.status_code in SERVER_ERROR_CODES:
                raise SpanPanelServerError(f"Server error {e.status_code}: {error_text}", e.status_code) from e
            raise SpanPanelAPIError(f"HTTP {e.status_code}: {error_text}", e.status_code) from e
        except httpx.HTTPStatusError as e:
            # Convert HTTPStatusError to UnexpectedStatus and handle appropriately
            # Special case for auth endpoint - 401/403 here means auth failed
            error_text = e.response.text if hasattr(e.response, "text") else str(e)

            if e.response.status_code in AUTH_ERROR_CODES:
                raise SpanPanelAuthError(f"Authentication failed: {error_text}") from e
            if e.response.status_code in RETRIABLE_ERROR_CODES:
                raise SpanPanelRetriableError(
                    f"Retriable server error {e.response.status_code}: {error_text}", e.response.status_code
                ) from e
            if e.response.status_code in SERVER_ERROR_CODES:
                raise SpanPanelServerError(
                    f"Server error {e.response.status_code}: {error_text}", e.response.status_code
                ) from e
            raise SpanPanelAPIError(f"HTTP {e.response.status_code}: {error_text}", e.response.status_code) from e
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(f"Request timed out after {self._timeout}s") from e
        except ValueError as e:
            # Handle specific dictionary parsing errors from malformed server responses
            if "dictionary update sequence element" in str(e) and "length" in str(e) and "required" in str(e):
                raise SpanPanelAPIError(
                    f"Server returned malformed authentication response. "
                    f"This may indicate a panel firmware issue or network problem. "
                    f"Original error: {e}"
                ) from e
            # Handle other ValueError instances (like Pydantic validation errors)
            raise SpanPanelAPIError(f"Invalid data during authentication: {e}") from e
        except Exception as e:
            # Catch any other unexpected errors
            raise SpanPanelAPIError(f"Unexpected error during authentication: {e}") from e

    # Panel Status and Info
    async def get_status(self) -> StatusOut:
        """Get complete panel system status (does not require authentication)."""
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_status_simulation()

        # In live mode, use standard endpoint
        return await self._get_status_live()

    async def _get_status_simulation(self) -> StatusOut:
        """Get status data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Get simulation data
        status_data = await self._simulation_engine.get_status()

        # Convert to model object
        fresh_status = self._convert_raw_to_status_out(status_data)

        # Reuse existing object to avoid creation overhead
        if self._status_object is None:
            self._status_object = fresh_status
        else:
            self._update_status_in_place(self._status_object, fresh_status)

        return self._status_object

    async def _get_status_live(self) -> StatusOut:
        """Get status data from live panel."""

        async def _get_status_operation() -> StatusOut:
            client = self._get_client_for_endpoint(requires_auth=False)
            # Status endpoint works with both authenticated and unauthenticated clients
            result = await system_status_api_v1_status_get.asyncio(client=client)
            # Since raise_on_unexpected_status=True, result should never be None
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
            return result

        try:
            # Fetch fresh data from API
            start_time = time.time()
            fresh_status = await self._retry_with_backoff(_get_status_operation)
            api_duration = time.time() - start_time
            _LOGGER.debug("Status API call took %.3fs", api_duration)

            # Reuse existing object to avoid creation overhead
            if self._status_object is None:
                self._status_object = fresh_status
            else:
                self._update_status_in_place(self._status_object, fresh_status)

            return self._status_object
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

    async def get_panel_state(self) -> PanelState:
        """Get panel state information.

        In simulation mode, panel behavior is defined by the YAML configuration file.
        Use set_panel_overrides() for temporary variations outside normal ranges.
        """
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_panel_state_simulation()

        # In live mode, use live implementation
        return await self._get_panel_state_live()

    async def _get_panel_state_simulation(self) -> PanelState:
        """Get panel state data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Get simulation data
        full_data = await self._simulation_engine.get_panel_data()
        panel_data = full_data.get("panel", {})

        # Convert to model object
        fresh_panel_state = self._convert_raw_to_panel_state(panel_data)

        # Synchronize branch power with circuit power for consistency
        await self._synchronize_branch_power_with_circuits(fresh_panel_state, full_data)

        # Note: Panel grid power will be recalculated after circuits are processed
        # to ensure consistency with the actual circuit power values

        # Reuse existing object to avoid creation overhead
        if self._panel_state_object is None:
            self._panel_state_object = fresh_panel_state
        else:
            self._update_panel_state_in_place(self._panel_state_object, fresh_panel_state)

        return self._panel_state_object

    async def _adjust_panel_power_for_virtual_circuits(self, panel_state: PanelState) -> None:
        """Adjust panel power to include unmapped tab power for consistency with circuit totals."""
        if not hasattr(panel_state, "branches") or not panel_state.branches:
            return

        # This method is no longer needed without caching
        return

    def _validate_synchronization_data(self, panel_state: PanelState, full_data: dict[str, Any]) -> dict[str, Any] | None:
        """Validate data required for branch power synchronization."""
        if not hasattr(panel_state, "branches") or not panel_state.branches:
            _LOGGER.debug("No branches to synchronize")
            return None

        circuits_data = full_data.get("circuits", {})
        if not circuits_data:
            _LOGGER.debug("No circuits data to synchronize")
            return None

        # The circuits data has a nested structure: circuits -> {circuit_id: circuit_data}
        actual_circuits = circuits_data.get("circuits", circuits_data)
        if not actual_circuits:
            _LOGGER.debug("No actual circuits data to synchronize")
            return None

        if isinstance(actual_circuits, dict):
            return actual_circuits
        return None

    def _build_tab_power_mapping(self, actual_circuits: dict[str, Any], panel_state: PanelState) -> dict[int, float]:
        """Build mapping of tab numbers to total circuit power for that tab."""
        tab_power_map: dict[int, float] = {}

        # Process each circuit and distribute its power across its tabs
        for _circuit_id, circuit_data in actual_circuits.items():
            if not isinstance(circuit_data, dict):
                continue

            circuit_power = circuit_data.get("instantPowerW", 0.0)
            circuit_tabs = circuit_data.get("tabs", [])

            if not circuit_tabs:
                continue

            # Handle both single tab and multi-tab circuits
            if isinstance(circuit_tabs, int):
                circuit_tabs = [circuit_tabs]
            elif not isinstance(circuit_tabs, list):
                continue

            # Distribute circuit power equally across its tabs
            power_per_tab = circuit_power / len(circuit_tabs) if circuit_tabs else 0.0

            for tab_num in circuit_tabs:
                if isinstance(tab_num, int) and 1 <= tab_num <= len(panel_state.branches):
                    tab_power_map[tab_num] = tab_power_map.get(tab_num, 0.0) + power_per_tab

        return tab_power_map

    def _update_branch_power(self, panel_state: PanelState, tab_power_map: dict[int, float]) -> None:
        """Update branch power to match circuit power."""
        for tab_num, power in tab_power_map.items():
            branch_idx = tab_num - 1
            if 0 <= branch_idx < len(panel_state.branches):
                panel_state.branches[branch_idx].instant_power_w = power

    def _calculate_grid_power(self, actual_circuits: dict[str, Any]) -> tuple[float, float, float]:
        """Calculate grid power from circuit consumption and production."""
        total_consumption = 0.0
        total_production = 0.0

        for circuit_id, circuit_data in actual_circuits.items():
            if not isinstance(circuit_data, dict):
                continue

            circuit_power = circuit_data.get("instantPowerW", 0.0)
            circuit_name = circuit_data.get("name", circuit_id).lower()

            # Identify producer circuits by name or configuration
            if any(keyword in circuit_name for keyword in ["solar", "inverter", "generator", "battery"]):
                total_production += circuit_power
            else:
                total_consumption += circuit_power

        # Panel grid power = consumption - production
        # Positive = importing from grid, Negative = exporting to grid
        grid_power = total_consumption - total_production
        return total_consumption, total_production, grid_power

    async def _synchronize_branch_power_with_circuits(self, panel_state: PanelState, full_data: dict[str, Any]) -> None:
        """Synchronize branch power with circuit power for consistency in simulation mode."""
        actual_circuits = self._validate_synchronization_data(panel_state, full_data)
        if actual_circuits is None:
            return

        _LOGGER.debug("Synchronizing branch power with %d circuits", len(actual_circuits))

        # Build tab power mapping and update branches
        tab_power_map = self._build_tab_power_mapping(actual_circuits, panel_state)
        self._update_branch_power(panel_state, tab_power_map)

        # Calculate and update grid power
        total_consumption, total_production, grid_power = self._calculate_grid_power(actual_circuits)
        panel_state.instant_grid_power_w = grid_power

        _LOGGER.debug(
            "Branch power synchronization complete: %d tabs updated, consumption: %.1fW, production: %.1fW, grid: %.1fW",
            len(tab_power_map),
            total_consumption,
            total_production,
            panel_state.instant_grid_power_w,
        )

    async def _recalculate_panel_grid_power_from_circuits(self, circuits_out: CircuitsOut) -> None:
        """Recalculate panel grid power to match the actual circuit power values.

        This method ensures panel state consistency regardless of initialization order
        and handles energy aggregation more robustly.
        """
        # Calculate total circuit power and energy using the same logic as the test
        total_consumption = 0.0
        total_production = 0.0
        total_produced_energy = 0.0
        total_consumed_energy = 0.0
        circuits_with_unavailable_energy = 0
        total_circuits_processed = 0

        # Iterate through circuits stored in additional_properties
        for circuit_id, circuit in circuits_out.circuits.additional_properties.items():
            # Process all circuits with valid IDs (including empty string, but not None)
            # Only skip None circuit_id to avoid masking data problems
            if circuit_id is not None:
                # Skip virtual circuits for unmapped tabs from power calculations
                # because we already count the power for circuits that are mapped to tabs
                # but still count them for energy aggregation consistency
                if not circuit_id.startswith("unmapped_tab_"):
                    # Use the same logic as the test: determine if consuming or producing based on circuit type
                    circuit_name = circuit.name.lower() if circuit.name else ""
                    if any(keyword in circuit_name for keyword in ["solar", "inverter", "generator", "battery"]):
                        # Producer circuits (solar, battery when discharging)
                        total_production += circuit.instant_power_w
                    else:
                        # Consumer circuits
                        total_consumption += circuit.instant_power_w

                # Count circuits with unavailable energy data for better aggregation logic
                total_circuits_processed += 1
                if circuit.produced_energy_wh is None or circuit.consumed_energy_wh is None:
                    circuits_with_unavailable_energy += 1

                # Sum up energy values (skip None values which indicate unavailable data)
                if circuit.produced_energy_wh is not None:
                    total_produced_energy += circuit.produced_energy_wh
                if circuit.consumed_energy_wh is not None:
                    total_consumed_energy += circuit.consumed_energy_wh

        # Update panel grid power to match circuit totals
        expected_grid_power = total_consumption - total_production

        # Always ensure panel state object exists to avoid order dependency
        if self._panel_state_object is None:
            # If panel state doesn't exist yet, we can't update it
            # This is expected in some call orders and should not cause errors
            _LOGGER.debug("Panel state object not initialized, skipping grid power update")
            return

        # Update panel state with calculated values
        self._panel_state_object.instant_grid_power_w = expected_grid_power

        # More nuanced energy aggregation: only set to None if significant portion is unavailable
        # This prevents a single circuit with unavailable data from invalidating all energy data
        if total_circuits_processed == 0:
            # No circuits processed, keep existing energy values
            return

        unavailable_ratio = circuits_with_unavailable_energy / total_circuits_processed

        # Only set energy to None if more than 50% of circuits have unavailable energy
        # This provides better resilience while still indicating when data is significantly incomplete
        if unavailable_ratio > 0.5:
            self._panel_state_object.main_meter_energy.produced_energy_wh = None
            self._panel_state_object.main_meter_energy.consumed_energy_wh = None
            _LOGGER.debug(
                "Setting panel energy to None: %d/%d circuits have unavailable energy (%.1f%%)",
                circuits_with_unavailable_energy,
                total_circuits_processed,
                unavailable_ratio * 100,
            )
        else:
            self._panel_state_object.main_meter_energy.produced_energy_wh = total_produced_energy
            self._panel_state_object.main_meter_energy.consumed_energy_wh = total_consumed_energy
            if circuits_with_unavailable_energy > 0:
                _LOGGER.debug(
                    "Panel energy aggregated despite %d/%d circuits with unavailable energy (%.1f%%)",
                    circuits_with_unavailable_energy,
                    total_circuits_processed,
                    unavailable_ratio * 100,
                )

    async def _get_panel_state_live(self) -> PanelState:
        """Get panel state data from live panel."""

        async def _get_panel_state_operation() -> PanelState:
            client = self._get_client_for_endpoint(requires_auth=True)
            # Panel state requires authentication
            result = await get_panel_state_api_v1_panel_get.asyncio(client=client)
            # Since raise_on_unexpected_status=True, result should never be None
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
            return result

        try:
            # Fetch fresh data from API
            start_time = time.time()
            fresh_state = await self._retry_with_backoff(_get_panel_state_operation)
            api_duration = time.time() - start_time
            _LOGGER.debug("Panel state API call took %.3fs", api_duration)

            # Reuse existing object to avoid creation overhead
            if self._panel_state_object is None:
                self._panel_state_object = fresh_state
            else:
                self._update_panel_state_in_place(self._panel_state_object, fresh_state)

            return self._panel_state_object
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
            # Only convert to auth error if it's specifically an HTTP 401 error, not just any error mentioning "401"
            if isinstance(e, (httpx.HTTPStatusError | UnexpectedStatus | RuntimeError)) and "401" in str(e):
                # If we have a token but got 401, authentication failed
                # If we don't have a token, authentication is required
                if self._access_token:
                    raise SpanPanelAuthError("Authentication failed") from e
                raise SpanPanelAuthError("Authentication required") from e
            # All other exceptions are internal errors, not auth problems
            raise SpanPanelAPIError(f"Unexpected error: {e}") from e

    async def get_circuits(self) -> CircuitsOut:
        """Get all circuits and their current state, including virtual circuits for unmapped tabs.

        In simulation mode, circuit behavior is defined by the YAML configuration file.
        Use set_circuit_overrides() for temporary variations outside normal ranges.
        """
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_circuits_simulation()

        # In live mode, use live implementation
        return await self._get_circuits_live()

    async def _get_circuits_simulation(self) -> CircuitsOut:
        """Get circuits data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Get simulation data (contains both circuits and panel data)
        full_data = await self._simulation_engine.get_panel_data()
        circuits_data = full_data.get("circuits", {})

        # Convert to model object
        fresh_circuits = self._convert_raw_to_circuits_out(circuits_data)

        # Extract branches directly from full_data to avoid redundant API call
        panel_data = full_data.get("panel", {})
        if panel_data and "branches" in panel_data:
            # Convert branches from raw data
            branches = []
            for branch_data in panel_data.get("branches", []):
                branch = Branch(
                    id=branch_data.get("id", ""),
                    relay_state=RelayState(branch_data.get("relayState", "CLOSED")),
                    instant_power_w=branch_data.get("instantPowerW", 0.0),
                    imported_active_energy_wh=branch_data.get("importedActiveEnergyWh", 0.0),
                    exported_active_energy_wh=branch_data.get("exportedActiveEnergyWh", 0.0),
                    measure_start_ts_ms=branch_data.get("measureStartTsMs", 0),
                    measure_duration_ms=branch_data.get("measureDurationMs", 0),
                    is_measure_valid=branch_data.get("isMeasureValid", True),
                )
                branches.append(branch)

            # Add virtual circuits for unmapped tabs using branches from same data
            self._add_unmapped_virtuals(fresh_circuits, branches)
        else:
            _LOGGER.debug("No branches in panel data (simulation), skipping unmapped circuit creation")

        # Reuse existing object to avoid creation overhead
        if self._circuits_object is None:
            self._circuits_object = fresh_circuits
        else:
            self._update_circuits_in_place(self._circuits_object, fresh_circuits)

        # Recalculate panel grid power to match circuit totals (after object reuse)
        await self._recalculate_panel_grid_power_from_circuits(self._circuits_object)

        return self._circuits_object

    async def _get_circuits_live(self) -> CircuitsOut:
        """Get circuits data from live panel."""

        async def _get_circuits_operation() -> CircuitsOut:
            # Get circuits first (needed to determine mapped tabs)
            client = self._get_client_for_endpoint(requires_auth=True)
            result = await get_circuits_api_v1_circuits_get.asyncio(client=client)
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")

            # Get panel state for branches data (depends on circuits to determine unmapped tabs)
            panel_state = await self.get_panel_state()
            _LOGGER.debug(
                "Panel state branches: %s",
                len(panel_state.branches) if hasattr(panel_state, "branches") else "No branches",
            )

            # Create virtual circuits for unmapped tabs
            if hasattr(panel_state, "branches") and panel_state.branches:
                self._add_unmapped_virtuals(result, panel_state.branches)
            else:
                _LOGGER.debug("No branches in panel state (live mode), skipping unmapped circuit creation")

            return result

        try:
            # Fetch fresh data from API
            fresh_circuits = await self._retry_with_backoff(_get_circuits_operation)

            # Reuse existing object to avoid creation overhead
            if self._circuits_object is None:
                self._circuits_object = fresh_circuits
            else:
                self._update_circuits_in_place(self._circuits_object, fresh_circuits)

            return self._circuits_object
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

    def _get_mapped_tabs_from_circuits(self, circuits: CircuitsOut) -> set[int]:
        """Collect tab numbers that are already mapped to circuits.

        Args:
            circuits: CircuitsOut container to inspect

        Returns:
            Set of mapped tab numbers
        """
        mapped_tabs: set[int] = set()
        if hasattr(circuits, "circuits") and hasattr(circuits.circuits, "additional_properties"):
            for circuit in circuits.circuits.additional_properties.values():
                if hasattr(circuit, "tabs") and circuit.tabs is not None and str(circuit.tabs) != "UNSET":
                    if isinstance(circuit.tabs, list | tuple):
                        mapped_tabs.update(circuit.tabs)
                    elif isinstance(circuit.tabs, int):
                        mapped_tabs.add(circuit.tabs)
        return mapped_tabs

    def _add_unmapped_virtuals(self, circuits: CircuitsOut, branches: list[Branch]) -> None:
        """Add virtual circuits for any tabs not present in the mapped set.

        Args:
            circuits: CircuitsOut to mutate with virtual entries
            branches: Panel branches used to synthesize metrics
        """
        mapped_tabs = self._get_mapped_tabs_from_circuits(circuits)
        total_tabs = len(branches)
        all_tabs = set(range(1, total_tabs + 1))
        unmapped_tabs = all_tabs - mapped_tabs

        _LOGGER.debug(
            "Creating unmapped circuits. Total tabs: %s, Mapped tabs: %s, Unmapped tabs: %s",
            total_tabs,
            mapped_tabs,
            unmapped_tabs,
        )

        for tab_num in unmapped_tabs:
            branch_idx = tab_num - 1
            if branch_idx < len(branches):
                branch = branches[branch_idx]
                virtual_circuit = self._create_unmapped_tab_circuit(branch, tab_num)
                circuit_id = f"unmapped_tab_{tab_num}"
                circuits.circuits.additional_properties[circuit_id] = virtual_circuit
                _LOGGER.debug("Created unmapped circuit: %s", circuit_id)

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
        imported_energy = getattr(branch, "imported_active_energy_wh", 0.0)
        exported_energy = getattr(branch, "exported_active_energy_wh", 0.0)

        # Convert values safely, handling 'unknown' strings when panel is offline
        def _safe_power_conversion(value: Any) -> float:
            """Safely convert power value to float, returning 0.0 for invalid values."""
            if value is None:
                return 0.0
            if isinstance(value, int | float):
                return float(value)
            if isinstance(value, str):
                if value.lower() in ("unknown", "unavailable", "offline"):
                    return 0.0
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return 0.0
            return 0.0

        def _safe_energy_conversion(value: Any) -> float | None:
            """Safely convert energy value, returning None for unavailable values."""
            if value is None:
                return None
            if isinstance(value, int | float):
                return float(value)
            if isinstance(value, str):
                if value.lower() in ("unknown", "unavailable", "offline"):
                    return None  # Energy should be None when unavailable
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return None
            return None

        # Safely convert all values
        instant_power_w = _safe_power_conversion(getattr(branch, "instant_power_w", 0.0))
        # For solar tabs, imported energy represents production
        # Preserve None values for unavailable energy data
        produced_energy_wh = _safe_energy_conversion(imported_energy)
        consumed_energy_wh = _safe_energy_conversion(exported_energy)

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

    async def get_storage_soe(self) -> BatteryStorage:
        """Get storage state of energy (SOE) data.

        In simulation mode, storage behavior is defined by the YAML configuration file.
        """
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._get_storage_soe_simulation()

        # In live mode, ignore variation parameters
        return await self._get_storage_soe_live()

    async def _get_storage_soe_simulation(self) -> BatteryStorage:
        """Get storage SOE data in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        # Ensure simulation is properly initialized asynchronously
        await self._ensure_simulation_initialized()

        # Get simulation data
        storage_data = await self._simulation_engine.get_soe()

        # Convert to model object
        fresh_battery = self._convert_raw_to_battery_storage(storage_data)

        # Reuse existing object to avoid creation overhead
        if self._battery_object is None:
            self._battery_object = fresh_battery
        else:
            self._update_battery_storage_in_place(self._battery_object, fresh_battery)

        return self._battery_object

    async def _get_storage_soe_live(self) -> BatteryStorage:
        """Get storage SOE data from live panel."""

        async def _get_storage_soe_operation() -> BatteryStorage:
            client = self._get_client_for_endpoint(requires_auth=True)
            # Storage SOE requires authentication
            result = await get_storage_soe_api_v1_storage_soe_get.asyncio(client=client)
            # Since raise_on_unexpected_status=True, result should never be None
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
            return result

        try:
            # Fetch fresh data from API
            fresh_storage = await self._retry_with_backoff(_get_storage_soe_operation)

            # Reuse existing object to avoid creation overhead
            if self._battery_object is None:
                self._battery_object = fresh_storage
            else:
                self._update_battery_storage_in_place(self._battery_object, fresh_storage)

            return self._battery_object
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
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._set_circuit_relay_simulation(circuit_id, state)

        # In live mode, use live implementation
        return await self._set_circuit_relay_live(circuit_id, state)

    async def _set_circuit_relay_simulation(self, circuit_id: str, state: str) -> Any:
        """Set circuit relay state in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        await self._ensure_simulation_initialized()

        # Validate state
        if state.upper() not in ["OPEN", "CLOSED"]:
            raise SpanPanelAPIError(f"Invalid relay state '{state}'. Must be one of: OPEN, CLOSED")

        # Apply the relay state override to the simulation engine
        circuit_overrides = {circuit_id: {"relay_state": state.upper()}}
        self._simulation_engine.set_dynamic_overrides(circuit_overrides=circuit_overrides)

        # Return a mock success response
        return {"status": "success", "circuit_id": circuit_id, "relay_state": state.upper()}

    async def _set_circuit_relay_live(self, circuit_id: str, state: str) -> Any:
        """Set circuit relay state in live mode."""

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

            # Circuit state modification requires authentication
            return await set_circuit_state_api_v_1_circuits_circuit_id_post.asyncio(
                client=client, circuit_id=circuit_id, body=body
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
        # In simulation mode, use simulation engine
        if self._simulation_mode:
            return await self._set_circuit_priority_simulation(circuit_id, priority)

        # In live mode, use live implementation
        return await self._set_circuit_priority_live(circuit_id, priority)

    async def _set_circuit_priority_simulation(self, circuit_id: str, priority: str) -> Any:
        """Set circuit priority in simulation mode."""
        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        await self._ensure_simulation_initialized()

        # Validate priority
        if priority.upper() not in ["MUST_HAVE", "NICE_TO_HAVE"]:
            raise SpanPanelAPIError(f"Invalid priority '{priority}'. Must be one of: MUST_HAVE, NICE_TO_HAVE")

        # Apply the priority override to the simulation engine
        circuit_overrides = {circuit_id: {"priority": priority.upper()}}
        self._simulation_engine.set_dynamic_overrides(circuit_overrides=circuit_overrides)

        # Return a mock success response
        return {"status": "success", "circuit_id": circuit_id, "priority": priority.upper()}

    async def _set_circuit_priority_live(self, circuit_id: str, priority: str) -> Any:
        """Set circuit priority in live mode."""

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

            # Circuit state modification requires authentication
            return await set_circuit_state_api_v_1_circuits_circuit_id_post.asyncio(
                client=client, circuit_id=circuit_id, body=body
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

    async def set_circuit_overrides(
        self, circuit_overrides: dict[str, dict[str, Any]] | None = None, global_overrides: dict[str, Any] | None = None
    ) -> None:
        """Set temporary circuit overrides in simulation mode.

        This allows temporary variations outside the normal ranges defined in the YAML configuration.
        Only works in simulation mode.

        Args:
            circuit_overrides: Dict mapping circuit_id to override parameters:
                - power_override: Set specific power value (Watts)
                - relay_state: Force relay state ("OPEN" or "CLOSED")
                - priority: Override priority ("MUST_HAVE" or "NON_ESSENTIAL")
                - power_multiplier: Multiply normal power by this factor
            global_overrides: Apply to all circuits:
                - power_multiplier: Global power multiplier
                - noise_factor: Override noise factor
                - time_acceleration: Override time acceleration

        Example:
            # Force specific circuit to high power
            await client.set_circuit_overrides({
                "circuit_001": {
                    "power_override": 2000.0,
                    "relay_state": "CLOSED"
                }
            })

            # Apply global 2x power multiplier
            await client.set_circuit_overrides(
                global_overrides={"power_multiplier": 2.0}
            )
        """
        if not self._simulation_mode:
            raise SpanPanelAPIError("Circuit overrides only available in simulation mode")

        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        await self._ensure_simulation_initialized()

        # Apply overrides to simulation engine
        self._simulation_engine.set_dynamic_overrides(circuit_overrides=circuit_overrides, global_overrides=global_overrides)

    async def clear_circuit_overrides(self) -> None:
        """Clear all temporary circuit overrides in simulation mode.

        Returns circuit behavior to the YAML configuration defaults.
        Only works in simulation mode.
        """
        if not self._simulation_mode:
            raise SpanPanelAPIError("Circuit overrides only available in simulation mode")

        if self._simulation_engine is None:
            raise SpanPanelAPIError("Simulation engine not initialized")

        await self._ensure_simulation_initialized()

        # Clear overrides from simulation engine
        self._simulation_engine.clear_dynamic_overrides()

    async def get_all_data(self, include_battery: bool = False) -> dict[str, Any]:
        """Get all panel data in parallel for maximum performance.

        This method makes concurrent API calls to fetch all data at once,
        reducing total time from ~1.5s (sequential) to ~1.0s (parallel).

        Args:
            include_battery: Whether to include battery/storage data

        Returns:
            Dictionary containing all panel data:
            {
                'status': StatusOut,
                'panel_state': PanelState,
                'circuits': CircuitsOut,
                'storage': BatteryStorage (if include_battery=True)
            }
        """
        # Create tasks for all data types
        tasks = [
            self.get_status(),
            self.get_panel_state(),
            self.get_circuits(),
        ]

        if include_battery:
            tasks.append(self.get_storage_soe())

        # Execute all calls in parallel
        results = await asyncio.gather(*tasks)

        # Return all data
        result = {
            "status": results[0],
            "panel_state": results[1],
            "circuits": results[2],
        }

        if include_battery:
            result["storage"] = results[3]

        return result

    # ---------------------------------------------------------------------------
    # SpanPanelClientProtocol conformance
    # ---------------------------------------------------------------------------

    @property
    def capabilities(self) -> PanelCapability:
        """Return the full Gen2 capability set."""
        return PanelCapability.GEN2_FULL

    async def connect(self) -> bool:
        """Probe the panel to verify connectivity.

        Unlike the async context manager (``async with client``), this method
        does not open a persistent httpx connection pool.  It is intended for
        one-shot reachability checks and auto-detection.

        Returns:
            ``True`` if the panel responded, ``False`` otherwise.
        """
        return await self.ping()

    async def ping(self) -> bool:
        """Return ``True`` if the panel is reachable and responds to status requests."""
        try:
            await self.get_status()
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def get_snapshot(self) -> SpanPanelSnapshot:
        """Return a unified, transport-agnostic snapshot of the current panel state.

        Fetches status, panel state, and circuits concurrently.  Battery SOE
        is attempted separately and silently omitted if the panel has no
        storage hardware.
        """
        from .generated_client.types import UNSET  # pylint: disable=import-outside-toplevel

        status, panel_state, circuits_out = await asyncio.gather(
            self.get_status(),
            self.get_panel_state(),
            self.get_circuits(),
        )

        battery_soe: float | None = None
        with suppress(Exception):  # pylint: disable=broad-exception-caught
            storage = await self.get_storage_soe()
            pct = storage.soe.percentage
            if isinstance(pct, int):
                battery_soe = float(pct)

        circuit_snapshots: dict[str, SpanCircuitSnapshot] = {}
        for circuit_id, circuit in circuits_out.circuits.additional_properties.items():
            name: str = str(circuit.name) if circuit.name is not UNSET else f"Circuit {circuit_id}"
            tabs_raw = circuit.tabs
            tabs: list[int] | None = list(tabs_raw) if isinstance(tabs_raw, list) else None
            relay_str: str | None = str(circuit.relay_state) if circuit.relay_state else None
            priority_str: str | None = str(circuit.priority) if circuit.priority else None
            is_on = str(circuit.relay_state) == "CLOSED"

            circuit_snapshots[circuit_id] = SpanCircuitSnapshot(
                circuit_id=circuit_id,
                name=name,
                power_w=circuit.instant_power_w,
                voltage_v=0.0,
                current_a=0.0,
                is_on=is_on,
                relay_state=relay_str,
                priority=priority_str,
                tabs=tabs,
                energy_produced_wh=circuit.produced_energy_wh,
                energy_consumed_wh=circuit.consumed_energy_wh,
            )

        main_meter_energy = panel_state.main_meter_energy
        feedthrough_energy = panel_state.feedthrough_energy

        return SpanPanelSnapshot(
            panel_generation=PanelGeneration.GEN2,
            serial_number=status.system.serial,
            firmware_version=status.software.firmware_version,
            circuits=circuit_snapshots,
            main_power_w=panel_state.instant_grid_power_w,
            main_relay_state=str(panel_state.main_relay_state),
            grid_power_w=panel_state.instant_grid_power_w,
            battery_soe=battery_soe,
            dsm_state=panel_state.dsm_state,
            dsm_grid_state=panel_state.dsm_grid_state,
            # Panel data
            feedthrough_power_w=panel_state.feedthrough_power_w,
            main_meter_energy_produced_wh=main_meter_energy.produced_energy_wh,
            main_meter_energy_consumed_wh=main_meter_energy.consumed_energy_wh,
            feedthrough_energy_produced_wh=feedthrough_energy.produced_energy_wh,
            feedthrough_energy_consumed_wh=feedthrough_energy.consumed_energy_wh,
            current_run_config=panel_state.current_run_config,
            # Hardware status
            hardware_door_state=str(status.system.door_state),
            hardware_uptime=status.system.uptime,
            hardware_is_ethernet_connected=status.network.eth_0_link,
            hardware_is_wifi_connected=status.network.wlan_link,
            hardware_is_cellular_connected=status.network.wwan_link,
            hardware_update_status=status.software.update_status,
            hardware_env=status.software.env,
            hardware_manufacturer=status.system.manufacturer,
            hardware_model=status.system.model,
            hardware_proximity_proven=getattr(status.system, "proximity_proven", None),
        )

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        if self._client:
            # The generated client has async context manager support
            with suppress(Exception):
                await self._client.__aexit__(None, None, None)
            self._client = None
        self._in_context = False
