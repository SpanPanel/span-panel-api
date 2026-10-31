"""The masked device-tree export: the panel's whole tree, safe to share.

``export_masked_tree`` writes the tree in the ``tree-v1`` form of the reference
captures, so an attached export replays through the same loader, adapter and
fidelity harness they do. Every retained value is carried as the wire string
the panel published; a declared property that was never valued has no key, so
the three states of a property (valued, valued empty, never valued) stay
apart.

A masked export replaces every device id with a placeholder that starts
``masked-``, so it can never equal a real id. The placeholder follows the
device rather than the shape of its id: SPAN treats ids as opaque and has said
they can change, so circuits are numbered by the breaker space they occupy and
lugs take their direction. Exports taken before and after such a change then
compare device by device.

Values are masked by their ``node/property`` name: a serial, the site's
address, location, postal code, network and name. Every value that names a
device is passed through the same placeholders, so a reference still resolves;
one that names no device in the tree becomes ``masked-missing-<n>`` and stays
visibly dangling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from span_panel_api.models import MaskedTree, MaskedTreeDevice, UndescribedDevice
from span_panel_api_schema_1.description import device_type, nodes, properties

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
_MISSING = "missing"

_SCHEMA: Final = "tree-v1"
_NUMERIC_DATATYPES: Final = frozenset({"integer", "float"})

_SERIAL_NUMBER: Final = "info/serial-number"
_NAME: Final = "info/name"
_MASKED_PANEL_NAME: Final = "Masked Panel"
_SITE_MASKS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "info/utility-meter-serial-number": "MASKED-METER",
        "status/wifi-ssid": "masked-ssid",
        "status/postal-code": "00000",
        "info/address-lines": "Masked Address",
        "info/locality": "Masked City",
        "info/region": "Masked Region",
        "info/latitude": "0.0",
        "info/longitude": "0.0",
    }
)
"""Site values replaced whole, by name. ``country-code`` and ``time-zone`` are kept."""

_ID_PROPERTIES: Final = frozenset({"connection/feeds-device-id", "connection/fed-by-device-id"})
_GRID_FORMING_ENTITY: Final = "grid/grid-forming-entity"
_GRID: Final = "GRID"
_ID_LIST_PROPERTY: Final = "shared-with-device-ids"
_REFERENCE_KEYS: Final = ("parent", "root")
_CHILDREN: Final = "children"
_DESCRIPTION_NAME: Final = "name"

_DIGIT_RUNS = re.compile(r"([0-9]+)")

type NaturalKey = tuple[tuple[int, str | int], ...]
type Order = tuple[NaturalKey, str]


def natural_key(text: str) -> NaturalKey:
    """Sort key that compares runs of ASCII digits as integers and the rest case-folded."""
    return tuple(
        (1, int(part)) if part.isascii() and part.isdigit() else (0, part.casefold())
        for part in _DIGIT_RUNS.split(text)
        if part
    )


def _order(device_id: str) -> Order:
    """Total order on ids: natural order, with the raw id breaking a tie.

    Distinct ids can share a natural key (`g-01` and `g-1`, or ids differing only
    in case); without the tie-breaker their order is the input order, and the
    same tree would not always give the same document.
    """
    return (natural_key(device_id), device_id)


def placeholder_ids(root: DiscoveredDevice, descendants: Sequence[DiscoveredDevice]) -> dict[str, str]:
    """Every device id in the tree, mapped to its distinct ``masked-`` placeholder.

    The root is ``masked-panel``. Circuits are numbered by their lowest breaker
    space, then by natural id order for those that share a space or declare
    none (the remote CT). Lugs take ``masked-lugs-upstream`` / ``-downstream``
    from ``info/direction``; a lugs device whose direction is absent, not one of
    the two, or already taken is numbered ``masked-lugs-<n>``. Any other
    described device is numbered per short type in natural id order, and a device
    that has not described itself, or whose description is not a JSON object,
    is ``masked-undescribed-<n>``.
    """
    ids: dict[str, str] = {root.device_id: _PANEL}
    counters: dict[str, int] = {}

    def numbered(name: str) -> str:
        counters[name] = counters.get(name, 0) + 1
        return f"{_PREFIX}{name}-{counters[name]}"

    ordered = sorted(descendants, key=lambda device: _order(device.device_id))
    circuits = [device for device in ordered if _is_described(device) and device_type(device) == _TYPE_CIRCUIT]
    for device in sorted(circuits, key=_circuit_order):
        ids[device.device_id] = numbered(_CIRCUIT)

    taken: set[str] = set()
    for device in ordered:
        if not _is_described(device) or device_type(device) != _TYPE_LUGS:
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
        ids[device.device_id] = numbered(_short_type(device) if _is_described(device) else _UNDESCRIBED)
    return ids


def _is_described(device: DiscoveredDevice) -> bool:
    """Whether the device has described itself with a ``$description`` that is a JSON object.

    The SDK stores whatever JSON the topic carried. A description that parsed
    to a list or a string declares nothing, so the device is treated as one
    that has not described itself, by the placeholders and the document alike.
    """
    description: object = device.description
    return isinstance(description, dict)


def _short_type(device: DiscoveredDevice) -> str:
    declared = device_type(device).removeprefix(_TYPE_PREFIX)
    return declared or _UNTYPED


def _text(device: DiscoveredDevice, node: str, prop: str) -> str:
    value = device.properties.get(node, {}).get(prop)
    return "" if value is None else str(value).strip()


def _circuit_order(device: DiscoveredDevice) -> tuple[int, int, Order]:
    """Hosted circuits by lowest breaker space first, the rest after, ties by natural id."""
    spaces = [int(part) for part in re.findall(r"[0-9]+", _text(device, "info", "spaces"))]
    if not spaces:
        return (1, 0, _order(device.device_id))
    return (0, min(spaces), _order(device.device_id))


def export_masked_tree(
    root: DiscoveredDevice, descendants: Sequence[DiscoveredDevice], *, producer: str, tree_complete: bool
) -> MaskedTree:
    """The tree as a ``tree-v1`` document, with device ids, serials and site values masked.

    A described device goes under ``devices``: its ``$description`` with
    ``parent``, ``root`` and ``children`` mapped to placeholders, every declared
    value split by its declared ``datatype`` (``integer`` and ``float`` into
    ``numeric_properties``, the rest into ``properties``), every value its
    description does not declare in ``undeclared_properties``, its ``$target``
    values, and its ``$state`` when one arrived. A device its parent declared
    but that has not described itself, or whose description is not a JSON
    object, goes under ``undescribed_devices`` with its values unsplit, since
    nothing says what they are.

    The masks apply to a value by its ``node/property`` name wherever it is
    published, declared or not, and to targets as to values. A valued-empty
    ``""`` stays empty under every rule: it carries no identity, and replacing
    it would turn an empty reading into a value.

    Devices are written in placeholder order and each device's values in key
    order, so the same tree always gives the same document. A dangling id is
    numbered ``masked-missing-<n>`` in the order it is first met: devices in
    placeholder order, and within one its values, then its targets, then its
    description's references. The SDK's objects are only read; a description
    is copied before anything in it is replaced.
    """
    ids = placeholder_ids(root, descendants)
    masker = _Masker(ids, root.device_id)
    devices: dict[str, MaskedTreeDevice] = {}
    undescribed: dict[str, UndescribedDevice] = {}
    for device in sorted([root, *descendants], key=lambda device: _order(ids[device.device_id])):
        placeholder = ids[device.device_id]
        values = masker.values(device.device_id, _flattened(device.properties))
        targets = masker.values(device.device_id, _flattened(device.property_targets))
        description: object = device.description
        if isinstance(description, dict):  # `_is_described`, inline so the description narrows
            devices[placeholder] = _described(device, masker.description(device.device_id, description), values, targets)
        else:
            undescribed[placeholder] = _undescribed(device, values, targets)
    return {
        "metadata": {"schema": _SCHEMA, "masked": True, "producer": producer, "tree_complete": tree_complete},
        "devices": devices,
        "undescribed_devices": undescribed,
    }


class _Masker:
    """The masking rules, with the one piece of state they share: dangling ids met so far."""

    def __init__(self, ids: Mapping[str, str], panel_id: str) -> None:
        self._ids = ids
        self._panel_id = panel_id
        self._missing: dict[str, str] = {}

    def reference(self, device_id: str) -> str:
        """The placeholder of the device an id names, or the dangling id's own ``masked-missing-<n>``."""
        placeholder = self._ids.get(device_id)
        if placeholder is not None:
            return placeholder
        if device_id not in self._missing:
            self._missing[device_id] = f"{_PREFIX}{_MISSING}-{len(self._missing) + 1}"
        return self._missing[device_id]

    def values(self, owner: str, values: Mapping[str, str]) -> dict[str, str]:
        """One device's ``node/property`` values with every rule applied, in key order."""
        return {topic: self.value(owner, topic, values[topic]) for topic in sorted(values)}

    def value(self, owner: str, topic: str, value: str) -> str:
        if value == "":
            return value
        masked = self._fixed_mask(owner, topic)
        return self._referenced(topic, value) if masked is None else masked

    def _fixed_mask(self, owner: str, topic: str) -> str | None:
        """The replacement for an identifying or site value, or None where the name carries neither."""
        if topic == _SERIAL_NUMBER:
            return self._ids[owner].upper()
        if topic == _NAME and owner == self._panel_id:
            return _MASKED_PANEL_NAME
        return _SITE_MASKS.get(topic)

    def _referenced(self, topic: str, value: str) -> str:
        """The value with every device id it names mapped; a value that names none, as published."""
        if topic in _ID_PROPERTIES:
            return self.reference(value)
        if topic == _GRID_FORMING_ENTITY:
            # The grid itself, read as the adapter reads it, is not an id.
            return value if value.strip().upper() == _GRID else self.reference(value)
        if topic.partition("/")[2] == _ID_LIST_PROPERTY:
            return ",".join(self.reference(part.strip()) for part in value.split(",") if part.strip())
        return value

    def description(self, owner: str, description: Mapping[object, object]) -> dict[str, object]:
        """A copy of a ``$description`` with its device references mapped and the panel's name masked."""
        masked: dict[str, object] = {str(key): copy.deepcopy(value) for key, value in description.items()}
        for key in _REFERENCE_KEYS:
            reference = masked.get(key)
            if isinstance(reference, str):
                masked[key] = self.reference(reference)
        children = masked.get(_CHILDREN)
        if isinstance(children, list):
            masked[_CHILDREN] = [self.reference(child) if isinstance(child, str) else child for child in children]
        if owner == self._panel_id and _DESCRIPTION_NAME in masked:
            masked[_DESCRIPTION_NAME] = _MASKED_PANEL_NAME
        return masked


def _described(
    device: DiscoveredDevice, description: dict[str, object], values: dict[str, str], targets: dict[str, str]
) -> MaskedTreeDevice:
    numeric = {
        f"{node_id}/{property_id}": _is_numeric(definition)
        for node_id, node in nodes(description).items()
        for property_id, definition in properties(node).items()
    }
    entry: MaskedTreeDevice = {
        "description": description,
        "properties": {},
        "numeric_properties": {},
        "targets": targets,
        "undeclared_properties": {},
    }
    for topic, value in values.items():
        if topic not in numeric:
            entry["undeclared_properties"][topic] = value
        elif numeric[topic]:
            entry["numeric_properties"][topic] = value
        else:
            entry["properties"][topic] = value
    if device.state is not None:
        entry["state"] = str(device.state)
    return entry


def _is_numeric(definition: Mapping[str, object]) -> bool:
    """Whether a property's declared ``datatype`` puts its value in ``numeric_properties``."""
    datatype = definition.get("datatype")
    return isinstance(datatype, str) and datatype in _NUMERIC_DATATYPES


def _undescribed(device: DiscoveredDevice, values: dict[str, str], targets: dict[str, str]) -> UndescribedDevice:
    entry: UndescribedDevice = {"values": values, "targets": targets}
    if device.state is not None:
        entry["state"] = str(device.state)
    return entry


def _flattened(by_node: object) -> dict[str, str]:
    """The SDK's ``{node: {property: value}}`` map, keyed ``node/property``."""
    flat: dict[str, str] = {}
    if not isinstance(by_node, dict):
        return flat
    for node_id, by_property in by_node.items():
        if isinstance(by_property, dict):
            for property_id, value in by_property.items():
                flat[f"{node_id}/{property_id}"] = str(value)
    return flat
