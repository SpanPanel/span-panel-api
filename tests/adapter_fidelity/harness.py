"""Replay a captured device tree through the schema-1 adapter and account for every property.

The question is whether `SchemaOneAdapter` turns what a panel publishes into the
snapshot faithfully: every declared property reaches the snapshot or is dropped by
code that says why, every value it carries is the wire value under the field's
documented transform, and no value appears that nothing on the wire produced.

**Method.**

1. *Replay.* A ``tree-v1`` capture (``{"devices": {id: {"description",
   "properties", "numeric_properties"}}}``) is fed to the real adapter as retained
   messages, the path `SpanMqttClient.get_snapshot` takes: the root first, then
   breadth-first by declared children, each device's ``$description``, then
   ``$state ready``, then its values.
2. *Flatten.* The snapshot, the command targets and the label wait are flattened to
   ``leaf path -> value``.
3. *Perturb.* Every declared property, valued or not, is published with values
   that differ from the captured one, chosen from its declared datatype, and the
   leaves that move are its effect. A valued property is perturbed on one session
   and restored by republishing its captured value, as a broker update would; an
   unvalued one is perturbed on a replay of its own, since nothing on the wire
   returns a property to unvalued. The session must end equal to the baseline.
4. *Classify* each declared property instance by its effect into a `Fate`.
5. *Check values.* Every leaf a valued property moves must equal the wire value
   under the transform `CellRules` documents for it.
6. *Audit leaves.* Every non-empty leaf that no valued property moves must be
   a derivation `CellRules` documents; anything else is fabricated.

Shape-generic: nothing here names a panel model or a capture. What is right for a
given cell lives in its `CellRules`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
import json
import math
from pathlib import Path
import re
from typing import Final

from reference_payloads.schema_one import RetainedTopicTree, devices_from_tree
from span_panel_api.models import (
    ADOPTION_IDENTITY_NODE,
    ADOPTION_TOPOLOGY_NODE,
    AdoptedDevice,
    ControlTarget,
    ExtensionProperty,
    V2HomieSchema,
)
from span_panel_api_schema_1.adapter import SchemaOneAdapter
from span_panel_api_schema_1.const import (
    DEVICE_TYPE_PREFIX,
    HOMIE_DOMAIN,
    HOMIE_VERSION,
    NODE_GRID,
    PROP_GRID_FORMING_ENTITY,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_LUGS,
    TYPE_MID,
)
from span_panel_api_schema_1.field_metadata import _CONSUMED_OFF_SNAPSHOT, addressed_rows, is_addressed
from span_panel_api_schema_1.firmware import release_build

ANY_LEAF: Final = "*"
"""A `CellRules.transforms` family that stands for every leaf a property moves."""


class Fate(StrEnum):
    """What became of one declared property."""

    MAPPED = "mapped"
    EXTENSION = "extension"
    ADOPTED = "adopted"
    CONTROL = "control/readiness only"
    INTENTIONAL = "intentionally dropped"
    LATENT = "latent"
    """Declared and never valued here; the library addresses it, so it would be read."""
    INERT = "inert"
    """Declared and never valued here; nothing reads it."""
    SILENT = "silently dropped"
    """Valued, with no effect anywhere, and no code that says why."""


_REACHES: Final = (Fate.MAPPED, Fate.EXTENSION, Fate.ADOPTED, Fate.CONTROL)
_FATE_ORDER: Final = {fate: index for index, fate in enumerate(Fate)}


@dataclass(frozen=True, slots=True, order=True)
class RowKey:
    """One row of the coverage matrix: a property on a device role."""

    role: str  # device role, e.g. "circuit", "bess", "panel", "lugs-upstream"
    node_property: str  # e.g. "meter/active-power"


class TransformKind(StrEnum):
    """How a snapshot leaf follows from the wire value that moves it."""

    IDENTITY = "identity"  # the number the wire carries
    NEGATE = "negate"  # its negation, with no -0.0
    INT = "int"  # the number as an int
    LITERAL = "literal"  # the string exactly as published
    BOOL = "bool"  # True exactly when the wire says "true"
    DERIVED = "derived"  # a documented derivation, not a one-to-one transform


@dataclass(frozen=True, slots=True)
class Transform:
    """The documented transform from one wire property to one snapshot leaf family."""

    kind: TransformKind
    cite: str
    earlier: tuple[int, TransformKind] | None = None
    """``(build, kind)``: before release build ``build`` the wire carries ``kind`` instead."""

    def kind_for(self, firmware: str) -> TransformKind:
        """The transform for a panel on ``firmware``.

        Where the transform changed at a release build and the firmware string
        names none, the library decides by comparing signs instead, so the value
        is then a derivation rather than a fixed transform.
        """
        if self.earlier is None:
            return self.kind
        build = release_build(firmware)
        if build is None:
            return TransformKind.DERIVED
        first_build, before = self.earlier
        return self.kind if build >= first_build else before


@dataclass(frozen=True, slots=True)
class Derivation:
    """A leaf that may hold a value no valued wire property moves on its own."""

    family: str
    cite: str
    values: tuple[str | bool, ...] | None = None
    """The values the derivation can produce; ``None`` for any."""
    inputs: tuple[str, ...] = ()
    """``node/property`` on the leaf's own device that must all be valued.

    For a value two valued properties decide together, where perturbing either
    one alone cannot move it.
    """


class DropReason(StrEnum):
    """Why the library reads a declared property into nothing, by design."""

    OFF_SNAPSHOT = "read by a route no snapshot field shows"
    IDENTITY_OR_TOPOLOGY = "an identity or topology property, never a reading"
    EXTRA_INSTANCE = "a further instance of a device the snapshot holds one of"


@dataclass(frozen=True, slots=True)
class CellRules:
    """What is right for a cell, each entry citing the code that makes it so."""

    transforms: Mapping[RowKey, Mapping[str, Transform]]
    """Row -> leaf family (or `ANY_LEAF`) -> the transform from the wire value."""
    derivations: tuple[Derivation, ...]
    drops: Mapping[DropReason, str]
    """The code behind each kind of intentional drop."""
    deferred: Mapping[RowKey, str]
    """Valued rows the library drops today, each with the reason it is deferred."""

    @property
    def justifications(self) -> dict[str, str]:
        """Every citation and reason, keyed by what it justifies."""
        cited: dict[str, str] = {}
        for key, by_family in self.transforms.items():
            for family_name, transform in by_family.items():
                cited[f"transform {key.role} {key.node_property} -> {family_name}"] = transform.cite
        for derivation in self.derivations:
            cited[f"derivation {derivation.family}"] = derivation.cite
        for reason, cite in self.drops.items():
            cited[f"drop: {reason}"] = cite
        for key, reason_text in self.deferred.items():
            cited[f"deferred {key.role} {key.node_property}"] = reason_text
        return cited

    def transform_for(self, key: RowKey, family_name: str) -> Transform | None:
        by_family = self.transforms.get(key, {})
        return by_family.get(family_name) or by_family.get(ANY_LEAF)

    def derivation_for(self, family_name: str, value: object, device_values: Mapping[str, str] | None) -> Derivation | None:
        for derivation in self.derivations:
            if derivation.family != family_name:
                continue
            if derivation.values is not None and not any(_same(value, allowed) for allowed in derivation.values):
                continue
            if derivation.inputs and (
                device_values is None or any(device_values.get(prop) is None for prop in derivation.inputs)
            ):
                continue
            return derivation
        return None


@dataclass(frozen=True, slots=True)
class FidelityReport:
    """One capture measured against its `CellRules`."""

    rows: Mapping[RowKey, tuple[Fate, ...]]
    silently_dropped: tuple[RowKey, ...]
    deferred: tuple[RowKey, ...]
    """Rows that would be silently dropped, held back by a reason in `CellRules.deferred`."""
    fabricated: tuple[str, ...]  # snapshot leaf paths
    mismatched: tuple[str, ...]  # snapshot leaf paths whose value breaks the transform
    declared_property_instances: int
    instance_fates: Mapping[Fate, int]
    leaf_rows: Mapping[str, frozenset[RowKey]]
    """Each fabricated or mismatched leaf -> the rows that own it."""
    notes: Mapping[str, str]
    """Each fabricated or mismatched leaf -> what is wrong with it."""
    derivations_used: frozenset[str]
    """Families of the `CellRules.derivations` that accounted for a leaf here."""
    grid_forming_names: Mapping[str, str | None]
    """Device id -> the name the snapshot shows while the MID names that device as forming the grid."""

    def failing_rows(self) -> frozenset[RowKey]:
        """The silently dropped rows and the rows owning a fabricated or mismatched leaf."""
        owned = (key for leaf in (*self.fabricated, *self.mismatched) for key in self.leaf_rows.get(leaf, frozenset()))
        return frozenset((*self.silently_dropped, *owned))

    def unowned_leaves(self) -> frozenset[str]:
        """Fabricated or mismatched leaves no wire row owns (a synthesised entry, a constant field).

        `failing_rows()` cannot see them, so a gate compares them separately.
        """
        return frozenset(leaf for leaf in (*self.fabricated, *self.mismatched) if not self.leaf_rows.get(leaf))


# --- the capture -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Device:
    device_id: str
    device_type: str
    description: Mapping[str, object]
    values: Mapping[str, str]  # "node/property" -> raw wire value
    declared: Mapping[str, Mapping[str, object]]  # "node/property" -> declaration
    children: tuple[str, ...]
    parent: str


@dataclass(frozen=True, slots=True)
class _Capture:
    devices: Mapping[str, _Device]
    root_id: str
    order: tuple[str, ...]
    """The root, then breadth-first by declared children, then any device no parent names."""

    @property
    def firmware(self) -> str:
        return self.devices[self.root_id].values.get("info/firmware-version", "")

    def retained(self) -> RetainedTopicTree:
        return {
            device_id: {"$description": json.dumps(device.description), "$state": "ready", **device.values}
            for device_id, device in self.devices.items()
        }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _load(tree_path: Path) -> _Capture:
    tree = _mapping(json.loads(tree_path.read_text(encoding="utf-8")))
    devices: dict[str, _Device] = {}
    for device_id, entry in _mapping(tree.get("devices")).items():
        record = _mapping(entry)
        description = _mapping(record.get("description"))
        values: dict[str, str] = {}
        for bucket in ("properties", "numeric_properties"):
            for topic, raw in _mapping(record.get(bucket)).items():
                if not isinstance(raw, str):
                    raise TypeError(f"{tree_path.name}: {device_id}/{topic} is not a wire string: {raw!r}")
                values[topic] = raw
        declared = {
            f"{node_id}/{property_id}": _mapping(declaration)
            for node_id, node in _mapping(description.get("nodes")).items()
            for property_id, declaration in _mapping(_mapping(node).get("properties")).items()
        }
        children = description.get("children")
        devices[device_id] = _Device(
            device_id=device_id,
            device_type=_text(description.get("type")),
            description=description,
            values=values,
            declared=declared,
            children=tuple(_text(child) for child in children) if isinstance(children, list) else (),
            parent=_text(description.get("parent")),
        )
    roots = [device_id for device_id, device in devices.items() if not device.parent]
    if len(roots) != 1:
        raise ValueError(f"{tree_path.name}: expected one root device, found {roots}")
    order: list[str] = []
    queue = [roots[0]]
    while queue:
        device_id = queue.pop(0)
        if device_id in order or device_id not in devices:
            continue
        order.append(device_id)
        queue.extend(devices[device_id].children)
    order.extend(sorted(device_id for device_id in devices if device_id not in order))
    return _Capture(devices=devices, root_id=roots[0], order=tuple(order))


# --- replay and flatten ----------------------------------------------------


def _frozen_clock() -> float:
    """The feed-grace clock, held still so every replay sees the same label wait."""
    return 1000.0


def _replay(capture: _Capture) -> SchemaOneAdapter:
    """A fresh adapter fed the capture as retained messages."""
    schema = V2HomieSchema(
        firmware_version=capture.firmware,
        types_schema_hash="sha256:capture",
        types={},
        data_model_version="1.0",
    )
    adapter = SchemaOneAdapter(capture.root_id, schema, clock=_frozen_clock)
    for device_id in capture.order:
        device = capture.devices[device_id]
        prefix = f"{HOMIE_DOMAIN}/{HOMIE_VERSION}/{device_id}"
        adapter.handle_message(f"{prefix}/$description", json.dumps(device.description))
        adapter.handle_message(f"{prefix}/$state", "ready")
        for topic in sorted(device.values):
            adapter.handle_message(f"{prefix}/{topic}", device.values[topic])
    return adapter


_DEVICE_MAPS: Final = {"circuits": "circuit", "pv_inverters": "pv_inverter", "evse": "evse"}
"""Snapshot fields that map a key to one device's snapshot, and the leaf family each device takes."""
_DEVICE_FIELDS: Final = frozenset({"battery", "pv", "mid", "pcs"})
_INDEXED = re.compile(r"^(?P<container>[a-z_]+)\[(?P<instance>[^\]]*)\]\.(?P<field>.+)$")
_INSTANCE = re.compile(r"^[a-z_.]+\[([^\]]*)\]")


def _plain(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _record(leaves: dict[str, object], prefix: str, item: object) -> None:
    if item is None:
        leaves[prefix] = None
        return
    if not is_dataclass(item) or isinstance(item, type):
        raise TypeError(f"{prefix} is not a device snapshot: {item!r}")
    for item_field in fields(item):
        leaves[f"{prefix}.{item_field.name}"] = _plain(getattr(item, item_field.name))


def _topic(target: ControlTarget | None) -> str | None:
    return None if target is None else target.topic


def _extension_leaves(leaves: dict[str, object], properties: Iterable[ExtensionProperty]) -> None:
    for prop in properties:
        key = f"ext[{prop.subject.kind}|{prop.subject.instance_key or ''}|{prop.node_id}/{prop.property_id}]"
        leaves[key] = prop.value


def _adopted_leaves(leaves: dict[str, object], devices: Iterable[AdoptedDevice]) -> None:
    for device in devices:
        base = f"adopted[{device.device_id}]"
        for device_field in fields(device):
            if device_field.name != "properties":
                leaves[f"{base}.{device_field.name}"] = _plain(getattr(device, device_field.name))
        for prop in device.properties:
            leaves[f"{base}.prop[{prop.node_id}/{prop.property_id}]"] = prop.value


def _flatten(adapter: SchemaOneAdapter) -> dict[str, object]:
    """``leaf path -> value`` for the snapshot, the command targets and the label wait."""
    leaves: dict[str, object] = {
        "ready.is_ready": adapter.is_ready(),
        "ready.missing_names": tuple(sorted(adapter.circuit_nodes_missing_names())),
    }
    if not adapter.is_ready():
        return leaves
    snapshot = adapter.build_snapshot()
    for snapshot_field in fields(snapshot):
        name = snapshot_field.name
        value: object = getattr(snapshot, name)
        if name == "extension_properties":
            _extension_leaves(leaves, snapshot.extension_properties)
        elif name == "adopted_devices":
            _adopted_leaves(leaves, snapshot.adopted_devices)
        elif name in _DEVICE_MAPS:
            if not isinstance(value, Mapping):
                raise TypeError(f"snapshot field {name!r} is not a map")
            for key, item in value.items():
                _record(leaves, f"{name}[{key}]", item)
        elif name in _DEVICE_FIELDS:
            _record(leaves, name, value)
        elif is_dataclass(value) or isinstance(value, Mapping | list | tuple):
            raise TypeError(f"snapshot field {name!r} holds a structure this harness does not flatten")
        else:
            leaves[f"panel.{name}"] = value
    for circuit_id in snapshot.circuits:
        leaves[f"ctl.relay_target[{circuit_id}]"] = _topic(adapter.set_circuit_relay_target(circuit_id))
        leaves[f"ctl.priority_target[{circuit_id}]"] = _topic(adapter.set_circuit_priority_target(circuit_id))
    for evse_key in snapshot.evse:
        leaves[f"ctl.evse_limit_target[{evse_key}]"] = _topic(adapter.set_evse_charge_limit_target(evse_key))
    leaves["ctl.dps_target"] = _topic(adapter.set_dominant_power_source_target())
    return leaves


def family(leaf: str) -> str:
    """A leaf path without its instance: ``circuits[abc].instant_power_w`` -> ``circuit.instant_power_w``."""
    if leaf.startswith("ext["):
        kind, _key, path = leaf[len("ext[") : -1].split("|", 2)
        return f"ext.{kind}:{path}"
    if leaf.startswith("adopted["):
        return "adopted" + re.sub(r"\[[^\]]*\]", "", leaf.split("]", 1)[1])
    if leaf.startswith("ctl."):
        return re.sub(r"\[[^\]]*\]$", "", leaf)
    match = _INDEXED.match(leaf)
    if match is not None:
        return f"{_DEVICE_MAPS[match['container']]}.{match['field']}"
    return leaf


def _instance(leaf: str) -> str | None:
    match = _INSTANCE.match(leaf)
    return None if match is None else match.group(1)


# --- perturbation ----------------------------------------------------------


def _number(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def _candidates(
    declaration: Mapping[str, object], raw: str | None, property_id: str, device_ids: Iterable[str]
) -> list[str]:
    """Values that differ from ``raw`` and are legal for the declaration.

    Several per property, so that a derived field with a threshold (``poles >= 2``)
    or a reference that must resolve (a ``*-device-id`` naming a device in the
    tree) is exercised rather than reported as having no effect. Every member of an
    enum, because a derived field may move for only one of them, and one value
    outside it.
    """
    datatype = _text(declaration.get("datatype")) or "string"
    base = None if raw is None else _number(raw)
    pool: list[str]
    if datatype == "boolean":
        pool = ["true", "false"]
    elif datatype == "enum":
        members = [member.strip() for member in _text(declaration.get("format")).split(",") if member.strip()]
        pool = [*members, "PERTURBED_ENUM"]
    elif datatype == "integer":
        integers = [int(base) + 13] if base is not None else []
        pool = [str(value) for value in (*integers, 1, 2, 0, 13, -7)]
    elif datatype == "float":
        floats = [base + 13.25, -base] if base else []
        pool = [repr(float(value)) for value in (*floats, 0.0, 13.25, -7.5)]
    elif datatype == "json":
        pool = ['{"perturbed": true}', "{}"]
    else:
        pool = [f"{raw}~p" if raw else "perturbed-value"]
        if property_id.endswith(("-device-id", "-entity")):
            pool.extend((*device_ids, "GRID"))
    distinct: list[str] = []
    for value in pool:
        if value != raw and value not in distinct:
            distinct.append(value)
    return distinct[:3] if datatype in ("integer", "float") else distinct


_MISSING: Final = object()


def _same(left: object, right: object) -> bool:
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    return type(left) is type(right) and left == right


def _moved(baseline: Mapping[str, object], perturbed: Mapping[str, object]) -> set[str]:
    return {
        leaf
        for leaf in baseline.keys() | perturbed.keys()
        if not _same(baseline.get(leaf, _MISSING), perturbed.get(leaf, _MISSING))
    }


@dataclass(slots=True)
class _Instance:
    """One declared property on one device, and what the experiment found."""

    device_id: str
    device_type: str
    key: RowKey
    declaration: Mapping[str, object]
    raw: str | None
    addressed: bool
    extra: bool
    effect: set[str] = field(default_factory=set)
    fate: Fate = Fate.SILENT
    deferred: bool = False


def _role(capture: _Capture, device: _Device) -> str:
    if device.device_id == capture.root_id:
        return "panel"
    short = device.device_type.removeprefix(DEVICE_TYPE_PREFIX)
    if device.device_type.startswith(TYPE_LUGS):
        direction = device.values.get("info/direction", "").strip().lower()
        return f"lugs-{direction}" if direction else "lugs"
    return short


def _instances(capture: _Capture, adapter: SchemaOneAdapter) -> list[_Instance]:
    addressed = addressed_rows(devices_from_tree(capture.retained()))
    # The snapshot holds one BESS and one MID, the first of each the adapter finds.
    kept = {adapter.find_node_by_type(TYPE_BESS), adapter.find_node_by_type(TYPE_MID)}
    found: list[_Instance] = []
    for device_id in capture.order:
        device = capture.devices[device_id]
        extra = device.device_type in (TYPE_BESS, TYPE_MID) and device_id not in kept
        for node_property, declaration in device.declared.items():
            node_id, property_id = node_property.split("/", 1)
            found.append(
                _Instance(
                    device_id=device_id,
                    device_type=device.device_type,
                    key=RowKey(_role(capture, device), node_property),
                    declaration=declaration,
                    raw=device.values.get(node_property),
                    addressed=is_addressed(addressed, device.device_type, node_id, property_id),
                    extra=extra,
                )
            )
    return found


def _reference_ids(capture: _Capture) -> list[str]:
    """Devices a ``*-device-id`` or ``*-entity`` property could name: every child but circuits and lugs."""
    return [
        device_id
        for device_id in capture.order
        if device_id != capture.root_id
        and capture.devices[device_id].device_type != TYPE_CIRCUIT
        and not capture.devices[device_id].device_type.startswith(TYPE_LUGS)
    ]


def _measure(capture: _Capture, instances: list[_Instance], baseline: Mapping[str, object]) -> None:
    references = _reference_ids(capture)
    session = _replay(capture)
    for instance in instances:
        topic = f"{HOMIE_DOMAIN}/{HOMIE_VERSION}/{instance.device_id}/{instance.key.node_property}"
        property_id = instance.key.node_property.split("/", 1)[1]
        # An unvalued property cannot be returned to unvalued, so it is perturbed on
        # a replay of its own, which is discarded; each candidate replaces the last,
        # as a later retained value does.
        target = session if instance.raw is not None else _replay(capture)
        for candidate in _candidates(instance.declaration, instance.raw, property_id, references):
            target.handle_message(topic, candidate)
            instance.effect |= _moved(baseline, _flatten(target))
        if instance.raw is not None:
            session.handle_message(topic, instance.raw)
    unrestored = _moved(baseline, _flatten(session))
    if unrestored:
        raise AssertionError(f"republishing the captured values did not restore the session: {sorted(unrestored)[:5]}")


# --- classification --------------------------------------------------------

_OFF_SNAPSHOT: Final = set(_CONSUMED_OFF_SNAPSHOT)


def _classify(instance: _Instance, rules: CellRules) -> None:
    effect = instance.effect
    if any(not leaf.startswith(("ext[", "adopted[", "ctl.", "ready.")) for leaf in effect):
        instance.fate = Fate.MAPPED
    elif any(leaf.startswith("ext[") for leaf in effect):
        instance.fate = Fate.EXTENSION
    elif any(leaf.startswith("adopted[") for leaf in effect):
        instance.fate = Fate.ADOPTED
    elif effect:
        instance.fate = Fate.CONTROL
    elif _drop_reason(instance) is not None:
        instance.fate = Fate.INTENTIONAL
    elif instance.raw is not None:
        instance.deferred = instance.key in rules.deferred
        instance.fate = Fate.INTENTIONAL if instance.deferred else Fate.SILENT
    else:
        instance.fate = Fate.LATENT if instance.addressed else Fate.INERT


def _drop_reason(instance: _Instance) -> DropReason | None:
    if instance.extra:
        return DropReason.EXTRA_INSTANCE
    node_id, property_id = instance.key.node_property.split("/", 1)
    if is_addressed(_OFF_SNAPSHOT, instance.device_type, node_id, property_id):
        return DropReason.OFF_SNAPSHOT
    if node_id in (ADOPTION_IDENTITY_NODE, ADOPTION_TOPOLOGY_NODE) and not instance.addressed:
        return DropReason.IDENTITY_OR_TOPOLOGY
    return None


def _promote_conditional(instances: list[_Instance]) -> None:
    """An unvalued instance with no effect, where the same row reaches the snapshot through another.

    Its read is conditional on the instance's own topology or values, such as a
    ``feeds-device-status`` on a circuit that feeds nothing, so it takes the
    row's fate rather than reading as latent or inert.
    """
    groups: dict[RowKey, list[_Instance]] = {}
    for instance in instances:
        groups.setdefault(instance.key, []).append(instance)
    for group in groups.values():
        reached = Counter(instance.fate for instance in group if instance.fate in _REACHES)
        if not reached:
            continue
        fate = max(sorted(reached, key=_FATE_ORDER.__getitem__), key=reached.__getitem__)
        for instance in group:
            if instance.raw is None and instance.fate in (Fate.LATENT, Fate.INERT):
                instance.fate = fate


# --- value checks ----------------------------------------------------------


def _observe(raw: str, value: object) -> str:
    """Which transform turns ``raw`` into ``value``."""
    if value is None:
        return "valued -> None"
    if isinstance(value, bool):
        return TransformKind.BOOL if value == (raw.strip().lower() == "true") else "bool(other)"
    if isinstance(value, int | float):
        number = _number(raw)
        if number is None:
            return "number from an unparseable value"
        if isinstance(value, float) and value == 0.0 and math.copysign(1.0, value) < 0:
            return "negative zero"
        if value == number:
            return TransformKind.INT if isinstance(value, int) else TransformKind.IDENTITY
        if number != 0.0 and value == -number:
            return TransformKind.NEGATE
        return "number(other)"
    if isinstance(value, str):
        return TransformKind.LITERAL if value == raw else "string(other)"
    return "other"


def _compatible(observed: str, expected: TransformKind, raw: str) -> bool:
    if expected == TransformKind.DERIVED or observed == expected:
        return True
    # A zero reads the same under identity and negation.
    if expected == TransformKind.NEGATE and observed == TransformKind.IDENTITY and _number(raw) == 0.0:
        return True
    return expected == TransformKind.IDENTITY and observed == TransformKind.INT


def _check_values(
    capture: _Capture,
    instances: Iterable[_Instance],
    baseline: Mapping[str, object],
    rules: CellRules,
    failures: dict[str, set[RowKey]],
    notes: dict[str, str],
) -> set[str]:
    mismatched: set[str] = set()
    for instance in instances:
        if instance.fate not in (Fate.MAPPED, Fate.EXTENSION) or instance.raw is None:
            # An unvalued source's leaves are the leaf audit's to judge.
            continue
        raw = instance.raw
        for leaf in sorted(instance.effect):
            if leaf.startswith(("ctl.", "ready.", "adopted[")) or leaf not in baseline:
                continue
            if _instance(leaf) == raw:
                continue  # this property is the instance's snapshot key
            value = baseline[leaf]
            problem: str | None = None
            if leaf.startswith("ext["):
                if value != raw:
                    problem = f"extension value {value!r} is not the wire value {raw!r}"
            else:
                transform = rules.transform_for(instance.key, family(leaf))
                if transform is None:
                    problem = f"no documented transform from {instance.key.node_property}"
                else:
                    expected = transform.kind_for(capture.firmware)
                    observed = _observe(raw, value)
                    if not _compatible(observed, expected, raw):
                        problem = f"{raw!r} -> {value!r} is {observed}, documented {expected} ({transform.cite})"
            if problem is not None:
                mismatched.add(leaf)
                failures.setdefault(leaf, set()).add(instance.key)
                notes[leaf] = f"{instance.key.role} {instance.key.node_property}: {problem}"
    return mismatched


# --- the leaf audit ---------------------------------------------------------


def _audit_leaves(
    capture: _Capture,
    instances: Iterable[_Instance],
    baseline: Mapping[str, object],
    rules: CellRules,
    failures: dict[str, set[RowKey]],
    notes: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Leaves holding a value no valued wire property moves, and the derivations that account for the rest."""
    sources: dict[str, list[_Instance]] = {}
    for instance in instances:
        for leaf in instance.effect:
            sources.setdefault(leaf, []).append(instance)
    fabricated: set[str] = set()
    used: set[str] = set()
    for leaf, value in baseline.items():
        if leaf.startswith(("ready.", "ext[", "adopted[")) or value is None or value in ((), ""):
            continue
        owners = sources.get(leaf, [])
        if any(owner.raw is not None for owner in owners):
            continue
        device_id = _instance(leaf)
        device = None if device_id is None else capture.devices.get(device_id)
        derivation = rules.derivation_for(family(leaf), value, None if device is None else device.values)
        if derivation is not None:
            used.add(derivation.family)
            continue
        fabricated.add(leaf)
        failures.setdefault(leaf, set()).update(owner.key for owner in owners)
        unvalued = ", ".join(sorted({f"{owner.device_id} {owner.key.node_property}" for owner in owners}))
        notes[leaf] = f"{value!r} with {'only unvalued sources: ' + unvalued if owners else 'no wire source'}"
    return fabricated, used


# --- a cell ----------------------------------------------------------------


def _grid_forming_names(capture: _Capture) -> dict[str, str | None]:
    """What the snapshot names each device while the MID says that device forms the grid."""
    grid_forming = f"{NODE_GRID}/{PROP_GRID_FORMING_ENTITY}"
    mid = next((device_id for device_id in capture.order if capture.devices[device_id].device_type == TYPE_MID), None)
    if mid is None or grid_forming not in capture.devices[mid].declared:
        return {}
    probe = _replay(capture)
    topic = f"{HOMIE_DOMAIN}/{HOMIE_VERSION}/{mid}/{grid_forming}"
    names: dict[str, str | None] = {}
    for device_id in capture.order:
        if device_id == capture.root_id:
            continue
        probe.handle_message(topic, device_id)
        snapshot = probe.build_snapshot()
        names[device_id] = None if snapshot.mid is None else snapshot.mid.grid_forming_device_name
    return names


def run_cell(tree_path: Path, rules: CellRules) -> FidelityReport:
    """Measure one captured tree against ``rules``."""
    capture = _load(tree_path)
    adapter = _replay(capture)
    baseline = _flatten(adapter)
    if baseline["ready.is_ready"] is not True:
        raise AssertionError(f"{tree_path.name}: the replayed tree never became ready")

    instances = _instances(capture, adapter)
    _measure(capture, instances, baseline)
    for instance in instances:
        _classify(instance, rules)
    _promote_conditional(instances)

    failures: dict[str, set[RowKey]] = {}
    notes: dict[str, str] = {}
    mismatched = _check_values(capture, instances, baseline, rules, failures, notes)
    fabricated, used = _audit_leaves(capture, instances, baseline, rules, failures, notes)

    fates: dict[RowKey, set[Fate]] = {}
    for instance in instances:
        fates.setdefault(instance.key, set()).add(instance.fate)
    return FidelityReport(
        rows={key: tuple(sorted(found, key=_FATE_ORDER.__getitem__)) for key, found in sorted(fates.items())},
        silently_dropped=tuple(sorted({instance.key for instance in instances if instance.fate == Fate.SILENT})),
        deferred=tuple(sorted({instance.key for instance in instances if instance.deferred})),
        fabricated=tuple(sorted(fabricated)),
        mismatched=tuple(sorted(mismatched)),
        declared_property_instances=len(instances),
        instance_fates=dict(Counter(instance.fate for instance in instances)),
        leaf_rows={leaf: frozenset(keys) for leaf, keys in failures.items()},
        notes=notes,
        derivations_used=frozenset(used),
        grid_forming_names=_grid_forming_names(capture),
    )
