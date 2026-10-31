"""The masked device-tree export: which placeholder each device takes.

A masked export replaces every device id with a placeholder that starts
``masked-``, so it can never equal a real id. The placeholder follows the
device rather than the shape of its id: SPAN treats ids as opaque and has said
they can change, so circuits are numbered by the breaker space they occupy and
lugs take their direction. Exports taken before and after such a change then
compare device by device.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import TYPE_CHECKING

from span_panel_api_schema_1.description import device_type

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice

_PREFIX = "masked-"
_PANEL = f"{_PREFIX}panel"
_TYPE_PREFIX = "energy.ebus.device."
_TYPE_CIRCUIT = f"{_TYPE_PREFIX}circuit"
_TYPE_LUGS = f"{_TYPE_PREFIX}lugs"
_UNDESCRIBED = "undescribed"
_UNTYPED = "untyped"
_LUGS = "lugs"
_CIRCUIT = "circuit"
_DIRECTIONS = {"UPSTREAM": "upstream", "DOWNSTREAM": "downstream"}

_DIGIT_RUNS = re.compile(r"([0-9]+)")

type NaturalKey = tuple[tuple[int, str | int], ...]


def natural_key(text: str) -> NaturalKey:
    """Sort key that compares runs of ASCII digits as integers and the rest case-folded."""
    return tuple(
        (1, int(part)) if part.isascii() and part.isdigit() else (0, part.casefold())
        for part in _DIGIT_RUNS.split(text)
        if part
    )


def placeholder_ids(root: DiscoveredDevice, descendants: Sequence[DiscoveredDevice]) -> dict[str, str]:
    """Every device id in the tree, mapped to its distinct ``masked-`` placeholder.

    The root is ``masked-panel``. Circuits are numbered by their lowest breaker
    space, then by natural id order for those that share a space or declare
    none (the remote CT). Lugs take ``masked-lugs-upstream`` / ``-downstream``
    from ``info/direction``; a lugs device whose direction is absent, not one of
    the two, or already taken is numbered ``masked-lugs-<n>``. Any other
    described device is numbered per short type in natural id order, and a device
    that has not described itself is ``masked-undescribed-<n>``.
    """
    ids: dict[str, str] = {root.device_id: _PANEL}
    counters: dict[str, int] = {}

    def numbered(name: str) -> str:
        counters[name] = counters.get(name, 0) + 1
        return f"{_PREFIX}{name}-{counters[name]}"

    ordered = sorted(descendants, key=lambda device: natural_key(device.device_id))
    circuits = [device for device in ordered if device.description is not None and device_type(device) == _TYPE_CIRCUIT]
    for device in sorted(circuits, key=_circuit_order):
        ids[device.device_id] = numbered(_CIRCUIT)

    taken: set[str] = set()
    for device in ordered:
        if device.description is None or device_type(device) != _TYPE_LUGS:
            continue
        direction = _DIRECTIONS.get(_text(device, "info", "direction"))
        if direction is None or direction in taken:
            ids[device.device_id] = numbered(_LUGS)
        else:
            taken.add(direction)
            ids[device.device_id] = f"{_PREFIX}{_LUGS}-{direction}"

    for device in ordered:
        if device.device_id in ids:
            continue
        ids[device.device_id] = numbered(_UNDESCRIBED if device.description is None else _short_type(device))
    return ids


def _short_type(device: DiscoveredDevice) -> str:
    declared = device_type(device).removeprefix(_TYPE_PREFIX)
    return declared or _UNTYPED


def _text(device: DiscoveredDevice, node: str, prop: str) -> str:
    value = device.properties.get(node, {}).get(prop)
    return "" if value is None else str(value).strip()


def _circuit_order(device: DiscoveredDevice) -> tuple[int, int, NaturalKey]:
    """Hosted circuits by lowest breaker space first, the rest after, ties by natural id."""
    spaces = [int(part) for part in re.findall(r"[0-9]+", _text(device, "info", "spaces"))]
    if not spaces:
        return (1, 0, natural_key(device.device_id))
    return (0, min(spaces), natural_key(device.device_id))
