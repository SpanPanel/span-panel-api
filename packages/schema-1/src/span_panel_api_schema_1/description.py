"""Narrowing readers for a Homie ``$description`` document.

Every level of a description is optional and the SDK hands it back as an
untyped mapping, so each reader has to narrow before it can index. Doing that
once here keeps the narrowing identical everywhere and keeps ``Any`` out of the
modules that read declarations — :mod:`field_metadata` for units and datatypes,
:mod:`charge_limit` for which spelling of a node a charger declares,
:mod:`circuits` for whether a control may be written.

``$settable`` is read here too, and only here: it is the one declaration
attribute that authorises a write, and three modules were deciding what its
absence means. :func:`declared_settable` gives that answer once.

These read the *declaration*, never a value. Property values come through
:mod:`panel`'s ``text`` / ``number`` / ``integer`` readers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api_schema_1.const import ATTR_SETTABLE

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice


def device_type(device: DiscoveredDevice) -> str:
    """The device's declared type from its description, or '' before it arrives.

    A device exists in the tree from the moment its parent names it as a child,
    so an empty type is the normal mid-discovery state rather than an error.
    """
    description: dict[str, object] = device.description or {}
    declared = description.get("type")
    return str(declared) if declared else ""


def nodes(description: dict[str, object]) -> dict[str, dict[str, object]]:
    """The capability nodes a description declares, by node id."""
    declared = description.get("nodes")
    if not isinstance(declared, dict):
        return {}
    return {str(key): value for key, value in declared.items() if isinstance(value, dict)}


def properties(node: dict[str, object]) -> dict[str, dict[str, object]]:
    """The properties one node declares, by property id."""
    declared = node.get("properties")
    if not isinstance(declared, dict):
        return {}
    return {str(key): value for key, value in declared.items() if isinstance(value, dict)}


def node_properties(device: DiscoveredDevice | None, node_id: str) -> dict[str, dict[str, object]]:
    """The properties one device declares on one node, or an empty mapping.

    The device-level entry point, for a caller that has a device rather than a
    parsed description. A device mid-discovery has no description at all, which
    is the normal state rather than an error, so it answers empty like a device
    that declares the node with nothing on it.
    """
    if device is None:
        return {}
    return properties(nodes(device.description or {}).get(node_id, {}))


def declared_settable(definition: dict[str, object] | None) -> bool:
    """Whether one property's declaration authorises a write to it.

    **Absence is refusal, in both of the ways a declaration can be absent.**
    Homie 5 defines ``$settable`` as defaulting to *false*, so a property
    declared without the attribute has authorised nothing; and ``None`` — the
    property, or the node carrying it, not declared at all — has not even said
    there is something to write. Neither is permission, and this is the only
    place in the adapter that decides so.

    That default is what a conforming publisher relies on. The eBus SDK builds a
    ``$description`` from ``PropertySpec``, whose ``settable`` is ``False`` by
    default and which emits the attribute *only* when it is true
    (``ebus_sdk.declaration``) — so a producer describing a locked control omits
    ``$settable`` and never publishes ``settable: false``. Reading omission as
    permission would therefore mean offering a control on precisely the devices
    commissioned not to have one.

    A string ``"true"`` counts and nothing else does, because Homie attributes
    travel as text and a publisher that serialises the description by hand may
    not re-type the booleans — while a value that is neither the boolean nor
    that word has not said what it means, and a write is not the place to guess.
    """
    if definition is None:
        return False
    settable = definition.get(ATTR_SETTABLE)
    if isinstance(settable, bool):
        return settable
    return str(settable).strip().lower() == "true"


def optional_str(value: object) -> str | None:
    """A declaration's string attribute, with empty and absent both meaning None."""
    if value is None:
        return None
    text = str(value)
    return text or None
