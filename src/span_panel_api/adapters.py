"""Adapter discovery via the `span_panel_api.schema_adapters` entry-point group.

Called once per process on the first create_span_client(). A venv change needs a
process restart regardless, so a process-lifetime cache is correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
import logging
from typing import TypeGuard

from span_panel_api.exceptions import SpanPanelAdapterIncompatibleError, SpanPanelAdapterMissingError
from span_panel_api.protocol import ADAPTER_CONTRACT_VERSION, SchemaAdapter

_LOGGER = logging.getLogger(__name__)
_ENTRY_POINT_GROUP = "span_panel_api.schema_adapters"


@dataclass(frozen=True)
class _Discovery:
    """One scan of the entry-point group: what was usable, and why the rest was not.

    Rejections are kept rather than only logged. A rejected adapter and an
    absent one are the same absence from ``adapters``, but they are opposite
    problems for whoever hits them — install something, versus upgrade what is
    already installed. Keeping the reason is what lets ``resolve_adapter`` tell
    them apart at the point the distinction matters, without re-scanning.
    """

    adapters: dict[str, type[SchemaAdapter]]
    rejected: dict[str, str]


_DISCOVERY: _Discovery | None = None


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

    Checks member *presence* only, which is all a Protocol can express at
    runtime: an adapter carrying every required name and the wrong ``__init__``
    arity still satisfies this. That gap is why the protocol also requires a
    declared ``ADAPTER_CONTRACT`` and why ``_contract_defect`` runs after this —
    presence answers "is this an adapter", the contract answers "is it one this
    package can drive".

    Worth having on its own regardless: it catches a module, function or
    instance registered where a class belongs, and turns it into a named, logged
    skip instead of an opaque TypeError deep inside connect().
    """
    return isinstance(loaded, type) and all(hasattr(loaded, member) for member in _REQUIRED_MEMBERS)


def _describe_defect(loaded: object) -> str:
    """Explain why `loaded` failed _is_adapter_class. Only called on the error path."""
    if not isinstance(loaded, type):
        return f"expected a class, got {type(loaded).__name__}"
    missing = [member for member in _REQUIRED_MEMBERS if not hasattr(loaded, member)]
    if "ADAPTER_CONTRACT" in missing:
        # Every adapter built for a contract-versioned bootstrap declares this,
        # so its absence dates the package rather than faulting it: this is an
        # adapter from before the contract was versioned at all.
        return (
            f"{loaded.__name__} declares no ADAPTER_CONTRACT, so it predates contract "
            f"versioning and was built against an older span-panel-api. Install an adapter "
            f"release built for contract {ADAPTER_CONTRACT_VERSION}."
        )
    return f"{loaded.__name__} does not implement SchemaAdapter (missing: {', '.join(missing)})."


def _contract_defect(adapter_cls: type[SchemaAdapter]) -> str | None:
    """Reject an adapter built against a different contract. None when usable.

    Runs only after `_is_adapter_class`, so the attribute is known to exist and
    the remaining questions are whether it is an integer and whether it agrees.
    """
    declared: object = adapter_cls.ADAPTER_CONTRACT
    # bool is a subclass of int, and `ADAPTER_CONTRACT = True` comparing equal
    # to contract 1 would be an absurd way to pass this check.
    if not isinstance(declared, int) or isinstance(declared, bool):
        return f"{adapter_cls.__name__} declares ADAPTER_CONTRACT={declared!r}, which is not an integer."
    if declared != ADAPTER_CONTRACT_VERSION:
        direction = "older than" if declared < ADAPTER_CONTRACT_VERSION else "newer than"
        return (
            f"{adapter_cls.__name__} is built for adapter contract {declared}, "
            f"{direction} the contract {ADAPTER_CONTRACT_VERSION} this span-panel-api speaks."
        )
    return None


def _discover() -> _Discovery:
    """Scan and cache the entry-point group, keeping rejections alongside adapters.

    A bad entry point is skipped with a logged reason, never raised: one broken
    third-party adapter must not take down a panel whose own adapter is fine.
    Whether a skip matters is decided later, by whoever asks for that key.
    """
    global _DISCOVERY  # pylint: disable=global-statement  # process-lifetime cache by design
    if _DISCOVERY is None:
        adapters: dict[str, type[SchemaAdapter]] = {}
        rejected: dict[str, str] = {}
        for ep in entry_points(group=_ENTRY_POINT_GROUP):
            if ep.name in adapters or ep.name in rejected:
                _LOGGER.warning("Duplicate schema adapter entry point %r; keeping the first found", ep.name)
                continue
            try:
                loaded: object = ep.load()
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to load schema adapter entry point %r", ep.name)
                rejected[ep.name] = "the package raised on import; see the logged traceback."
                continue
            if not _is_adapter_class(loaded):
                shape_defect = _describe_defect(loaded)
                _LOGGER.error("Ignoring schema adapter entry point %r: %s", ep.name, shape_defect)
                rejected[ep.name] = shape_defect
                continue
            if (contract_defect := _contract_defect(loaded)) is not None:
                _LOGGER.error("Ignoring schema adapter entry point %r: %s", ep.name, contract_defect)
                rejected[ep.name] = contract_defect
                continue
            adapters[ep.name] = loaded
        _DISCOVERY = _Discovery(adapters=adapters, rejected=rejected)
    return _DISCOVERY


def discover_adapters() -> dict[str, type[SchemaAdapter]]:
    """Every adapter class this package can actually drive, by entry-point name.

    Rejected entry points are deliberately absent rather than present-but-broken:
    a caller iterating this should never have to re-check what discovery already
    decided.
    """
    return _discover().adapters


def resolve_adapter(key: str, reason: str) -> type[SchemaAdapter]:
    """Return the discovered adapter class for `key`, or raise saying why not.

    The one place an unavailable adapter turns into a named error. Both the
    factory's Tier 1 dispatch and the transport's default path go through here so
    a user whose panel outruns their install sees the same message either way.

    Absent and rejected are separated here rather than at discovery, because
    only here is it known that this particular key is the one the panel needs.
    """
    discovery = _discover()
    adapter_cls = discovery.adapters.get(key)
    if adapter_cls is not None:
        return adapter_cls
    if (defect := discovery.rejected.get(key)) is not None:
        raise SpanPanelAdapterIncompatibleError(needed=key, reason=reason, defect=defect)
    raise SpanPanelAdapterMissingError(needed=key, reason=reason, available=sorted(discovery.adapters))


def _reset_adapter_cache() -> None:
    """Test hook. Not public API."""
    global _DISCOVERY  # pylint: disable=global-statement  # test hook for the cache above
    _DISCOVERY = None
