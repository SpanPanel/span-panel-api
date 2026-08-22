"""Build ``ExtensionProperty`` records for properties on devices this adapter *does* model.

The other half of vendor extensibility from :mod:`adoption`. That module handles
a device type nothing here models; this one handles a new property on a device
something here does -- a battery vendor hanging ``battery-2/cell-temperature``
off the BESS. Until this existed the second case reached a consumer nowhere: it
became a discovery row and stopped at diagnostics, which only a maintainer
reading an attachment ever sees.

**Values, like :mod:`adoption` and unlike :mod:`field_metadata`'s discovery
rows.** The same property is described by both surfaces on purpose, joined by
its ``{node}/{property}`` path: a declaration for the maintainer, a reading for
the user. The types are separate so that conflating them is a type error rather
than a leak, and `ExtensionProperty` is deliberately not a `FieldMetadata` --
`partition()` walks the metadata map, so a value carried here has no path into a
payload that leaves the machine.

**Read-only, structurally.** No set topic is built here and `ExtensionProperty`
has no member to put one in. A settable extension property is carried with
``settable=True`` for curation triage and still surfaces as a reading, because
these properties live on exactly the devices whose curated controls do real
safety work -- the EVSE limit refuses a value above the commissioned ceiling,
and the islanding assertion translates ``GRID`` into ``ON_GRID``. A generic
write path would sit beside both, on the same wire, with neither.

**Unaddressed is asked once.** The set comes from
:func:`field_metadata.addressed_rows`, so this module and the discovery rows
cannot disagree about which properties this adapter reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api.models import ADOPTION_IDENTITY_NODE, ADOPTION_TOPOLOGY_NODE, ExtensionProperty, ExtensionSubject
from span_panel_api_schema_1.description import nodes, optional_str, properties
from span_panel_api_schema_1.field_metadata import is_addressed

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ebus_sdk.homie import DiscoveredDevice


def build_extension_properties(
    subjects: Sequence[tuple[DiscoveredDevice, ExtensionSubject]],
    addressed: set[tuple[str, str, str]],
) -> tuple[ExtensionProperty, ...]:
    """Every declared-but-unaddressed property of the modelled devices given.

    The caller supplies the pairing rather than this module deriving it, because
    the subject key for a multi-instance kind is the snapshot's own map key --
    the circuit id, the harmonised EVSE key -- and those are decided while the
    snapshot is being assembled. Deriving them a second time here would be a
    second implementation of the same decision, free to drift from the first.

    Subject resolution is therefore indifferent to proxying by construction: a
    device the snapshot builder sorted into a role arrives here already paired
    with that role, whether the tree proxied it or not. The reference tree's own
    MID arrives proxied as ``bess-mid`` and is paired with ``mid`` like any
    other.
    """
    found: list[ExtensionProperty] = []
    for device, subject in subjects:
        declared = _declared_type(device)
        if not declared:
            continue
        for node_id, node in nodes(device.description or {}).items():
            if node_id in (ADOPTION_IDENTITY_NODE, ADOPTION_TOPOLOGY_NODE):
                continue
            declarations = properties(node)
            unaddressed = {
                property_id: definition
                for property_id, definition in declarations.items()
                if not is_addressed(addressed, declared, node_id, property_id)
            }
            if not unaddressed:
                continue
            # True when the node carries at least one property this adapter does
            # read. One bit rather than the node-to-field map: a vendor
            # extending `meter` is probably extending the meter, and that is all
            # a consumer can act on. Exporting which fields would freeze this
            # adapter's internals as API for a signal the design ranks last.
            has_curated_siblings = len(unaddressed) < len(declarations)
            for property_id, definition in unaddressed.items():
                raw = device.get_property(node_id, property_id)
                found.append(
                    ExtensionProperty(
                        subject=subject,
                        node_id=node_id,
                        property_id=property_id,
                        datatype=str(definition.get("datatype") or "string"),
                        unit=optional_str(definition.get("unit")),
                        format=optional_str(definition.get("format")),
                        settable=bool(definition.get("settable", False)),
                        value=None if raw is None else str(raw),
                        node_has_curated_siblings=has_curated_siblings,
                    )
                )
    return tuple(found)


def _declared_type(device: DiscoveredDevice) -> str:
    """The device's declared ``$type``, or empty when it has not arrived yet.

    A device mid-discovery declares no type, which is a normal state rather than
    a finding: it is skipped and picked up on a later snapshot, the same way
    :func:`adoption.build_adopted_devices` skips it.
    """
    description: dict[str, object] = device.description or {}
    return str(description.get("type") or "")
