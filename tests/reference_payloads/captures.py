"""The reference captures, and the replay that turns each into a snapshot.

`tests/fixtures/captures/` holds masked captures of real panels on firmware
`spanos3/r202639/03`, vendored byte for byte from an upstream commit and pinned
by SHA-256 (see that directory's README). Unlike the payloads `schema_one` reads,
they are not package data: no emitter release produces them, so there is
nothing to regenerate them from, and the pin is what keeps the copies honest.

A capture is in the emitter's `tree-v1` form: each device's parsed
`$description`, and its retained values split into `properties` and
`numeric_properties`. Both halves are the wire strings the panel published, in
its own literal form, so `CaptureTree.retained` merges them back into the
`RetainedTopicTree` a broker holds and `replay` feeds that to the adapter like
any other tree. Nothing here interprets a value.

Tests over the captures are properties: they iterate `CAPTURES`, select what to
check by what each tree declares (the selectors below), and read the values they
expect from the same `CaptureTree` they replay. No capture name, device id or
value belongs in a test.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Final

from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1 import SchemaOneAdapter

from .schema_one import RetainedTopicTree, replay

CAPTURES_DIR: Final = Path(__file__).resolve().parent.parent / "fixtures" / "captures"
"""Where the vendored captures, their `SHA256SUMS` and their provenance live."""

_SUFFIX: Final = "-tree-v1.json"

CAPTURES: Final[tuple[str, ...]] = tuple(
    sorted(path.name.removesuffix(_SUFFIX) for path in CAPTURES_DIR.glob(f"*{_SUFFIX}"))
)
"""Every capture's stem, sorted: the parametrize list of each property."""

_TYPE_PANEL: Final = "energy.ebus.device.distribution-enclosure"
_TYPE_CIRCUIT: Final = "energy.ebus.device.circuit"
_TYPE_BESS: Final = "energy.ebus.device.bess"

_SPACES: Final = "info/spaces"
_FEEDS_DEVICE_ID: Final = "connection/feeds-device-id"
_METER_SHARED_WITH: Final = "meter/shared-with-device-ids"


@dataclass(frozen=True)
class CapturedDevice:
    """One device of a capture: what its description declares and what it retained.

    `declared` holds every `node/property` the description declares, valued or
    not; `values` holds the retained values, keyed the same way. A property in
    `declared` and absent from `values` was declared and never published.
    """

    device_id: str
    device_type: str
    description: str
    declared: frozenset[str]
    values: Mapping[str, str]


@dataclass(frozen=True)
class CaptureTree:
    """A parsed capture: its devices in the capture's order, and its panel."""

    devices: Mapping[str, CapturedDevice]
    root_id: str

    def device_type(self, device_id: str) -> str:
        return self.devices[device_id].device_type

    def circuits(self) -> tuple[str, ...]:
        """Every circuit-typed device, hosted in a breaker space or not."""
        return tuple(device_id for device_id, device in self.devices.items() if device.device_type == _TYPE_CIRCUIT)

    def declares(self, device_id: str, topic: str) -> bool:
        """Whether the device's description declares `node/property`."""
        return topic in self.devices[device_id].declared

    def value(self, device_id: str, topic: str) -> str | None:
        """The retained `node/property` value as the wire carries it, or None if unpublished."""
        return self.devices[device_id].values.get(topic)

    def number(self, device_id: str, topic: str) -> float | None:
        """The retained value read as a number, or None if unpublished."""
        value = self.value(device_id, topic)
        return None if value is None else float(value)

    def retained(self, *, drop_values_of: str | None = None) -> RetainedTopicTree:
        """The tree as a broker retains it, optionally withholding one device's values.

        A withheld device keeps its description, so it is declared and unvalued:
        what the adapter sees between a device describing itself and its values
        arriving.
        """
        if drop_values_of is not None and drop_values_of not in self.devices:
            raise KeyError(f"no device {drop_values_of!r} to withhold")
        return {
            device_id: {
                "$description": device.description,
                **({} if device_id == drop_values_of else device.values),
            }
            for device_id, device in self.devices.items()
        }


def capture_path(stem: str) -> Path:
    return CAPTURES_DIR / f"{stem}{_SUFFIX}"


_TREES: Final[dict[str, CaptureTree]] = {}


def capture_tree(stem: str) -> CaptureTree:
    """Parse one capture. Cached and read-only, so every test reads the same tree."""
    if stem not in _TREES:
        _TREES[stem] = _parse(capture_path(stem))
    return _TREES[stem]


def _parse(path: Path) -> CaptureTree:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    document = _json_object(raw, path.name)
    devices = {
        device_id: _device(device_id, body, f"{path.name}: {device_id}")
        for device_id, body in _json_object(document["devices"], f"{path.name}: devices").items()
    }
    roots = [device_id for device_id, device in devices.items() if device.device_type == _TYPE_PANEL]
    if len(roots) != 1:
        raise ValueError(f"{path.name} holds {len(roots)} panels, not one")
    return CaptureTree(devices=MappingProxyType(devices), root_id=roots[0])


def capture_adapter(stem: str, *, drop_values_of: str | None = None) -> SchemaOneAdapter:
    """A fresh adapter that has received the whole capture, the way a broker replays it."""
    tree = capture_tree(stem)
    return replay(tree.retained(drop_values_of=drop_values_of), tree.root_id)


def capture_snapshot(stem: str, *, drop_values_of: str | None = None) -> SpanPanelSnapshot:
    """The snapshot the adapter builds from the replayed capture."""
    return capture_adapter(stem, drop_values_of=drop_values_of).build_snapshot()


def hosted_circuits(tree: CaptureTree) -> tuple[str, ...]:
    """Circuits whose description declares the breaker spaces they occupy."""
    return tuple(circuit_id for circuit_id in tree.circuits() if tree.declares(circuit_id, _SPACES))


def space_less_meters(tree: CaptureTree) -> tuple[str, ...]:
    """Circuit-typed devices whose description declares no breaker space."""
    return tuple(circuit_id for circuit_id in tree.circuits() if not tree.declares(circuit_id, _SPACES))


def circuit_fed_batteries(tree: CaptureTree) -> Mapping[str, str]:
    """Battery id -> the circuit whose `connection/feeds-device-id` names it.

    A battery named by two circuits raises: no capture has that shape, and a
    property written for one feeding circuit would silently check only one.
    """
    fed: dict[str, str] = {}
    for circuit_id in tree.circuits():
        target = tree.value(circuit_id, _FEEDS_DEVICE_ID)
        if target is not None and target in tree.devices and tree.device_type(target) == _TYPE_BESS:
            if target in fed:
                raise ValueError(f"battery {target!r} is fed by both {fed[target]!r} and {circuit_id!r}")
            fed[target] = circuit_id
    return fed


def shared_meter_peers(tree: CaptureTree) -> Mapping[str, tuple[str, ...]]:
    """Circuit id -> the ids its `meter/shared-with-device-ids` value lists, as published.

    Read from the wire value alone, split on commas as the eBus meter catalog
    defines the property, so a property can compare the adapter's grouping with
    what each circuit declared. Circuits that publish no value, or an empty one,
    are absent.
    """
    peers: dict[str, tuple[str, ...]] = {}
    for circuit_id in tree.circuits():
        listed = tuple(
            part.strip() for part in (tree.value(circuit_id, _METER_SHARED_WITH) or "").split(",") if part.strip()
        )
        if listed:
            peers[circuit_id] = listed
    return peers


def _device(device_id: str, body: object, where: str) -> CapturedDevice:
    fields = _json_object(body, where)
    description = _json_object(fields["description"], f"{where}: description")
    device_type = description["type"]
    if not isinstance(device_type, str):
        raise TypeError(f"{where}: description type is not a string")
    declared = frozenset(
        f"{node_id}/{property_id}"
        for node_id, node in _json_object(description["nodes"], f"{where}: nodes").items()
        for property_id in _json_object(_json_object(node, f"{where}: {node_id}")["properties"], f"{where}: {node_id}")
    )
    properties = _json_strings(fields["properties"], f"{where}: properties")
    numeric = _json_strings(fields["numeric_properties"], f"{where}: numeric_properties")
    if properties.keys() & numeric.keys():
        raise ValueError(f"{where} retains {sorted(properties.keys() & numeric.keys())} twice")
    values = {**properties, **numeric}
    undeclared = sorted(set(values) - declared)
    if undeclared:
        raise ValueError(f"{where} retains values it does not declare: {undeclared}")
    return CapturedDevice(
        device_id=device_id,
        device_type=device_type,
        description=json.dumps(description),
        declared=declared,
        values=MappingProxyType(values),
    )


def _json_object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{where} is not a JSON object")
    entries: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{where} has a non-string key")
        entries[key] = item
    return entries


def _json_strings(value: object, where: str) -> dict[str, str]:
    strings: dict[str, str] = {}
    for key, item in _json_object(value, where).items():
        if not isinstance(item, str):
            raise TypeError(f"{where}: {key} is not a string")
        strings[key] = item
    return strings
