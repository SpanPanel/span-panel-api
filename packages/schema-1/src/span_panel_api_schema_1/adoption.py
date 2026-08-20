"""Build ``AdoptedDevice`` records for tree devices this adapter models no fields for.

The unit of adoption is a **device**, never a property. A new property on a
device this adapter already models is a curation task with a short turnaround,
and minting something for it automatically spends a consumer's entity identity
permanently on a shape a human would likely have chosen differently. A device
type nothing here models is the opposite case: no curation is coming for it, so
surfacing what it publishes is strictly better than the silence that ships today.

The schema is explicitly vendor-extensible, so an unmodelled type is an expected
arrival rather than a hypothetical one.

**Values, unlike :mod:`field_metadata`'s discovery rows.** Those rows exist to be
forwarded in consumer diagnostics, which leave the machine, so they carry
declarations only. These records exist to become entities on the machine that
built them, so they carry the reading. The two must not be conflated, and the
types are separate so that conflating them is a type error rather than a leak.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api.models import ADOPTION_IDENTITY_NODE, ADOPTION_TOPOLOGY_NODE, AdoptedDevice, AdoptedProperty
from span_panel_api_schema_1.const import (
    HOMIE_DOMAIN,
    HOMIE_VERSION,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_EVSE,
    TYPE_INVERTER,
    TYPE_LUGS,
    TYPE_MID,
    TYPE_PANEL,
    TYPE_PV,
)
from span_panel_api_schema_1.description import device_type, nodes, optional_str, properties

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice

MODELLED_TYPES: tuple[str, ...] = (
    TYPE_PANEL,
    TYPE_CIRCUIT,
    TYPE_LUGS,
    TYPE_EVSE,
    TYPE_BESS,
    TYPE_PV,
    TYPE_MID,
    TYPE_INVERTER,
)
"""Every device type this adapter builds snapshot fields from.

Stated once here and asserted against the snapshot builder by test, rather than
derived from it: `TreeRoles` sorts by a chain of comparisons that no expression
can read back, and a type silently dropping out of that chain while staying in
this tuple would make a device invisible to *both* paths -- unmodelled by the
builder and unadopted by this module. The test is what closes that.
"""

PROP_VENDOR_NAME = "vendor-name"
PROP_MODEL = "model"
PROP_SERIAL_NUMBER = "serial-number"
PROP_FIRMWARE_VERSION = "firmware-version"
PROP_HARDWARE_VERSION = "hardware-version"


def is_modelled(declared: str) -> bool:
    """Whether this adapter builds snapshot fields from a device of this type.

    Subtype-aware, because firmware may declare either a base type or a subtype
    of it -- ``…device.lugs`` with a ``direction`` property, or
    ``…device.lugs.upstream``. A subtype of something modelled is modelled: the
    snapshot builder matches lugs by prefix for exactly this reason, and a
    subtype arriving must not be adopted behind the builder's back.
    """
    return any(declared == known or declared.startswith(f"{known}.") for known in MODELLED_TYPES)


def build_adopted_devices(children: list[DiscoveredDevice]) -> tuple[AdoptedDevice, ...]:
    """Adopt every child whose declared type this adapter models nothing for.

    A device mid-discovery declares no type at all, which is a normal state
    rather than an unmodelled device: it is skipped rather than adopted, and
    adopted on a later snapshot once its description arrives.

    Extra instances of a modelled type are deliberately *not* adopted. A second
    BESS is a multiplicity limitation of the snapshot model, not an unmodelled
    device, and adopting it would stand a machine-named device card beside a
    curated one describing the same class of hardware.
    """
    adopted: list[AdoptedDevice] = []
    for device in children:
        declared = device_type(device)
        if not declared or is_modelled(declared):
            continue
        adopted.append(_adopt(device, declared))
    return tuple(adopted)


def _adopt(device: DiscoveredDevice, declared: str) -> AdoptedDevice:
    """One device's identity, from ``info``, and its readings, from everything else."""
    description: dict[str, object] = device.description or {}
    declared_nodes = nodes(description)
    identity = properties(declared_nodes.get(ADOPTION_IDENTITY_NODE, {}))

    def card(property_id: str) -> str | None:
        """An ``info`` property's value, for the device card rather than an entity."""
        if property_id not in identity:
            return None
        return optional_str(device.get_property(ADOPTION_IDENTITY_NODE, property_id))

    return AdoptedDevice(
        device_id=device.device_id,
        device_type=declared,
        name=optional_str(description.get("name")),
        vendor_name=card(PROP_VENDOR_NAME),
        model=card(PROP_MODEL),
        serial_number=card(PROP_SERIAL_NUMBER),
        software_version=card(PROP_FIRMWARE_VERSION),
        hardware_version=card(PROP_HARDWARE_VERSION),
        properties=_readings(device, declared_nodes),
    )


def _readings(device: DiscoveredDevice, declared_nodes: dict[str, dict[str, object]]) -> tuple[AdoptedProperty, ...]:
    """Every declared property outside the identity and topology nodes.

    Those two are excluded by *node*, which is what the eBus vocabulary defines,
    rather than by property name. The catalogs carry no marker for "this string
    is a device reference", so a name list is the only alternative -- and a name
    list goes stale silently, as `ebus-sdk`'s own ``topology.py`` demonstrates by
    covering two device-reference properties and omitting a third that lives on
    a different capability.
    """
    readings: list[AdoptedProperty] = []
    for node_id, node in declared_nodes.items():
        if node_id in (ADOPTION_IDENTITY_NODE, ADOPTION_TOPOLOGY_NODE):
            continue
        for property_id, definition in properties(node).items():
            raw = device.get_property(node_id, property_id)
            settable = bool(definition.get("settable", False))
            readings.append(
                AdoptedProperty(
                    node_id=node_id,
                    property_id=property_id,
                    datatype=str(definition.get("datatype") or "string"),
                    unit=optional_str(definition.get("unit")),
                    format=optional_str(definition.get("format")),
                    settable=settable,
                    value=None if raw is None else str(raw),
                    set_topic=_set_topic(device.device_id, node_id, property_id) if settable else None,
                )
            )
    return tuple(readings)


def _set_topic(device_id: str, node_id: str, property_id: str) -> str:
    """The Homie topic a write to one property is published to.

    The same three-part construction the adapter uses for every curated control,
    repeated here rather than reached for, because that is the point: this
    function is only ever called on a device `is_modelled` rejected and only for
    a property the device declares settable, so no topic it can produce names
    anything a curated setter owns. Sharing the adapter's builder would put the
    whole address space one argument away.
    """
    return f"{HOMIE_DOMAIN}/{HOMIE_VERSION}/{device_id}/{node_id}/{property_id}/set"
