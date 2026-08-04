"""Adapter discovery via the `span_panel_api.schema_adapters` entry-point group.

Called once per process on the first create_span_client(). A venv change needs a
process restart regardless, so a process-lifetime cache is correct.
"""

from __future__ import annotations

from importlib.metadata import entry_points
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from span_panel_api.protocol import SchemaAdapter

_LOGGER = logging.getLogger(__name__)
_ENTRY_POINT_GROUP = "span_panel_api.schema_adapters"
_REGISTRY: dict[str, type[SchemaAdapter]] | None = None


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


def _reset_adapter_cache() -> None:
    """Test hook. Not public API."""
    global _REGISTRY  # pylint: disable=global-statement  # test hook for the cache above
    _REGISTRY = None
