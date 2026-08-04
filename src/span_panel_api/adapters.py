"""Adapter discovery via the `span_panel_api.schema_adapters` entry-point group.

Called once per process on the first create_span_client(). A venv change needs a
process restart regardless, so a process-lifetime cache is correct.
"""

from __future__ import annotations

from importlib.metadata import entry_points
import logging
from typing import TypeGuard

from span_panel_api.exceptions import SpanPanelAdapterMissingError
from span_panel_api.protocol import SchemaAdapter

_LOGGER = logging.getLogger(__name__)
_ENTRY_POINT_GROUP = "span_panel_api.schema_adapters"
_REGISTRY: dict[str, type[SchemaAdapter]] | None = None


def _derive_required_members(protocol: type) -> tuple[str, ...]:
    """Every public member a protocol declares, whatever kind it is.

    Derived from the protocol rather than restated, so the check cannot drift
    out of sync with the contract it enforces — adding any public member to
    SchemaAdapter automatically makes it required of every adapter package.

    Two sources, because a protocol declares members two ways: annotation-only
    data members live in ``__annotations__`` and never reach ``vars()``, while
    anything with a body lives in ``vars()`` and is not annotated.

    Member *kind* is deliberately not filtered on. Screening ``vars()`` for
    ``callable`` looks equivalent and is not: a ``property`` object is not
    callable and neither is a ``classmethod`` object, so that filter would
    silently stop requiring a member the day the protocol declared one. Every
    public name in ``vars()`` is a member the protocol body declared — Protocol's
    own machinery (``_is_protocol``, ``__protocol_attrs__``, ``__subclasshook__``)
    is uniformly underscore-prefixed — so no kind check is needed to begin with.

    ``issubclass`` is not an option here: SchemaAdapter has non-method members,
    and runtime_checkable protocols with data attributes reject it outright.
    """
    return (
        *sorted(getattr(protocol, "__annotations__", {})),
        *sorted(name for name in vars(protocol) if not name.startswith("_")),
    )


_REQUIRED_MEMBERS: tuple[str, ...] = _derive_required_members(SchemaAdapter)

# The adapter key for panels that publish no data-model-version. This is a
# bootstrap-level fact — Tier 1 dispatch reads absence as "flat schema" — not an
# import of the flat adapter. The bootstrap knows the *name*; whether anything
# answers to it is entry-point discovery's problem.
DEFAULT_ADAPTER_KEY = "schema_0"


def _is_adapter_class(loaded: object) -> TypeGuard[type[SchemaAdapter]]:
    """Narrow an entry point's loaded object to an adapter class.

    A TypeGuard rather than a bare bool: `ep.load()` returns `Any`, and this is
    the boundary where that `Any` has to become a checked `type[SchemaAdapter]`
    rather than being assigned into the registry unexamined.

    Deliberately checks member *presence* only. A Protocol cannot express
    signatures at runtime, so an adapter with the right names and the wrong
    arity still gets through and fails at call time. The check is worth having
    anyway: it catches the failure that actually happens — a module, function or
    instance registered where a class belongs — and turns it into a named,
    logged skip instead of an opaque TypeError deep inside connect().
    """
    return isinstance(loaded, type) and all(hasattr(loaded, member) for member in _REQUIRED_MEMBERS)


def _describe_defect(loaded: object) -> str:
    """Explain why `loaded` failed _is_adapter_class. Only called on the error path."""
    if not isinstance(loaded, type):
        return f"expected a class, got {type(loaded).__name__}"
    missing = [member for member in _REQUIRED_MEMBERS if not hasattr(loaded, member)]
    return f"{loaded.__name__} does not implement SchemaAdapter (missing: {', '.join(missing)})"


def discover_adapters() -> dict[str, type[SchemaAdapter]]:
    """Load and cache every adapter class registered under the entry-point group.

    A bad entry point is skipped with a logged reason, never raised: one broken
    third-party adapter must not take down a panel whose own adapter is fine.
    """
    global _REGISTRY  # pylint: disable=global-statement  # process-lifetime cache by design
    if _REGISTRY is None:
        registry: dict[str, type[SchemaAdapter]] = {}
        for ep in entry_points(group=_ENTRY_POINT_GROUP):
            if ep.name in registry:
                _LOGGER.warning("Duplicate schema adapter entry point %r; keeping the first found", ep.name)
                continue
            try:
                loaded: object = ep.load()
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to load schema adapter entry point %r", ep.name)
                continue
            if not _is_adapter_class(loaded):
                _LOGGER.error("Ignoring schema adapter entry point %r: %s", ep.name, _describe_defect(loaded))
                continue
            registry[ep.name] = loaded
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
