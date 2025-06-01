"""High-level async client for SPAN Panel API."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from .exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
)

# Add the generated client to the path
generated_client_path = os.path.join(
    os.path.dirname(__file__), "..", "..", "generated_client"
)
sys.path.insert(0, os.path.abspath(generated_client_path))

try:
    from span_panel_api_client import ApiClient, Configuration
    from span_panel_api_client.api.default_api import DefaultApi
    from span_panel_api_client.models import (
        AuthIn,
        AuthOut,
        CircuitNameIn,
        CircuitsOut,
        PriorityIn,
        RelayStateIn,
        StatusOut,
        WifiConnectIn,
        WifiScanOut,
    )
except ImportError as e:
    raise ImportError(
        f"Could not import the generated client: {e}. "
        "Make sure the generated_client directory is present in the project root."
    ) from e


class SpanPanelClient:
    """Modern async client for SPAN Panel REST API.

    This client provides a clean, async interface to the SPAN Panel API
    using the generated OpenAPI client as the underlying transport.

    Example:
        async with SpanPanelClient("192.168.1.100") as client:
            # Authenticate
            auth = await client.authenticate("my-app", "My Application")

            # Get panel status
            status = await client.get_status()
            print(f"Panel: {status.system.manufacturer}")

            # Get circuits
            circuits = await client.get_circuits()
            for circuit_id, circuit in circuits.circuits.items():
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

        # Generated API client components
        self._config: Configuration | None = None
        self._api_client: ApiClient | None = None
        self._api: DefaultApi | None = None
        self._access_token: str | None = None

    @asynccontextmanager
    async def _get_api(self) -> AsyncGenerator[DefaultApi, None]:
        """Get the API client, initializing if needed."""
        if self._api is None:
            self._config = Configuration(host=self._base_url)
            if self._access_token:
                self._config.access_token = self._access_token

            self._api_client = ApiClient(self._config)
            self._api = DefaultApi(self._api_client)

        try:
            yield self._api
        except httpx.ConnectError as e:
            raise SpanPanelConnectionError(f"Failed to connect to {self._host}") from e
        except httpx.TimeoutException as e:
            raise SpanPanelTimeoutError(
                f"Request timed out after {self._timeout}s"
            ) from e
        except Exception as e:
            if "401" in str(e) or "Unauthorized" in str(e):
                raise SpanPanelAuthError("Authentication failed") from e
            raise SpanPanelAPIError(f"API error: {e}") from e

    async def __aenter__(self) -> SpanPanelClient:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        if self._api_client:
            await self._api_client.close()

    def set_access_token(self, token: str) -> None:
        """Set the access token for API authentication."""
        self._access_token = token
        if self._config:
            self._config.access_token = token

    # Authentication Methods
    async def authenticate(self, name: str, description: str = "") -> AuthOut:
        """Register and authenticate a new API client.

        Args:
            name: Client name
            description: Optional client description

        Returns:
            AuthOut containing access token
        """
        async with self._get_api() as api:
            auth_in = AuthIn(name=name, description=description)
            response = await api.generate_jwt_api_v1_auth_register_post(auth_in)

            # Store the token for future requests
            self.set_access_token(response.access_token)
            return response

    # Panel Status and Info
    async def get_status(self) -> StatusOut:
        """Get complete panel system status."""
        async with self._get_api() as api:
            return await api.system_status_api_v1_status_get()

    async def get_panel_state(self) -> Any:
        """Get detailed panel state including power and energy data."""
        async with self._get_api() as api:
            return await api.get_panel_state_api_v1_panel_get()

    async def get_panel_power(self) -> Any:
        """Get current panel power measurements."""
        async with self._get_api() as api:
            return await api.get_panel_power_api_v1_panel_power_get()

    async def get_panel_meter(self) -> Any:
        """Get panel meter energy data."""
        async with self._get_api() as api:
            return await api.get_panel_meter_api_v1_panel_meter_get()

    # Circuit Management
    async def get_circuits(self) -> CircuitsOut:
        """Get all circuits and their current state."""
        async with self._get_api() as api:
            return await api.get_circuits_api_v1_circuits_get()

    async def get_circuit(self, circuit_id: str) -> Any:
        """Get specific circuit information.

        Args:
            circuit_id: Circuit identifier
        """
        async with self._get_api() as api:
            return await api.get_circuit_state_api_v1_circuits_circuit_id_get(
                circuit_id
            )

    async def set_circuit_relay(self, circuit_id: str, state: str) -> Any:
        """Control circuit relay state.

        Args:
            circuit_id: Circuit identifier
            state: Relay state ("OPEN" or "CLOSED")
        """
        async with self._get_api() as api:
            relay_in = RelayStateIn(relay_state=state)
            body = {"relayStateIn": relay_in}
            return await api.set_circuit_state_api_v1_circuits_circuit_id_post(
                circuit_id, body
            )

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> Any:
        """Set circuit priority level.

        Args:
            circuit_id: Circuit identifier
            priority: Priority level ("MUST_HAVE", "NICE_TO_HAVE", "NON_ESSENTIAL")
        """
        async with self._get_api() as api:
            priority_in = PriorityIn(priority=priority)
            body = {"priorityIn": priority_in}
            return await api.set_circuit_state_api_v1_circuits_circuit_id_post(
                circuit_id, body
            )

    async def set_circuit_name(self, circuit_id: str, name: str) -> Any:
        """Set circuit name.

        Args:
            circuit_id: Circuit identifier
            name: New circuit name
        """
        async with self._get_api() as api:
            name_in = CircuitNameIn(name=name)
            body = {"circuitNameIn": name_in}
            return await api.set_circuit_state_api_v1_circuits_circuit_id_post(
                circuit_id, body
            )

    # Main Panel Control
    async def get_main_relay_state(self) -> Any:
        """Get main panel relay state."""
        async with self._get_api() as api:
            return await api.get_main_relay_state_api_v1_panel_grid_get()

    async def set_main_relay_state(self, state: str) -> Any:
        """Set main panel relay state.

        Args:
            state: Relay state ("OPEN" or "CLOSED")
        """
        async with self._get_api() as api:
            relay_in = RelayStateIn(relay_state=state)
            return await api.set_main_relay_state_api_v1_panel_grid_post(relay_in)

    async def emergency_reconnect(self) -> Any:
        """Run emergency reconnect procedure."""
        async with self._get_api() as api:
            return (
                await api.run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post()
            )

    # Storage / Battery
    async def get_storage_soe(self) -> Any:
        """Get battery state of energy."""
        async with self._get_api() as api:
            return await api.get_storage_soe_api_v1_storage_soe_get()

    async def get_storage_thresholds(self) -> Any:
        """Get storage nice-to-have thresholds."""
        async with self._get_api() as api:
            return (
                await api.get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get()
            )

    # WiFi Management
    async def scan_wifi(self) -> WifiScanOut:
        """Scan for available WiFi networks."""
        async with self._get_api() as api:
            return await api.get_wifi_scan_api_v1_wifi_scan_get()

    async def connect_wifi(self, ssid: str, password: str) -> Any:
        """Connect to a WiFi network.

        Args:
            ssid: Network SSID
            password: Network password
        """
        async with self._get_api() as api:
            wifi_in = WifiConnectIn(ssid=ssid, psk=password)
            return await api.run_wifi_connect_api_v1_wifi_connect_post(wifi_in)

    # Grid Islanding
    async def get_islanding_state(self) -> Any:
        """Get grid islanding state."""
        async with self._get_api() as api:
            return await api.get_islanding_state_api_v1_islanding_state_get()

    # Client Management
    async def get_auth_clients(self) -> Any:
        """Get all registered auth clients."""
        async with self._get_api() as api:
            return await api.get_all_clients_api_v1_auth_clients_get()

    async def get_auth_client(self, name: str) -> Any:
        """Get specific auth client by name."""
        async with self._get_api() as api:
            return await api.get_client_api_v1_auth_clients_name_get(name)

    async def delete_auth_client(self, name: str) -> Any:
        """Delete an auth client by name."""
        async with self._get_api() as api:
            return await api.delete_client_api_v1_auth_clients_name_delete(name)
