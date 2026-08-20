"""Narrowing readers for a Homie ``$description`` document.

Every level of a description is optional and the SDK hands it back as an
untyped mapping, so each reader has to narrow before it can index. Doing that
once here keeps the narrowing identical everywhere and keeps ``Any`` out of the
modules that read declarations — :mod:`field_metadata` for units and datatypes,
:mod:`charge_limit` for which spelling of a node a charger declares.

These read the *declaration*, never a value. Property values come through
:mod:`panel`'s ``text`` / ``number`` / ``integer`` readers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice


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


def optional_str(value: object) -> str | None:
    """A declaration's string attribute, with empty and absent both meaning None."""
    if value is None:
        return None
    text = str(value)
    return text or None
