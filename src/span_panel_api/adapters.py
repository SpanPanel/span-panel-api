"""Adapter discovery via the `span_panel_api.schema_adapters` entry-point group.

Called once per process on the first create_span_client(). A venv change needs a
process restart regardless, so a process-lifetime cache is correct.
"""

from __future__ import annotations

from importlib.metadata import entry_points
import logging
from typing import TYPE_CHECKING

from span_panel_api.exceptions import SpanPanelAdapterMissingError

if TYPE_CHECKING:
    from span_panel_api.protocol import SchemaAdapter

_LOGGER = logging.getLogger(__name__)
_ENTRY_POINT_GROUP = "span_panel_api.schema_adapters"
_REGISTRY: dict[str, type[SchemaAdapter]] | None = None

# The adapter key for panels that publish no data-model-version. This is a
# bootstrap-level fact — Tier 1 dispatch reads absence as "flat schema" — not an
# import of the flat adapter. The bootstrap knows the *name*; whether anything
# answers to it is entry-point discovery's problem.
DEFAULT_ADAPTER_KEY = "schema_0"


def discover_adapters() -> dict[str, type[SchemaAdapter]]:
    """Load and cache every adapter class registered under the entry-point group."""
    global _REGISTRY  # pylint: disable=global-statement  # process-lifetime cache by design
    if _REGISTRY is None:
        registry: dict[str, type[SchemaAdapter]] = {}
        for ep in entry_points(group=_ENTRY_POINT_GROUP):
            if ep.name in registry:
                _LOGGER.warning("Duplicate schema adapter entry point %r; keeping the first found", ep.name)
                continue
            try:
                registry[ep.name] = ep.load()
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to load schema adapter entry point %r", ep.name)
        _REGISTRY = registry
    return _REGISTRY


def resolve_adapter(key: str, reason: str) -> type[SchemaAdapter]:
    """Return the discovered adapter class for `key`, or raise naming what is installed.

    The one place a missing adapter turns into a named error. Both the factory's
    Tier 1 dispatch and the transport's default path go through here so a user
    whose panel outruns their install sees the same message either way.
    """
    registry = discover_adapters()
    adapter_cls = registry.get(key)
    if adapter_cls is None:
        raise SpanPanelAdapterMissingError(needed=key, reason=reason, available=sorted(registry))
    return adapter_cls


def _reset_adapter_cache() -> None:
    """Test hook. Not public API."""
    global _REGISTRY  # pylint: disable=global-statement  # test hook for the cache above
    _REGISTRY = None
