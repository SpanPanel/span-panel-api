"""Protocol definitions for SPAN Panel transport clients.

Two complementary mechanisms provide transport-agnostic access:

1. **PanelCapability flags** (in models.py) — runtime advertisement of what a
   client supports.  Read at setup time to enable/disable entity platforms.

2. **Protocol classes** (this module) — static type narrowing.  The core
   SpanPanelClientProtocol is required by every transport.  The capability
   Protocols are optional mixins that allow type-safe dispatch to optional
   methods without ``# type: ignore``.

Usage pattern:

    caps = client.capabilities
    # Runtime gating — decide which platforms to load
    if PanelCapability.RELAY_CONTROL in caps:
        platforms.append("switch")

    # Static narrowing — type-safe optional method dispatch
    if isinstance(client, CircuitControlProtocol):
        await client.set_circuit_relay(circuit_id, "OPEN")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .models import PanelCapability, SpanPanelSnapshot


@runtime_checkable
class SpanPanelClientProtocol(Protocol):
    """Core protocol all SPAN panel transport clients must satisfy."""

    @property
    def capabilities(self) -> PanelCapability: ...

    async def connect(self) -> bool: ...

    async def close(self) -> None: ...

    async def ping(self) -> bool: ...

    async def get_snapshot(self) -> SpanPanelSnapshot: ...


@runtime_checkable
class AuthCapableProtocol(Protocol):
    """Mixin: panels that require JWT authentication (Gen2).

    Check: ``PanelCapability.AUTHENTICATION in client.capabilities``
    """

    async def authenticate(
        self,
        name: str,
        description: str = "",
        otp: str | None = None,
    ) -> object: ...

    def set_access_token(self, token: str) -> None: ...


@runtime_checkable
class CircuitControlProtocol(Protocol):
    """Mixin: panels that support circuit relay and priority writes (Gen2).

    Check: ``PanelCapability.RELAY_CONTROL in client.capabilities``
    """

    async def set_circuit_relay(self, circuit_id: str, state: str) -> object: ...

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> object: ...


@runtime_checkable
class EnergyCapableProtocol(Protocol):
    """Mixin: panels that expose energy history and battery SOE (Gen2).

    Check: ``PanelCapability.BATTERY in client.capabilities``

    Battery SOE percentage is also available via
    ``SpanPanelSnapshot.battery_soe`` returned from ``get_snapshot()``.
    """

    async def get_storage_soe(self) -> object: ...


@runtime_checkable
class StreamingCapableProtocol(Protocol):
    """Mixin: panels using push-streaming (Gen3 gRPC).

    Check: ``PanelCapability.PUSH_STREAMING in client.capabilities``
    """

    def register_callback(self, cb: Callable[[], None]) -> Callable[[], None]: ...

    async def start_streaming(self) -> None: ...

    async def stop_streaming(self) -> None: ...
