"""Adapter discovery via the `span_panel_api.schema_adapters` entry-point group.

Two steps, deliberately separate, because they cost very different things:

*Enumeration* reads distribution metadata and answers "which adapter keys does
this environment register". *Resolution* imports one of those packages and
checks it implements the contract. Enumeration is a couple of file reads;
resolution of ``schema_1`` drags in the eBus SDK and jsonschema — measured at
two seconds on a cold import cache.

So only the key the panel actually reports is ever imported. An earlier version
resolved the whole group up front to build one registry, which meant every flat
panel paid for the parent/child parser it would never call — undoing the
containment schema-1's own packaging sets up, where the SDK dependency is
isolated to that distribution precisely so a flat install stays clear of it.
Under redispatch both adapters are the normal install, so "installed" stopped
implying "used" and eager resolution stopped being defensible.

Both steps cache for the life of the process. A venv change needs a restart
regardless, so nothing here can go stale while it matters.

**Everything in this module does blocking file I/O**, both the metadata reads
and the imports. Callers on an event loop must keep it off theirs; the async
transport does that with ``asyncio.to_thread``.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
import logging
from typing import TypeGuard

from span_panel_api.exceptions import SpanPanelAdapterIncompatibleError, SpanPanelAdapterMissingError
from span_panel_api.protocol import ADAPTER_CONTRACT_VERSION, SchemaAdapter

_LOGGER = logging.getLogger(__name__)
_ENTRY_POINT_GROUP = "span_panel_api.schema_adapters"

# Every entry point in the group, by name, unloaded. None means "not scanned".
_ENTRY_POINTS: dict[str, EntryPoint] | None = None
# Resolution verdicts, filled one key at a time. A key appears in exactly one:
# usable adapters here, and the reason for the rest in _REJECTED. Kept apart
# rather than as one nullable map because a rejected adapter and an absent one
# are opposite problems for whoever hits them — upgrade what is already
# installed, versus install something — and resolve_adapter can only tell them
# apart if the reason survives the scan that produced it.
_ADAPTERS: dict[str, type[SchemaAdapter]] = {}
_REJECTED: dict[str, str] = {}


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


def _enumerate() -> dict[str, EntryPoint]:
    """Scan the entry-point group by name, importing nothing.

    Names only, because a name is all it takes to answer the two questions asked
    before a panel has reported anything: what is installed, and does the key
    this panel needs appear at all. Loading is deferred to whoever asks for a
    specific key.
    """
    global _ENTRY_POINTS  # pylint: disable=global-statement  # process-lifetime cache by design
    if _ENTRY_POINTS is None:
        found: dict[str, EntryPoint] = {}
        for ep in entry_points(group=_ENTRY_POINT_GROUP):
            if ep.name in found:
                _LOGGER.warning("Duplicate schema adapter entry point %r; keeping the first found", ep.name)
                continue
            found[ep.name] = ep
        _ENTRY_POINTS = found
    return _ENTRY_POINTS


def _load_and_check(ep: EntryPoint) -> type[SchemaAdapter] | str:
    """Import one adapter and vet it, returning the class or the reason it is unusable.

    A defect is returned rather than raised so the caller decides what it means.
    Discovery has no standing to fail a connection: whether an unusable adapter
    matters depends entirely on whether the panel needs that key.
    """
    try:
        loaded: object = ep.load()
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Failed to load schema adapter entry point %r", ep.name)
        return "the package raised on import; see the logged traceback."
    if not _is_adapter_class(loaded):
        shape_defect = _describe_defect(loaded)
        _LOGGER.error("Ignoring schema adapter entry point %r: %s", ep.name, shape_defect)
        return shape_defect
    if (contract_defect := _contract_defect(loaded)) is not None:
        _LOGGER.error("Ignoring schema adapter entry point %r: %s", ep.name, contract_defect)
        return contract_defect
    return loaded


def installed_adapter_keys() -> list[str]:
    """Every adapter key this environment registers, sorted.

    Registered, not verified: naming a key here says a package claims it, not
    that the package loads or implements the current contract. Verifying would
    mean importing all of them, which is the cost this split exists to avoid,
    and the distinction only ever matters for one key — the one the panel needs,
    which ``resolve_adapter`` imports and vets on the spot.
    """
    return sorted(_enumerate())


def resolve_adapter(key: str, reason: str) -> type[SchemaAdapter]:
    """Return the adapter class for `key`, importing it on first use, or raise saying why not.

    The one place an unavailable adapter turns into a named error. Both the
    factory's Tier 1 dispatch and the transport's default path go through here so
    a user whose panel outruns their install sees the same message either way.

    Absent and rejected stay distinct: nothing registers the key at all, versus
    something does and cannot be driven. Same absence, opposite remedies.
    """
    if (cached := _ADAPTERS.get(key)) is not None:
        return cached
    if (cached_defect := _REJECTED.get(key)) is not None:
        raise SpanPanelAdapterIncompatibleError(needed=key, reason=reason, defect=cached_defect)

    ep = _enumerate().get(key)
    if ep is None:
        raise SpanPanelAdapterMissingError(needed=key, reason=reason, available=installed_adapter_keys())

    outcome = _load_and_check(ep)
    if isinstance(outcome, str):
        _REJECTED[key] = outcome
        raise SpanPanelAdapterIncompatibleError(needed=key, reason=reason, defect=outcome)
    _ADAPTERS[key] = outcome
    return outcome


def _reset_adapter_cache() -> None:
    """Test hook. Not public API."""
    global _ENTRY_POINTS  # pylint: disable=global-statement  # test hook for the cache above
    _ENTRY_POINTS = None
    _ADAPTERS.clear()
    _REJECTED.clear()
