"""Compare what a producer declares against what a capability catalog defines.

The registry used as a validator. Every property a device's ``$description``
declares carries a ``unit`` and a ``datatype``; the eBus capability catalog for
that node declares the same two fields for the same property. Agreement is
silence. Disagreement is a finding, surfaced for a human — never resolved
silently in either direction, because either side can be the wrong one. The
last mislabel this catches by machine (`meter/active-power` in ``kW``, values in
watts) was found because a person noticed a sibling device declaring the same
quantity differently.

**This module compares; it never sources.** Nothing here may become the place a
unit is read from. `field_metadata` takes units from each device's own
declaration precisely because the catalog is the superset across all hardware
and carries abstract units, and `test_an_abstract_unit_is_never_taken_from_the_catalog`
holds that line. The rules below exist to *judge* a declaration, which is a
different job from supplying one.

**Catalog definitions are passed in, never read from disk.** The vendored
catalogs live under ``packages/schema-1/spec/``, outside this distribution's
wheel, so a module that read them by path would work in the repository and fail
everywhere else. Taking them as an argument also lets a caller judge a
declaration against a catalog it fetched, which is what a live-panel diagnostic
would do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from span_panel_api_schema_1.description import optional_str

CAPABILITY_PREFIX = "energy.ebus.capability."
"""The namespace a node's declared ``$type`` uses, and a catalog's ``capability``."""


def capability_of(declared_type: str | None) -> str | None:
    """The bare capability name behind a node's declared ``$type``.

    A node id is conventionally the capability name and every capture we hold
    agrees, but the *type* is what the specification makes authoritative — a
    publisher may name the node anything. Reading the id instead would work
    until the day one did.
    """
    if declared_type is None or not declared_type.startswith(CAPABILITY_PREFIX):
        return None
    return declared_type[len(CAPABILITY_PREFIX) :] or None


class Divergent(Enum):
    """What a finding is about.

    ``UNCATALOGUED`` is terminal and exclusive: a property no catalog defines
    has nothing to compare a unit or a datatype against, so it is reported once
    as absent rather than three times as every field disagreeing with nothing.
    The EVSE's ``config`` node is the case that makes the distinction matter —
    it is not an eBus capability at all, and reporting its two properties as
    unit mismatches would be a claim about a catalog that does not exist.
    """

    UNIT = "unit"
    DATATYPE = "datatype"
    UNCATALOGUED = "uncatalogued"


@dataclass(frozen=True)
class Declaration:
    """The two fields of a property declaration this check compares.

    Both optional, on both sides: a catalog property may carry no unit
    (``power-factor``, every enum), and so may a declaration.
    """

    unit: str | None
    datatype: str | None


def declaration(raw: dict[str, object]) -> Declaration:
    """Narrow one property definition — from a ``$description`` or a catalog.

    The same reader for both, because the two documents declare a property the
    same way. That is the whole reason this comparison is possible.
    """
    return Declaration(unit=optional_str(raw.get("unit")), datatype=optional_str(raw.get("datatype")))


@dataclass(frozen=True)
class Divergence:
    """One disagreement between a producer and a catalog.

    Identity is the whole tuple, deliberately. A divergence whose values change
    — ``kW`` becoming ``mW`` — is a different divergence, and reads as the old
    one disappearing and a new one arriving rather than as an entry that
    silently goes on covering something nobody looked at.

    Producer-independent: the same mislabel seen in three captures of one panel
    is one finding, not three. Which producers show it is recorded beside the
    acknowledgement instead, so it can be checked without multiplying entries.
    """

    capability: str
    property_id: str
    kind: Divergent
    declared: str | None
    catalogued: str | None

    def __str__(self) -> str:
        """The line a human reads in a failure, and the line they sort by.

        Sorting reports on this rather than on the tuple, because `Divergent` is
        an Enum and not orderable, and because the text is what a reader is
        scanning — a report ordered by a key that is not visible in it reads as
        unordered.
        """
        if self.kind is Divergent.UNCATALOGUED:
            return f"{self.capability}/{self.property_id}: no catalog defines it"
        return (
            f"{self.capability}/{self.property_id}: declared {self.kind.value} "
            f"{self.declared!r}, catalog says {self.catalogued!r}"
        )


UNIT_FAMILIES: dict[str, frozenset[str]] = {
    "energy": frozenset({"Wh", "kWh", "MWh", "J", "kJ", "MJ"}),
}
"""Catalog unit tokens that name a dimension rather than a unit.

``soc/soe``, ``soc/total-energy-storage``, ``soc/loadup-headroom`` and
``info/nameplate-capacity`` are all ``unit: "energy"``, and the catalog prose is
explicit about why: the quantity is "reported in the device's native energy unit
(a BESS in kWh electrical, a water heater in Wh thermal) via `$unit`". The token
is an instruction to substitute, not a unit to match — so a publisher declaring
``kWh`` there is *conforming*, and a string compare against it would report the
one thing the specification asks for as the defect.

Membership is enumerated rather than derived from an SI-prefix rule, for the
reason the whole design doc argues: a rule gets the case nobody thought about
wrong, quietly. A device declaring an energy unit outside this set is a finding
a human should see, which is what an empty match produces.

Echoing the token itself (``unit: "energy"`` on the wire) is *not* membership,
and that is the second thing this catches: a publisher that copied the
placeholder out of the catalog instead of substituting its own unit.
"""

CATALOGUED_CONCRETE_UNITS: frozenset[str] = frozenset(
    {"%", "A", "Hz", "V", "VA", "VAh", "W", "Wh", "kA", "min", "var", "varh"}
)
"""Every non-abstract unit token the vendored catalogs currently use.

Pinned so that a token arriving upstream has to be classified by a human before
it is compared: `unclassified_units` fails on anything that is in neither this
set nor `UNIT_FAMILIES`. Without it, a new abstract family — ``power``, say —
would be string-compared against every concrete unit a publisher substitutes and
report the whole family as broken, which is exactly the false finding this
module's family rule exists to prevent.

Not a list of legal *wire* units. A publisher may declare any unit it likes;
this is only the vocabulary of the reference side.
"""


def unclassified_units(catalogued: frozenset[str]) -> frozenset[str]:
    """Catalog unit tokens this module has no classification for.

    The guard on the guard: the family rule is only sound while every token it
    might meet is known to be either concrete or a dimension.
    """
    return catalogued - CATALOGUED_CONCRETE_UNITS - frozenset(UNIT_FAMILIES)


def unit_agrees(declared: str | None, catalogued: str | None) -> bool:
    """Does a declared unit satisfy the catalogued one?

    Three rules, in the order they apply:

    1. A catalog property with no unit expects a declaration with none. A unit
       appearing where the reference carries none is as much a disagreement as
       the wrong unit — it says the two sides disagree about whether the
       quantity is dimensioned at all.
    2. An abstract family is satisfied by any member of the family, and by
       nothing else — including the family token itself.
    3. Everything else is an exact match.
    """
    if catalogued is None:
        return declared is None
    members = UNIT_FAMILIES.get(catalogued)
    if members is None:
        return declared == catalogued
    return declared is not None and declared in members


def compare(capability: str, property_id: str, declared: Declaration, catalogued: Declaration | None) -> list[Divergence]:
    """Judge one declared property against its catalog definition.

    ``catalogued`` is None when no catalog defines the property — either the
    capability has no catalog at all (``config``) or the catalog does not carry
    this name (``status/wifi-ssid``). Both produce a single ``UNCATALOGUED``
    finding and stop: there is no reference to compare against, and saying so
    once is the honest report.
    """
    if catalogued is None:
        return [Divergence(capability, property_id, Divergent.UNCATALOGUED, None, None)]

    found: list[Divergence] = []
    if not unit_agrees(declared.unit, catalogued.unit):
        found.append(Divergence(capability, property_id, Divergent.UNIT, declared.unit, catalogued.unit))
    if declared.datatype != catalogued.datatype:
        found.append(Divergence(capability, property_id, Divergent.DATATYPE, declared.datatype, catalogued.datatype))
    return found
