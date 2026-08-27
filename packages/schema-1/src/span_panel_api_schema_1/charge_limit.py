"""Where an EV charger publishes its charge-current ceiling — resolved, never assumed.

This is the only *settable* surface the v1.0 catch-up reads, and it is the one
whose name we cannot look up. Two spellings exist and neither is disprovable
from here:

- The reference tree, and the simulator it came from, declare node ``config``
  with ``max-charge-current`` (the commissioned ceiling) and
  ``user-max-charge-current`` (``settable: true``).
- The eBus catalog has **no** ``config`` capability. It puts the same surface on
  ``charge-limit`` 0.1 — ``installer-max`` (the immutable ceiling) and
  ``owner-limit`` (``settable``, and specified as MUST be ``<= installer-max``).

No capture can settle it: the panel we expect access to carries no SPAN Drive,
so no EVSE will describe itself to us. Waiting is not a plan that terminates.

It does not need to. ``devices/distribution-enclosure.md`` states the rule —
"the authoritative property set for any capability node is always declared in
that device's ``$description``" — so a correct reader names no node in a
constant. It asks the charger which of the spellings it declares, reads that
one, and builds the set topic from the node and property it found. That is right
whichever spelling firmware ships, and it is the same rule
:mod:`field_metadata` already follows for units and datatypes.

The spellings are ordered catalog-first, so a charger that grows the specified
node is read through the specified node even while it still declares the older
one. Adding a third spelling is one tuple entry; nothing else in the library
mentions either name.

**Settability is read, never assumed.** The two properties of a spelling differ
by exactly one Homie attribute — the ceiling declares no ``settable``, the limit
declares ``settable: true`` — so a reader that treated an absent attribute as
permission would offer to write the installer's commissioned ceiling. That is
Homie 5's default rather than a rule this module chose, and it is the same one
:func:`circuits.priority_is_settable` reads for a shed priority; both go through
:func:`description.declared_settable`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from span_panel_api_schema_1.description import declared_settable, nodes, optional_str, properties

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice

# `energy.ebus.capability.charge-limit` 0.1, the catalogued spelling.
NODE_CHARGE_LIMIT = "charge-limit"
PROP_INSTALLER_MAX = "installer-max"
PROP_OWNER_LIMIT = "owner-limit"

# The spelling the reference tree carries. No catalog defines a `config`
# capability, so this is a SPAN extension and is declared as one in the
# conformance suite's `_SPAN_EXTENSIONS`.
NODE_CONFIG = "config"
PROP_MAX_CHARGE_CURRENT = "max-charge-current"
PROP_USER_MAX_CHARGE_CURRENT = "user-max-charge-current"


@dataclass(frozen=True, slots=True)
class ChargeLimitSpelling:
    """One node/property naming of the charge-current ceiling surface."""

    node: str
    ceiling: str
    limit: str


SPELLINGS: tuple[ChargeLimitSpelling, ...] = (
    ChargeLimitSpelling(node=NODE_CHARGE_LIMIT, ceiling=PROP_INSTALLER_MAX, limit=PROP_OWNER_LIMIT),
    ChargeLimitSpelling(node=NODE_CONFIG, ceiling=PROP_MAX_CHARGE_CURRENT, limit=PROP_USER_MAX_CHARGE_CURRENT),
)
"""Every naming this adapter recognises, most-specified first.

Public because it *is* the adapter's read set for this surface, and the
conformance suite derives that set from here rather than from a second list —
the same reason `_read_pairs` walks the source instead of restating the
mappings.
"""


@dataclass(frozen=True, slots=True)
class ChargeLimitProperty:
    """One declared property of the resolved surface.

    Carries the declaration's own unit and datatype so a caller never has to go
    back to the ``$description`` for them: the value, its metadata and its set
    topic are then all derived from one resolution and cannot disagree about
    which property they describe. `_lugs_metadata` splits for the same reason.
    """

    property_id: str
    unit: str | None
    datatype: str
    settable: bool


@dataclass(frozen=True, slots=True)
class ChargeLimitSurface:
    """The charge-limit node one charger declares, and what is on it.

    Both members are optional because the catalog makes both optional: the
    ceiling is SHOULD and the limit is MAY. A charger may publish a ceiling it
    does not let anyone lower, and the reverse is legal too. Callers ask for the
    half they need rather than being handed a surface that claims both exist.
    """

    node: str
    ceiling: ChargeLimitProperty | None
    limit: ChargeLimitProperty | None


def resolve_charge_limit(device: DiscoveredDevice | None) -> ChargeLimitSurface | None:
    """The charge-limit surface this charger declares, or None if it declares none.

    None is the honest answer for a charger with no adjustable ceiling —
    ``charge-limit.md``'s absence semantics say exactly that: "absence of the
    ``charge-limit`` node means the EVSE has no adjustable charge-current
    ceiling (it charges at a fixed rate)".
    """
    if device is None:
        return None
    declared = nodes(device.description or {})
    for spelling in SPELLINGS:
        node = declared.get(spelling.node)
        if node is None:
            continue
        declarations = properties(node)
        # A declared node carrying neither property names nothing we can read,
        # and falling through to the next spelling is what lets a charger
        # declare an unrelated `config` node without hiding a `charge-limit` one.
        if spelling.ceiling not in declarations and spelling.limit not in declarations:
            continue
        return ChargeLimitSurface(
            node=spelling.node,
            ceiling=_property(spelling.ceiling, declarations.get(spelling.ceiling)),
            limit=_property(spelling.limit, declarations.get(spelling.limit)),
        )
    return None


def _property(property_id: str, definition: dict[str, object] | None) -> ChargeLimitProperty | None:
    if definition is None:
        return None
    return ChargeLimitProperty(
        property_id=property_id,
        unit=optional_str(definition.get("unit")),
        datatype=str(definition.get("datatype") or "string"),
        settable=declared_settable(definition),
    )
