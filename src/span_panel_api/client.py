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
            _LOGGER.debug("Cache MISS for %s: not in cache", cache_key)
            return None

        cached_data, cache_timestamp = self._cache_entries[cache_key]

        # Check if cache window has expired
        elapsed = time.time() - cache_timestamp
        if elapsed > self._window_duration:
            # Cache expired - remove it and return None
            del self._cache_entries[cache_key]
            _LOGGER.debug("Cache EXPIRED for %s: elapsed=%.1fs > window=%.1fs", cache_key, elapsed, self._window_duration)
            return None

        _LOGGER.debug("Cache HIT for %s: elapsed=%.1fs < window=%.1fs", cache_key, elapsed, self._window_duration)
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
        _LOGGER.debug("Cache SET for %s: window=%.1fs", cache_key, self._window_duration)

    def clear(self) -> None:
        """Clear all cached data."""
        self._cache_entries.clear()


class PersistentObjectCache:
    """Persistent object cache that reuses objects and only clears on API failures.

    This cache maintains live objects and updates them in place rather than recreating.
    Cache only clears when API calls fail, not based on time.

    Cache Behavior:
    - Objects created once and reused
    - Data updated in place on successful API calls
    - Cache only cleared on API failures
    - Reads always return immediately from cache

    Usage:
        cache = PersistentObjectCache()
        circuits = cache.get_circuits()  # Always returns immediately
        cache.update_circuits_from_api(new_data)  # Updates objects in place
        cache.clear_on_failure()  # Only called on API errors
    """

    def __init__(self) -> None:
        """Initialize the persistent cache."""
        self._circuits_cache: CircuitsOut | None = None
        self._panel_state_cache: PanelState | None = None
        self._status_cache: StatusOut | None = None
        self._battery_cache: BatteryStorage | None = None
        self._circuit_objects: dict[str, Circuit] = {}  # circuit_id -> Circuit object

    def get_circuits(self) -> CircuitsOut | None:
        """Get cached circuits data immediately."""
        return self._circuits_cache

    def get_panel_state(self) -> PanelState | None:
        """Get cached panel state data immediately."""
        return self._panel_state_cache

    def get_status(self) -> StatusOut | None:
        """Get cached status data immediately."""
        return self._status_cache

    def get_battery_storage(self) -> BatteryStorage | None:
        """Get cached battery storage data immediately."""
        return self._battery_cache

    def _initialize_circuits_cache(self, raw_data: dict[str, Any]) -> None:
        """Initialize circuits cache for first time."""
        self._circuits_cache = CircuitsOut.from_dict(raw_data)
        # Cache all circuit object references
        for circuit_id, circuit in self._circuits_cache.circuits.additional_properties.items():
            self._circuit_objects[circuit_id] = circuit
        _LOGGER.debug("Cache INIT for circuits: created %d objects", len(self._circuit_objects))

    def _log_circuits_debug_info(self, raw_data: dict[str, Any]) -> None:
        """Log debug information about circuits data structure."""
        _LOGGER.debug("Cache UPDATE circuits: raw_data keys = %s", list(raw_data.keys()))

        # Debug: Check what's inside the circuits key
        if "circuits" in raw_data:
            circuits_data = raw_data["circuits"]
            _LOGGER.debug(
                "Cache UPDATE circuits: circuits keys = %s",
                (
                    list(circuits_data.keys())
                    if isinstance(circuits_data, dict)
                    else f"circuits type = {type(circuits_data)}"
                ),
            )

            # If circuits_data is a dict, show a sample of its contents
            if isinstance(circuits_data, dict) and circuits_data:
                sample_key = next(iter(circuits_data.keys()))
                _LOGGER.debug("Cache UPDATE circuits: sample circuits[%s] = %s", sample_key, type(circuits_data[sample_key]))

    def _extract_circuit_data(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Extract circuit data from raw API response."""
        # Try different possible paths for circuit data
        if "circuits" in raw_data and isinstance(raw_data["circuits"], dict):
            circuits_obj = raw_data["circuits"]
            if "additionalProperties" in circuits_obj:
                additional_props = circuits_obj["additionalProperties"]
                if isinstance(additional_props, dict):
                    _LOGGER.debug(
                        "Cache UPDATE circuits: found circuits.additionalProperties with %d items",
                        len(additional_props),
                    )
                    return additional_props
            if "additional_properties" in circuits_obj:
                additional_props = circuits_obj["additional_properties"]
                if isinstance(additional_props, dict):
                    _LOGGER.debug(
                        "Cache UPDATE circuits: found circuits.additional_properties with %d items",
                        len(additional_props),
                    )
                    return additional_props
            # Maybe the circuits object itself contains the circuit data directly
            # Check if any keys look like circuit IDs
            circuit_like_keys = [
                k
                for k in circuits_obj
                if isinstance(k, str) and (k.startswith("unmapped_tab_") or k.isdigit() or len(k) > 5)
            ]
            if circuit_like_keys:
                _LOGGER.debug(
                    "Cache UPDATE circuits: using circuits directly with %d circuit-like keys: %s",
                    len(circuit_like_keys),
                    circuit_like_keys[:5],
                )
                return circuits_obj
            _LOGGER.debug("Cache UPDATE circuits: circuits keys don't look like circuit IDs: %s", list(circuits_obj.keys()))
            return {}
        if "additional_properties" in raw_data:
            additional_props = raw_data["additional_properties"]
            if isinstance(additional_props, dict):
                _LOGGER.debug("Cache UPDATE circuits: found additional_properties with %d items", len(additional_props))
                return additional_props
        _LOGGER.debug("Cache UPDATE circuits: could not find circuit data in raw_data")
        return {}

    def _update_existing_circuits(self, new_circuit_data: dict[str, Any]) -> int:
        """Update existing circuit objects with new data."""
        updated_count = 0

        for circuit_id, circuit_data in new_circuit_data.items():
            if circuit_id in self._circuit_objects:
                self._update_circuit_in_place(self._circuit_objects[circuit_id], circuit_data)
                updated_count += 1
            else:
                # New circuit (rare after initial load)
                new_circuit = Circuit.from_dict(circuit_data)
                self._circuit_objects[circuit_id] = new_circuit
                if self._circuits_cache is not None:
                    self._circuits_cache.circuits.additional_properties[circuit_id] = new_circuit
                _LOGGER.debug("Cache ADD new circuit: %s", circuit_id)

        return updated_count

    def update_circuits_from_api(self, raw_data: dict[str, Any]) -> CircuitsOut:
        """Update circuits cache from API response, reusing existing objects."""
        if self._circuits_cache is None:
            # First time - create objects
            self._initialize_circuits_cache(raw_data)
        else:
            # Update existing objects in place
            self._log_circuits_debug_info(raw_data)
            new_circuit_data = self._extract_circuit_data(raw_data)
            updated_count = self._update_existing_circuits(new_circuit_data)
            _LOGGER.debug("Cache UPDATE circuits: updated %d objects in place", updated_count)

        return self._circuits_cache

    def update_panel_state_from_api(self, raw_data: dict[str, Any]) -> PanelState:
        """Update panel state cache from API response."""
        try:
            if self._panel_state_cache is None:
                self._panel_state_cache = PanelState.from_dict(raw_data)
                _LOGGER.debug("Cache INIT for panel_state")
            else:
                # Update existing object in place
                self._update_panel_state_in_place(self._panel_state_cache, raw_data)
                _LOGGER.debug("Cache UPDATE panel_state: updated in place")

            return self._panel_state_cache
        except (KeyError, ValueError) as e:
            # Handle incomplete or invalid data - for test compatibility
            _LOGGER.debug("Cache UPDATE panel_state: incomplete data, skipping cache update: %s", e)
            # For test compatibility, don't update cache with invalid data
            if self._panel_state_cache is not None:
                return self._panel_state_cache
            # For tests that return minimal data, just skip caching
            return None

    def update_status_from_api(self, raw_data: dict[str, Any]) -> StatusOut:
        """Update status cache from API response."""
        try:
            if self._status_cache is None:
                self._status_cache = StatusOut.from_dict(raw_data)
                _LOGGER.debug("Cache INIT for status")
            else:
                # Update existing object in place
                self._update_status_in_place(self._status_cache, raw_data)
                _LOGGER.debug("Cache UPDATE status: updated in place")

            return self._status_cache
        except (KeyError, ValueError) as e:
            # Handle incomplete or invalid data - for test compatibility
            _LOGGER.debug("Cache UPDATE status: incomplete data, skipping cache update: %s", e)
            # For test compatibility, don't update cache with invalid data
            # Return existing cache if available, otherwise let the caller handle it
            if self._status_cache is not None:
                return self._status_cache
            # For tests that return minimal data, just skip caching
            return None

    def update_battery_storage_from_api(self, raw_data: dict[str, Any]) -> BatteryStorage:
        """Update battery storage cache from API response."""
        if self._battery_cache is None:
            self._battery_cache = BatteryStorage.from_dict(raw_data)
            _LOGGER.debug("Cache INIT for battery_storage")
        else:
            # Update existing object in place
            self._update_battery_storage_in_place(self._battery_cache, raw_data)
            _LOGGER.debug("Cache UPDATE battery_storage: updated in place")

        return self._battery_cache

    def _update_circuit_in_place(self, circuit: Circuit, new_data: dict[str, Any]) -> None:
        """Update circuit object attributes without recreating."""
        # Update dynamic power/energy fields
        circuit.instant_power_w = new_data.get("instantPowerW", circuit.instant_power_w)
        circuit.produced_energy_wh = new_data.get("producedEnergyWh", circuit.produced_energy_wh)
        circuit.consumed_energy_wh = new_data.get("consumedEnergyWh", circuit.consumed_energy_wh)
        circuit.instant_power_update_time_s = new_data.get("instantPowerUpdateTimeS", circuit.instant_power_update_time_s)
        circuit.energy_accum_update_time_s = new_data.get("energyAccumUpdateTimeS", circuit.energy_accum_update_time_s)

        # Update configuration fields that can change
        if "name" in new_data:
            circuit.name = new_data["name"]
        if "priority" in new_data:
            circuit.priority = Priority(new_data["priority"])
        if "tabs" in new_data:
            circuit.tabs = new_data["tabs"]
        if "isUserControllable" in new_data:
            circuit.is_user_controllable = new_data["isUserControllable"]
        if "isSheddable" in new_data:
            circuit.is_sheddable = new_data["isSheddable"]
        if "isNeverBackup" in new_data:
            circuit.is_never_backup = new_data["isNeverBackup"]
        if "relayState" in new_data:
            circuit.relay_state = RelayState(new_data["relayState"])

        # Update any additional properties
        if hasattr(circuit, "additional_properties"):
            for key, value in new_data.items():
                if key not in {
                    "id",
                    "instantPowerW",
                    "producedEnergyWh",
                    "consumedEnergyWh",
                    "instantPowerUpdateTimeS",
                    "energyAccumUpdateTimeS",
                    "name",
                    "priority",
                    "tabs",
                    "isUserControllable",
                    "isSheddable",
                    "isNeverBackup",
                    "relayState",
                }:
                    circuit.additional_properties[key] = value

    def _update_panel_state_in_place(self, panel_state: PanelState, new_data: dict[str, Any]) -> None:
        """Update panel state object attributes without recreating."""
        panel_state.instant_grid_power_w = new_data.get("instantGridPowerW", panel_state.instant_grid_power_w)
        panel_state.grid_sample_start_ms = new_data.get("gridSampleStartMs", panel_state.grid_sample_start_ms)
        panel_state.grid_sample_end_ms = new_data.get("gridSampleEndMs", panel_state.grid_sample_end_ms)

        if "relayState" in new_data:
            panel_state.relay_state = RelayState(new_data["relayState"])

    def _update_status_in_place(self, status: StatusOut, new_data: dict[str, Any]) -> None:
        """Update status object attributes without recreating."""
        # Update basic fields that might change
        if "software" in new_data and hasattr(status, "software"):
            # Update software status fields as needed
            pass
        if "system" in new_data and hasattr(status, "system"):
            # Update system status fields as needed
            pass

    def _update_battery_storage_in_place(self, battery: BatteryStorage, new_data: dict[str, Any]) -> None:
        """Update battery storage object attributes without recreating."""
        if "soe" in new_data and hasattr(battery, "soe"):
            soe_data = new_data["soe"]
            battery.soe.percentage = soe_data.get("percentage", battery.soe.percentage)

    def clear_on_failure(self) -> None:
        """Clear cache only when API calls fail."""
        self._circuits_cache = None
        self._panel_state_cache = None
        self._status_cache = None
        self._battery_cache = None
        self._circuit_objects.clear()
        _LOGGER.debug("Cache CLEARED due to API failure")

    def is_initialized(self) -> bool:
        """Check if cache has been initialized with data."""
        return self._circuits_cache is not None

    # Temporary compatibility methods for simulation and bulk operations
    def get_cached_data(self, cache_key: str) -> Any | None:
        """Compatibility method for simulation and bulk operations."""
        cache_map = {
            "status": self._status_cache,
            "panel_state": self._panel_state_cache,
            "circuits": self._circuits_cache,
            "storage_soe": self._battery_cache,
        }
        return cache_map.get(cache_key)

    def set_cached_data(self, cache_key: str, data: Any) -> None:
        """Compatibility method for simulation operations."""
        # For simulation keys, we don't persist - just ignore
        # Live API calls should use the update_*_from_api methods instead

    def clear(self) -> None:
        """Clear all cached data - compatibility method for simulation operations."""
        self.clear_on_failure()


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

        # Initialize persistent object cache
        self._api_cache = PersistentObjectCache()

        # Track background refresh tasks
        self._background_tasks: set[asyncio.Task[None]] = set()

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
        try:
            await self._client.__aenter__()
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
                        keepalive_expiry=30.0,  # Keep connections alive for 30 seconds
                    ),
                }

                # Create a new authenticated client
                self._client = AuthenticatedClient(
                    base_url=self._base_url,
                    token=self._access_token,
                    timeout=httpx.Timeout(self._timeout),
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
                keepalive_expiry=30.0,  # Keep connections alive for 30 seconds
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
        # Configure httpx for better connection pooling and persistence
        httpx_args = {
            "limits": httpx.Limits(
                max_keepalive_connections=5,  # Keep connections alive
                max_connections=10,  # Allow multiple connections
                keepalive_expiry=30.0,  # Keep connections alive for 30 seconds
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
            # Type cast needed because generated API has overly strict type hints
            response = await generate_jwt_api_v1_auth_register_post.asyncio(
                client=cast(AuthenticatedClient, client), body=auth_in
            )
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

        # Check persistent cache first
        cached_status = self._api_cache.get_status()
        if cached_status is not None:
            return cached_status

        # Get simulation data
        status_data = await self._simulation_engine.get_status()

        # Convert to model object
        status_out = self._convert_raw_to_status_out(status_data)

        # Cache the result using persistent cache
        cached_result = self._api_cache.update_status_from_api(status_data)
        if cached_result is not None:
            return cached_result
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

        # Check persistent cache first - always return immediately if available
        cached_status = self._api_cache.get_status()
        if cached_status is not None:
            # Trigger background refresh without blocking
            self._create_background_task(self._refresh_status_cache())
            return cached_status

        try:
            # First time only - fetch fresh data and initialize cache
            start_time = time.time()
            status = await self._retry_with_backoff(_get_status_operation)
            api_duration = time.time() - start_time
            _LOGGER.debug("Status API call took %.3fs", api_duration)
            # Update persistent cache with fresh data
            # Handle both StatusOut objects and dict responses
            status_dict = status.to_dict() if hasattr(status, "to_dict") else status
            cached_result = self._api_cache.update_status_from_api(status_dict)
            if cached_result is not None:
                return cached_result
            # If cache update failed (invalid data), return the original status
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

        # Check persistent cache first
        cached_panel = self._api_cache.get_panel_state()
        if cached_panel is not None:
            _LOGGER.debug("Panel state cache HIT in simulation")
            return cached_panel

        # Get simulation data
        full_data = await self._simulation_engine.get_panel_data()
        panel_data = full_data.get("panel", {})

        # Convert to model object
        panel_state = self._convert_raw_to_panel_state(panel_data)

        # Synchronize branch power with circuit power for consistency
        await self._synchronize_branch_power_with_circuits(panel_state, full_data)

        # Note: Panel grid power will be recalculated after circuits are processed
        # to ensure consistency with the actual circuit power values

        # Cache the result using persistent cache
        _LOGGER.debug("Panel state cache SET in simulation")
        cached_result = self._api_cache.update_panel_state_from_api(panel_state.to_dict())
        if cached_result is not None:
            return cached_result
        return panel_state

    async def _adjust_panel_power_for_virtual_circuits(self, panel_state: PanelState) -> None:
        """Adjust panel power to include unmapped tab power for consistency with circuit totals."""
        if not hasattr(panel_state, "branches") or not panel_state.branches:
            return

        # Get the current circuits to find mapped tabs
        cache_key = "full_sim_data"
        cached_full_data = self._api_cache.get_cached_data(cache_key)
        if cached_full_data is None:
            return

        circuits_data = cached_full_data.get("circuits", {})
        circuits_out = self._convert_raw_to_circuits_out(circuits_data)

        # Find tabs already mapped to circuits
        mapped_tabs: set[int] = set()
        if hasattr(circuits_out, "circuits") and hasattr(circuits_out.circuits, "additional_properties"):
            for circuit in circuits_out.circuits.additional_properties.values():
                if hasattr(circuit, "tabs") and circuit.tabs is not None and str(circuit.tabs) != "UNSET":
                    if isinstance(circuit.tabs, list | tuple):
                        mapped_tabs.update(circuit.tabs)
                    elif isinstance(circuit.tabs, int):
                        mapped_tabs.add(circuit.tabs)

        # Calculate unmapped tab power from branches
        unmapped_tab_power = 0.0
        total_tabs = len(panel_state.branches)
        all_tabs = set(range(1, total_tabs + 1))
        unmapped_tabs = all_tabs - mapped_tabs

        for tab_num in unmapped_tabs:
            branch_idx = tab_num - 1
            if branch_idx < len(panel_state.branches):
                branch = panel_state.branches[branch_idx]
                unmapped_tab_power += branch.instant_power_w

        # Add unmapped tab power to panel grid power
        panel_state.instant_grid_power_w += unmapped_tab_power

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
        """Recalculate panel grid power to match the actual circuit power values."""
        # Get the cached panel state to update
        cached_panel = self._api_cache.get_panel_state()
        if cached_panel is None:
            _LOGGER.debug("No cached panel state to update")
            return

        # Calculate consumption, production, and energy from actual circuit data
        total_consumption = 0.0
        total_production = 0.0
        total_produced_energy = 0.0
        total_consumed_energy = 0.0

        if hasattr(circuits_out, "circuits") and hasattr(circuits_out.circuits, "additional_properties"):
            for circuit_id, circuit in circuits_out.circuits.additional_properties.items():
                if circuit_id.startswith("unmapped_tab_"):
                    continue  # Skip virtual circuits for this calculation

                circuit_power = circuit.instant_power_w
                circuit_name = circuit.name.lower() if circuit.name else ""

                # Add to energy totals
                total_produced_energy += circuit.produced_energy_wh or 0.0
                total_consumed_energy += circuit.consumed_energy_wh or 0.0

                # Identify producer circuits by name
                if any(keyword in circuit_name for keyword in ["solar", "inverter", "generator", "battery"]):
                    total_production += circuit_power
                else:
                    total_consumption += circuit_power

        # Update panel grid power: consumption - production
        new_grid_power = total_consumption - total_production
        cached_panel.instant_grid_power_w = new_grid_power

        # Update panel energy to match circuit totals
        cached_panel.main_meter_energy.produced_energy_wh = total_produced_energy
        cached_panel.main_meter_energy.consumed_energy_wh = total_consumed_energy

        _LOGGER.debug(
            "Panel data recalculated: consumption=%.1fW, production=%.1fW, grid=%.1fW, "
            "produced_energy=%.6fWh, consumed_energy=%.6fWh",
            total_consumption,
            total_production,
            new_grid_power,
            total_produced_energy,
            total_consumed_energy,
        )

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

        # Check persistent cache first - always return immediately if available
        cached_state = self._api_cache.get_panel_state()
        if cached_state is not None:
            # Trigger background refresh without blocking
            self._create_background_task(self._refresh_panel_state_cache())
            return cached_state

        try:
            # First time only - fetch fresh data and initialize cache
            start_time = time.time()
            state = await self._retry_with_backoff(_get_panel_state_operation)
            api_duration = time.time() - start_time
            _LOGGER.debug("Panel state API call took %.3fs", api_duration)
            # Update persistent cache with fresh data
            # Handle both PanelState objects and dict responses
            state_dict = state.to_dict() if hasattr(state, "to_dict") else state
            cached_result = self._api_cache.update_panel_state_from_api(state_dict)
            if cached_result is not None:
                return cached_result
            # If cache update failed (invalid data), return the original state
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

        # Check persistent cache first
        cached_circuits = self._api_cache.get_circuits()
        if cached_circuits is not None:
            return cached_circuits

        # Get simulation data
        full_data = await self._simulation_engine.get_panel_data()
        circuits_data = full_data.get("circuits", {})

        # Convert to model object and apply unmapped tab logic (same as live mode)
        circuits_out = self._convert_raw_to_circuits_out(circuits_data)

        panel_state = await self.get_panel_state()

        if hasattr(panel_state, "branches") and panel_state.branches:
            self._add_unmapped_virtuals(circuits_out, panel_state.branches)
        else:
            _LOGGER.debug("No branches in panel state (simulation), skipping unmapped circuit creation")

        # Recalculate panel grid power to match circuit totals
        await self._recalculate_panel_grid_power_from_circuits(circuits_out)

        # Cache the result using persistent cache with the modified circuits data
        circuits_with_virtuals_data = circuits_out.to_dict()
        cached_result = self._api_cache.update_circuits_from_api(circuits_with_virtuals_data)
        if cached_result is not None:
            return cached_result
        return circuits_out

    async def _get_circuits_live(self) -> CircuitsOut:
        """Get circuits data from live panel."""

        async def _get_circuits_raw_operation() -> dict[str, Any]:
            """Get raw circuits data for persistent cache updates."""
            client = self._get_client_for_endpoint(requires_auth=True)
            response = await get_circuits_api_v1_circuits_get.asyncio_detailed(client=cast(AuthenticatedClient, client))
            if response.status_code != 200 or response.parsed is None:
                raise SpanPanelAPIError(f"API call failed with status {response.status_code}")

            # Return raw dict data for cache processing
            return cast(dict[str, Any], response.parsed.to_dict())

        async def _get_circuits_operation() -> CircuitsOut:
            # Get standard circuits response
            client = self._get_client_for_endpoint(requires_auth=True)
            result = await get_circuits_api_v1_circuits_get.asyncio(client=cast(AuthenticatedClient, client))
            if result is None:
                raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")

            # Get panel state for branches data
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

        # Check persistent cache first - always return immediately if available
        cached_circuits = self._api_cache.get_circuits()
        if cached_circuits is not None:
            # Trigger background refresh without blocking
            self._create_background_task(self._refresh_circuits_cache())
            return cached_circuits

        try:
            # First time only - fetch fresh data and initialize cache
            raw_circuits_data = await self._retry_with_backoff(_get_circuits_raw_operation)

            # Update persistent cache with fresh data (reuses objects)
            circuits = self._api_cache.update_circuits_from_api(raw_circuits_data)

            # Add virtual circuits for unmapped tabs
            panel_state = await self.get_panel_state()
            if hasattr(panel_state, "branches") and panel_state.branches:
                self._add_unmapped_virtuals(circuits, panel_state.branches)

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

    def _ensure_unmapped_circuits_in_cache(self, cached_circuits: CircuitsOut) -> None:
        """Ensure unmapped circuits are present in cached circuits data.

        This is a defensive method that never raises exceptions to avoid breaking cache functionality.

        Args:
            cached_circuits: The cached circuits data to potentially augment
        """
        try:
            cached_state = self._api_cache.get_cached_data("panel_state")
            if cached_state is None or not hasattr(cached_state, "branches") or not cached_state.branches:
                return

            # Find mapped tabs from cached circuits
            mapped_tabs: set[int] = set()
            if not (hasattr(cached_circuits, "circuits") and hasattr(cached_circuits.circuits, "additional_properties")):
                return

            for circuit in cached_circuits.circuits.additional_properties.values():
                if hasattr(circuit, "tabs") and circuit.tabs is not None and str(circuit.tabs) != "UNSET":
                    if isinstance(circuit.tabs, list | tuple):
                        mapped_tabs.update(circuit.tabs)
                    elif isinstance(circuit.tabs, int):
                        mapped_tabs.add(circuit.tabs)

            total_tabs = len(cached_state.branches)
            all_tabs = set(range(1, total_tabs + 1))
            unmapped_tabs = all_tabs - mapped_tabs

            for tab_num in unmapped_tabs:
                branch_idx = tab_num - 1
                if branch_idx < len(cached_state.branches):
                    branch = cached_state.branches[branch_idx]
                    virtual_circuit = self._create_unmapped_tab_circuit(branch, tab_num)
                    circuit_id = f"unmapped_tab_{tab_num}"
                    cached_circuits.circuits.additional_properties[circuit_id] = virtual_circuit
        except (AttributeError, IndexError, KeyError, TypeError):  # Defensive: never fail on cache post-processing
            # Log at debug level but don't fail - this is defensive code
            _LOGGER.debug("Error ensuring unmapped circuits in cache, continuing with cached data")

    async def _refresh_circuits_cache(self) -> None:
        """Background task to refresh circuits cache without blocking reads."""
        try:

            async def _get_circuits_raw_operation() -> dict[str, Any]:
                """Get raw circuits data for persistent cache updates."""
                client = self._get_client_for_endpoint(requires_auth=True)
                response = await get_circuits_api_v1_circuits_get.asyncio_detailed(client=cast(AuthenticatedClient, client))
                if response.status_code != 200 or response.parsed is None:
                    raise SpanPanelAPIError(f"API call failed with status {response.status_code}")

                # Return raw dict data for cache processing
                return cast(dict[str, Any], response.parsed.to_dict())

            raw_circuits_data = await self._retry_with_backoff(_get_circuits_raw_operation)
            self._api_cache.update_circuits_from_api(raw_circuits_data)

            # Update virtual circuits
            panel_state = await self.get_panel_state()
            if hasattr(panel_state, "branches") and panel_state.branches:
                cached_circuits = self._api_cache.get_circuits()
                if cached_circuits:
                    self._add_unmapped_virtuals(cached_circuits, panel_state.branches)
        except (SpanPanelAPIError, SpanPanelConnectionError, SpanPanelTimeoutError, SpanPanelAuthError) as e:
            # Log error but don't fail - this is background refresh
            _LOGGER.debug("Background circuits cache refresh failed: %s", e)

    async def _refresh_status_cache(self) -> None:
        """Background task to refresh status cache without blocking reads."""
        try:

            async def _get_status_operation() -> StatusOut:
                client = self._get_client_for_endpoint(requires_auth=False)
                result = await system_status_api_v1_status_get.asyncio(client=cast(AuthenticatedClient, client))
                if result is None:
                    raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
                return result

            status = await self._retry_with_backoff(_get_status_operation)
            self._api_cache.update_status_from_api(status.to_dict())
        except (SpanPanelAPIError, SpanPanelConnectionError, SpanPanelTimeoutError, SpanPanelAuthError) as e:
            _LOGGER.debug("Background status cache refresh failed: %s", e)

    async def _refresh_panel_state_cache(self) -> None:
        """Background task to refresh panel state cache without blocking reads."""
        try:

            async def _get_panel_state_operation() -> PanelState:
                client = self._get_client_for_endpoint(requires_auth=True)
                result = await get_panel_state_api_v1_panel_get.asyncio(client=cast(AuthenticatedClient, client))
                if result is None:
                    raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
                return result

            panel_state = await self._retry_with_backoff(_get_panel_state_operation)
            self._api_cache.update_panel_state_from_api(panel_state.to_dict())
        except (SpanPanelAPIError, SpanPanelConnectionError, SpanPanelTimeoutError, SpanPanelAuthError) as e:
            _LOGGER.debug("Background panel state cache refresh failed: %s", e)

    async def _refresh_battery_storage_cache(self) -> None:
        """Background task to refresh battery storage cache without blocking reads."""
        try:

            async def _get_storage_soe_operation() -> BatteryStorage:
                client = self._get_client_for_endpoint(requires_auth=True)
                result = await get_storage_soe_api_v1_storage_soe_get.asyncio(client=cast(AuthenticatedClient, client))
                if result is None:
                    raise SpanPanelAPIError("API result is None despite raise_on_unexpected_status=True")
                return result

            storage = await self._retry_with_backoff(_get_storage_soe_operation)
            self._api_cache.update_battery_storage_from_api(storage.to_dict())
        except (SpanPanelAPIError, SpanPanelConnectionError, SpanPanelTimeoutError, SpanPanelAuthError) as e:
            _LOGGER.debug("Background battery storage cache refresh failed: %s", e)

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
            """Safely convert energy value, returning None for invalid values (not 0.0)."""
            if value is None:
                return None
            if isinstance(value, int | float):
                return float(value)
            if isinstance(value, str):
                if value.lower() in ("unknown", "unavailable", "offline"):
                    return None  # Energy should be None when unavailable, not 0.0
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return None
            return None

        # Safely convert all values
        instant_power_w = _safe_power_conversion(getattr(branch, "instant_power_w", 0.0))
        # For solar tabs, imported energy represents production
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

        # Check persistent cache first
        cached_storage = self._api_cache.get_battery_storage()
        if cached_storage is not None:
            return cached_storage

        # Get simulation data
        storage_data = await self._simulation_engine.get_soe()

        # Convert to model object
        battery_storage = self._convert_raw_to_battery_storage(storage_data)

        # Cache the result using persistent cache
        cached_result = self._api_cache.update_battery_storage_from_api(storage_data)
        if cached_result is not None:
            return cached_result
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

        # Check persistent cache first - always return immediately if available
        cached_storage = self._api_cache.get_battery_storage()
        if cached_storage is not None:
            # Trigger background refresh without blocking
            self._create_background_task(self._refresh_battery_storage_cache())
            return cached_storage

        try:
            # First time only - fetch fresh data and initialize cache
            storage = await self._retry_with_backoff(_get_storage_soe_operation)
            # Update persistent cache with fresh data
            # Handle both BatteryStorage objects and dict responses
            storage_dict = storage.to_dict() if hasattr(storage, "to_dict") else storage
            return self._api_cache.update_battery_storage_from_api(storage_dict)
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

        # Clear caches since behavior has changed
        self._api_cache.clear()

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

        # Clear caches since behavior has changed
        self._api_cache.clear()

    def _get_cached_data(self, include_battery: bool) -> tuple[Any, Any, Any, Any]:
        """Get cached data for all data types."""
        cached_status = self._api_cache.get_cached_data("status")
        cached_panel = self._api_cache.get_cached_data("panel_state")
        cached_circuits = self._api_cache.get_cached_data("circuits")
        cached_storage = self._api_cache.get_cached_data("storage_soe") if include_battery else None
        return cached_status, cached_panel, cached_circuits, cached_storage

    def _log_cache_status(
        self, cached_status: Any, cached_panel: Any, cached_circuits: Any, cached_storage: Any, include_battery: bool
    ) -> None:
        """Log cache hit status for debugging."""
        cache_hits = []
        if cached_status is not None:
            cache_hits.append("status")
        if cached_panel is not None:
            cache_hits.append("panel")
        if cached_circuits is not None:
            cache_hits.append("circuits")
        if include_battery and cached_storage is not None:
            cache_hits.append("storage")

        _LOGGER.debug("Cache status - Hits: %s, Persistent cache enabled", cache_hits or "none")

    def _prepare_fetch_tasks(
        self, cached_status: Any, cached_panel: Any, cached_circuits: Any, cached_storage: Any, include_battery: bool
    ) -> tuple[list[Any], list[str]]:
        """Prepare tasks for fetching uncached data and trigger background refreshes."""
        tasks = []
        task_keys = []

        # Only fetch if cache is empty (first time)
        if cached_status is None:
            tasks.append(self.get_status())
            task_keys.append("status")
        else:
            # Trigger background refresh
            self._create_background_task(self._refresh_status_cache())

        if cached_panel is None:
            tasks.append(self.get_panel_state())
            task_keys.append("panel_state")
        else:
            # Trigger background refresh
            self._create_background_task(self._refresh_panel_state_cache())

        if cached_circuits is None:
            tasks.append(self.get_circuits())
            task_keys.append("circuits")
        else:
            # Trigger background refresh
            self._create_background_task(self._refresh_circuits_cache())

        if include_battery and cached_storage is None:
            tasks.append(self.get_storage_soe())
            task_keys.append("storage")
        elif include_battery:
            # Trigger background refresh
            self._create_background_task(self._refresh_battery_storage_cache())

        return tasks, task_keys

    def _update_cached_data_from_results(
        self,
        cached_status: Any,
        cached_panel: Any,
        cached_circuits: Any,
        cached_storage: Any,
        results: list[Any],
        task_keys: list[str],
    ) -> tuple[Any, Any, Any, Any]:
        """Update cached data with fresh results from API calls."""
        for i, key in enumerate(task_keys):
            if key == "status":
                cached_status = results[i]
            elif key == "panel_state":
                cached_panel = results[i]
            elif key == "circuits":
                cached_circuits = results[i]
            elif key == "storage":
                cached_storage = results[i]
        return cached_status, cached_panel, cached_circuits, cached_storage

    async def get_all_data(self, include_battery: bool = False) -> dict[str, Any]:
        """Get all panel data in parallel for maximum performance.

        This method makes concurrent API calls when cache misses occur,
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
        # Check cache for all data types first
        cached_status, cached_panel, cached_circuits, cached_storage = self._get_cached_data(include_battery)

        # Debug cache status
        self._log_cache_status(cached_status, cached_panel, cached_circuits, cached_storage, include_battery)

        # Prepare tasks for uncached data and trigger background refreshes
        tasks, task_keys = self._prepare_fetch_tasks(
            cached_status, cached_panel, cached_circuits, cached_storage, include_battery
        )

        # Execute uncached calls in parallel (should be rare after first load)
        if tasks:
            results = await asyncio.gather(*tasks)
        else:
            results = []

        # Update results with fresh data
        cached_status, cached_panel, cached_circuits, cached_storage = self._update_cached_data_from_results(
            cached_status, cached_panel, cached_circuits, cached_storage, results, task_keys
        )

        # Return all data
        result = {
            "status": cached_status,
            "panel_state": cached_panel,
            "circuits": cached_circuits,
        }

        if include_battery:
            result["storage"] = cached_storage

        return result

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        if self._client:
            # The generated client has async context manager support
            with suppress(Exception):
                await self._client.__aexit__(None, None, None)
            self._client = None
        self._in_context = False
