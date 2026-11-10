"""Hand-built parent/child trees, for shapes the reference payloads do not carry.

A test that needs a meter without a breaker space, a pair of circuits sharing one
meter, or a reading the reference tree never publishes builds it here rather than
editing a capture: the capture stays what a producer actually sent, and the shape
a test asks about is named by the builder that makes it.

**Everything is synthetic.** Device ids are neutral (`panel-a`, `c-1`,
`meter-a`), values are invented and chosen only to be told apart, and nothing is
copied from any capture. Datatypes and units follow the eBus capability
catalogs vendored under `packages/schema-1/spec/catalogs/` where those define
the property.

**A builder declares, and values, exactly what its shape needs.** Each takes only
what a test varies; everything else is fixed. An absent property is not
declared at all, while one declared and not yet arrived comes from
`without_values`, so the two stay distinct the way they are on the wire. Values are published as the wire
carries them, in the publisher's frame, and no builder interprets a sign or a
direction: that is the adapter's job, and the tests are about whether it does it.

Builders return `SyntheticDevice`; `tree` assembles a panel and its children into
the same `RetainedTopicTree` shape a capture has, so `replay` and
`devices_from_tree` read both alike, and `discovered` rebuilds one device alone
for a test that calls a per-device builder directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json
from typing import Final, Literal, NotRequired, TypedDict

from ebus_sdk.homie import DiscoveredDevice

from .schema_one import RetainedTopicTree, device_from_topics

PANEL_ID: Final = "panel-a"
"""The panel's device id, which is also its serial number."""

FIRMWARE_VERSION: Final = "spanos3/r202639/03"
"""The firmware every synthetic panel reports."""

_TYPE_PANEL: Final = "energy.ebus.device.distribution-enclosure"
_TYPE_CIRCUIT: Final = "energy.ebus.device.circuit"
_TYPE_LUGS: Final = "energy.ebus.device.lugs"
_TYPE_EVSE: Final = "energy.ebus.device.evse"
_TYPE_BESS: Final = "energy.ebus.device.bess"
_CAPABILITY_PREFIX: Final = "energy.ebus.capability"


class PropertyDeclaration(TypedDict):
    """One property as a `$description` declares it."""

    name: str
    datatype: str
    format: NotRequired[str]
    unit: NotRequired[str]
    settable: NotRequired[bool]


type NodeDeclarations = Mapping[str, Mapping[str, PropertyDeclaration]]
"""Node id -> property id -> declaration."""


@dataclass(frozen=True)
class SyntheticDevice:
    """One device of a synthetic tree: its declaration and its retained values.

    `name` is the `$description.name`, fixed when the device is built, so dropping
    a value never changes the description. `values` is keyed `node/property`, the
    topic suffix the broker retains, and every key in it is declared in `nodes`.
    """

    device_id: str
    device_type: str
    name: str
    nodes: NodeDeclarations
    values: Mapping[str, str]


@dataclass(frozen=True)
class MeterReading:
    """The four values a circuit meter publishes, as the wire carries them."""

    active_power_w: float
    current_a: float
    imported_energy_wh: float
    exported_energy_wh: float


METER_READING: Final = MeterReading(
    active_power_w=-240.5,
    current_a=2.0,
    imported_energy_wh=1250.5,
    exported_energy_wh=3875.25,
)
"""The default reading. Imported and exported differ, so a swap shows."""

_DECLARATIONS: Final[NodeDeclarations] = {
    "info": {
        "name": {"name": "Name", "datatype": "string"},
        "spaces": {"name": "Breaker space number(s), comma-separated", "datatype": "string"},
        "nominal-voltage": {"name": "Rated voltage of the circuit", "datatype": "float", "unit": "V"},
        "serial-number": {"name": "Serial number", "datatype": "string"},
        "vendor-name": {"name": "Vendor name", "datatype": "string"},
        "model": {"name": "Model", "datatype": "string"},
        "firmware-version": {"name": "Firmware version", "datatype": "string"},
        "data-model-version": {"name": "eBus data-model version", "datatype": "string"},
        "direction": {"name": "Direction", "datatype": "enum", "format": "UPSTREAM,DOWNSTREAM"},
    },
    "meter": {
        "active-power": {"name": "Active power", "datatype": "float", "unit": "W"},
        "current": {"name": "Current", "datatype": "float", "unit": "A"},
        "imported-energy": {"name": "Energy imported", "datatype": "float", "unit": "Wh"},
        "exported-energy": {"name": "Energy exported", "datatype": "float", "unit": "Wh"},
        "shared-with-device-ids": {"name": "Other devices this meter also measures", "datatype": "string"},
        "voltage-a": {"name": "L1 voltage", "datatype": "float", "unit": "V"},
        "voltage-b": {"name": "L2 voltage", "datatype": "float", "unit": "V"},
        "busbar-current": {"name": "Busbar current", "datatype": "float", "unit": "A"},
        "frequency": {"name": "AC frequency", "datatype": "float", "unit": "Hz"},
    },
    "switch": {
        "relay": {"name": "Relay state", "datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"},
        "relay-controllable": {"name": "Can the relay be commanded by the user?", "datatype": "boolean"},
        "relay-requester": {
            "name": "Actor requesting the relay state",
            "datatype": "enum",
            "format": "UNKNOWN,NONE,LOAD_SHED,USER,PCS,CONFIGURATION,FAULT",
        },
        "shared-with-device-ids": {"name": "Other devices this relay also switches", "datatype": "string"},
        "lock-state": {"name": "Lock state", "datatype": "enum", "format": "UNLOCKED,LOCKED"},
    },
    "breaker": {
        "rating": {"name": "Breaker rating", "datatype": "integer", "unit": "A"},
        "poles": {"name": "Number of breaker poles", "datatype": "integer", "format": "1:4:1"},
        "protection-functions": {
            "name": "Protection types",
            "datatype": "enum",
            "format": "OVERCURRENT,SHORT_CIRCUIT,GROUND_FAULT,ARC_FAULT",
        },
    },
    "load-shed": {
        "priority": {"name": "Shed priority", "datatype": "enum", "format": "UNKNOWN,OFF_GRID,SOC_THRESHOLD,NEVER"},
    },
    "connection": {
        "feeds-device-id": {"name": "Device id fed by this connection", "datatype": "string"},
        "feeds-device-type": {"name": "Type of the device fed", "datatype": "string"},
        "feeds-device-status": {"name": "Link health to the device fed", "datatype": "enum", "format": "OK,LOST,DEGRADED"},
        "feeds-role": {
            "name": "Role of a downstream node not published as a device",
            "datatype": "enum",
            "format": "LOADS,SUBPANEL,SOLAR,STORAGE,GENERATOR,MIXED,UNUSED",
        },
        "overcurrent-protection": {"name": "Overcurrent protection rating", "datatype": "integer", "unit": "A"},
    },
    "soc": {
        "soc": {"name": "State of charge", "datatype": "float", "unit": "%"},
        "soe": {"name": "State of energy", "datatype": "float", "unit": "kWh"},
    },
    "status": {
        "communication-state": {"name": "Communication state", "datatype": "enum", "format": "OK,DEGRADED,LOST,UNKNOWN"},
        "status": {"name": "Status", "datatype": "enum", "format": "AVAILABLE,PREPARING,CHARGING,UNAVAILABLE"},
    },
}
"""Every property a builder may declare.

Datatypes and units follow the vendored catalogs wherever they define the
property; the names are this suite's own, and the adapter reads none of them.
"""


def _declare(values: Mapping[str, str], *, settable: Sequence[str] = ()) -> NodeDeclarations:
    """The declaration of every valued path.

    `settable` names the paths whose declaration carries `settable: true`; every
    other declaration omits the attribute, which Homie 5 reads as not settable.
    """
    nodes: dict[str, dict[str, PropertyDeclaration]] = {}
    for path in values:
        node, _, prop = path.partition("/")
        declaration = _DECLARATIONS[node][prop]
        nodes.setdefault(node, {})[prop] = {**declaration, "settable": True} if path in settable else declaration
    return nodes


def _number(value: float) -> str:
    return repr(float(value))


def _meter(reading: MeterReading) -> dict[str, str]:
    return {
        "meter/active-power": _number(reading.active_power_w),
        "meter/current": _number(reading.current_a),
        "meter/imported-energy": _number(reading.imported_energy_wh),
        "meter/exported-energy": _number(reading.exported_energy_wh),
    }


def panel(
    *,
    model: str = "MAIN_32",
    advertised_models: Sequence[str] | None = None,
    busbar_current_a: float | None = None,
    frequency_hz: float | None = None,
) -> SyntheticDevice:
    """The panel, reporting `model` and advertising `advertised_models`.

    The advertised set is the `$format` of `info/model`, and defaults to the one
    model reported. The busbar current and the line frequency are declared only
    when given.
    """
    values = {
        "info/serial-number": PANEL_ID,
        "info/vendor-name": "SPAN",
        "info/model": model,
        "info/firmware-version": FIRMWARE_VERSION,
        "info/data-model-version": "1.0",
        "meter/voltage-a": _number(120.5),
        "meter/voltage-b": _number(121.5),
    }
    if busbar_current_a is not None:
        values["meter/busbar-current"] = _number(busbar_current_a)
    if frequency_hz is not None:
        values["meter/frequency"] = _number(frequency_hz)
    nodes = _declare(values)
    advertised = ",".join(advertised_models if advertised_models is not None else (model,))
    model_declaration: PropertyDeclaration = {"name": "Model", "datatype": "enum", "format": advertised}
    return SyntheticDevice(
        device_id=PANEL_ID,
        device_type=_TYPE_PANEL,
        name="Panel",
        nodes={**nodes, "info": {**nodes["info"], "model": model_declaration}},
        values=values,
    )


def lugs(direction: Literal["UPSTREAM", "DOWNSTREAM"], *, overcurrent_protection_a: int | None = None) -> SyntheticDevice:
    """A lugs device, `lugs-upstream` or `lugs-downstream`.

    Its overcurrent-protection rating is declared only when given.
    """
    values = {
        "info/direction": direction,
        "meter/active-power": _number(0.0),
        "meter/imported-energy": _number(0.0),
        "meter/exported-energy": _number(0.0),
    }
    if overcurrent_protection_a is not None:
        values["connection/overcurrent-protection"] = str(overcurrent_protection_a)
    return SyntheticDevice(
        device_id=f"lugs-{direction.lower()}",
        device_type=_TYPE_LUGS,
        name=f"{direction.capitalize()} lugs",
        nodes=_declare(values),
        values=values,
    )


def hosted_circuit(
    device_id: str,
    spaces: Sequence[int],
    *,
    name: str | None = None,
    reading: MeterReading = METER_READING,
    feeds_role: str | None = None,
    feeds: SyntheticDevice | None = None,
    feed_status: str = "OK",
    nominal_voltage_v: float | None = None,
    protection_functions: Sequence[str] = (),
    relay: bool = True,
    meter_shared_with: Sequence[str] = (),
    relay_shared_with: Sequence[str] = (),
) -> SyntheticDevice:
    """A circuit occupying `spaces`, with a breaker and a controllable relay.

    One pole per space. With `relay` false the circuit declares no `switch` node
    at all. The connection node is declared only when the circuit has a role or
    feeds a device; the nominal voltage, the protection functions and each
    shared-with list only when given. `feeds_role` and the shared-with lists are
    published exactly as given, so an out-of-set role, or a list naming the
    circuit itself or an id no device has, can be published too.
    """
    if relay_shared_with and not relay:
        raise ValueError(f"{device_id} has no relay to share")
    circuit_name = name if name is not None else f"Circuit {device_id}"
    values = {
        "info/name": circuit_name,
        "info/spaces": ",".join(str(space) for space in spaces),
        **_meter(reading),
        "breaker/rating": "20",
        "breaker/poles": str(len(spaces)),
        "load-shed/priority": "SOC_THRESHOLD",
    }
    if relay:
        values["switch/relay"] = "CLOSED"
        values["switch/relay-controllable"] = "true"
        values["switch/relay-requester"] = "NONE"
    if meter_shared_with:
        values["meter/shared-with-device-ids"] = ",".join(meter_shared_with)
    if relay_shared_with:
        values["switch/shared-with-device-ids"] = ",".join(relay_shared_with)
    if feeds_role is not None:
        values["connection/feeds-role"] = feeds_role
    if feeds is not None:
        values["connection/feeds-device-id"] = feeds.device_id
        values["connection/feeds-device-type"] = feeds.device_type
        values["connection/feeds-device-status"] = feed_status
    if nominal_voltage_v is not None:
        values["info/nominal-voltage"] = _number(nominal_voltage_v)
    if protection_functions:
        values["breaker/protection-functions"] = ",".join(protection_functions)
    return SyntheticDevice(
        device_id=device_id,
        device_type=_TYPE_CIRCUIT,
        name=circuit_name,
        nodes=_declare(values, settable=("switch/relay", "load-shed/priority")),
        values=values,
    )


def space_less_meter(device_id: str = "meter-a", *, reading: MeterReading = METER_READING) -> SyntheticDevice:
    """A circuit-typed device that is only a meter.

    It declares no `info` node, so no breaker space and no name, and no
    `switch` node, so no relay.
    """
    values = _meter(reading)
    return SyntheticDevice(
        device_id=device_id, device_type=_TYPE_CIRCUIT, name="Meter", nodes=_declare(values), values=values
    )


def shared_pair(first: str = "c-1", second: str = "c-2", *, space: int = 1) -> tuple[SyntheticDevice, SyntheticDevice]:
    """Two circuits on one space, sharing one meter and one relay.

    Each names the other in `meter/` and `switch/shared-with-device-ids`, and
    both publish the same reading, as one meter measuring both would.
    """

    def member(device_id: str, peer: str) -> SyntheticDevice:
        return hosted_circuit(device_id, (space,), meter_shared_with=(peer,), relay_shared_with=(peer,))

    return member(first, second), member(second, first)


def evse(device_id: str = "evse-a", *, lock_settable: bool = True) -> SyntheticDevice:
    """A charger whose `switch/lock-state` is settable unless told otherwise."""
    values = {
        "info/vendor-name": "SPAN",
        "info/model": "Charger",
        "info/serial-number": f"{device_id}-serial",
        "switch/lock-state": "UNLOCKED",
        "status/status": "AVAILABLE",
    }
    return SyntheticDevice(
        device_id=device_id,
        device_type=_TYPE_EVSE,
        name="Charger",
        nodes=_declare(values, settable=("switch/lock-state",) if lock_settable else ()),
        values=values,
    )


def battery(device_id: str = "battery-a") -> SyntheticDevice:
    """A battery with its identity, state of charge, power and link state."""
    values = {
        "info/vendor-name": "Example",
        "info/model": "Battery",
        "info/serial-number": f"{device_id}-serial",
        "soc/soc": _number(50.0),
        "soc/soe": _number(6.5),
        "meter/active-power": _number(0.0),
        "status/communication-state": "OK",
    }
    return SyntheticDevice(
        device_id=device_id, device_type=_TYPE_BESS, name="Battery", nodes=_declare(values), values=values
    )


def circuit_fed_battery(
    circuit_id: str = "c-1", battery_id: str = "battery-a", *, space: int = 1, status: str = "OK"
) -> tuple[SyntheticDevice, SyntheticDevice]:
    """A battery and the circuit whose `connection/feeds-device-id` names it.

    `status` is the circuit's `connection/feeds-device-status`, the panel's view
    of its link to the battery.
    """
    fed = battery(battery_id)
    return hosted_circuit(circuit_id, (space,), feeds=fed, feed_status=status), fed


def without_values(device: SyntheticDevice, *paths: str) -> SyntheticDevice:
    """The same device, still declaring everything, with `paths` unvalued.

    No paths drops every value: a device that has described itself and whose
    retained values have not arrived. A path the device does not declare is
    refused, so a misspelt one cannot pass as a dropped value.
    """
    declared = {f"{node}/{prop}" for node, properties in device.nodes.items() for prop in properties}
    if undeclared := [path for path in paths if path not in declared]:
        raise ValueError(f"{device.device_id} declares no {', '.join(undeclared)}")
    dropped = set(paths) if paths else set(device.values)
    return replace(device, values={path: value for path, value in device.values.items() if path not in dropped})


def panel_with_positions(model: str, positions: Sequence[int]) -> RetainedTopicTree:
    """A panel reporting `model`, with one single-pole circuit `c-<n>` at each position."""
    return tree(panel(model=model), *(hosted_circuit(f"c-{position}", (position,)) for position in positions))


def tree(root: SyntheticDevice, *children: SyntheticDevice) -> RetainedTopicTree:
    """The retained topics of `root` and its `children`, as a capture holds them.

    Every child is a direct child of the root, listed in the root's
    `$description.children` in the order given. A device id given twice is
    refused.
    """
    seen = {root.device_id}
    for child in children:
        if child.device_id in seen:
            raise ValueError(f"device id {child.device_id} is given twice")
        seen.add(child.device_id)
    child_ids = [child.device_id for child in children]
    topics = {root.device_id: _retained(root, root_id=None, children=child_ids)}
    for child in children:
        topics[child.device_id] = _retained(child, root_id=root.device_id, children=())
    return topics


def discovered(device: SyntheticDevice) -> DiscoveredDevice:
    """One device rebuilt alone, as a child of `PANEL_ID`, for a per-device builder."""
    return device_from_topics(device.device_id, _retained(device, root_id=PANEL_ID, children=()))


def _retained(device: SyntheticDevice, *, root_id: str | None, children: Sequence[str]) -> dict[str, str]:
    """`$description`, `$state` and every value, as the broker retains them.

    A root's description carries neither `root` nor `parent`, as Homie 5 has it;
    every other device names the root as both.
    """
    description: dict[str, object] = {
        "homie": "5.0",
        "version": 1,
        "type": device.device_type,
        "name": device.name,
        "nodes": {
            node: {"name": node, "type": f"{_CAPABILITY_PREFIX}.{node}", "properties": dict(properties)}
            for node, properties in device.nodes.items()
        },
        "children": list(children),
        "extensions": [],
    }
    if root_id is not None:
        description["root"] = root_id
        description["parent"] = root_id
    return {"$description": json.dumps(description), "$state": "ready", **device.values}
