"""Devices this adapter models nothing for are adopted whole; modelled ones never are.

The rule under test has two halves and both are failure modes. Adopting a device
the snapshot builder already reads would stand a machine-named device card beside
a curated one describing the same hardware. *Not* adopting one the builder
ignores is the silence this module exists to end -- a panel publishing a device
nobody modelled, and no sign of it anywhere.

Every case is built by putting a device on the reference tree and reading what
comes back, never by calling the classifier directly: the question is what a
panel gets, and a classifier that agrees with itself proves nothing about that.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1.adoption import MODELLED_TYPES
from span_panel_api_schema_1.reference_payloads import device_from_topics, parent_child_tree
from span_panel_api_schema_1.snapshot import build_snapshot

if TYPE_CHECKING:
    from span_panel_api.models import AdoptedDevice

PANEL = "example-40t-001"

UNMODELLED_TYPE = "energy.ebus.device.generator"
"""A type this adapter models nothing for.

A generator rather than an invented string: the eBus vocabulary already names one
as a grid-forming device class, and the schema is explicitly vendor-extensible,
so an unmodelled arrival is the expected case rather than a hypothetical.
"""


def _tree() -> dict[str, dict[str, str]]:
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _snapshot(tree: dict[str, dict[str, str]]) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL, tree[PANEL])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL]
    return build_snapshot(panel, children)


def _device(
    device_type: str,
    *,
    name: str = "Backup Generator",
    nodes: dict[str, dict[str, dict[str, object]]] | None = None,
    values: dict[str, str] | None = None,
) -> dict[str, str]:
    """One device's retained topics, as the broker hands them back.

    `$description` is a JSON *string* rather than a nested object, which is how
    the transport carries it and how the reference payload stores it.
    """
    description: dict[str, object] = {
        "homie": "5.0",
        "version": 1,
        "type": device_type,
        "name": name,
        "nodes": nodes or {},
    }
    topics = {"$description": json.dumps(description), "$state": "ready"}
    topics.update(values or {})
    return topics


def _with(tree: dict[str, dict[str, str]], device_id: str, topics: dict[str, str]) -> dict[str, dict[str, str]]:
    tree[device_id] = topics
    return tree


def _adopted(snapshot: SpanPanelSnapshot) -> dict[str, AdoptedDevice]:
    return {device.device_id: device for device in snapshot.adopted_devices}


# -- The reference tree adopts nothing ---------------------------------------


def test_a_tree_of_modelled_devices_adopts_nothing() -> None:
    """The captured tree is thirteen devices this adapter reads, so it adopts none.

    The baseline the rest of this module measures against: anything that shows up
    here is a modelled device leaking into adoption, which is the failure that
    duplicates a curated device card.
    """
    assert _snapshot(_tree()).adopted_devices == ()


@pytest.mark.parametrize("modelled", MODELLED_TYPES)
def test_no_modelled_type_is_ever_adopted(modelled: str) -> None:
    """Every type the snapshot builder sorts into a role stays out of adoption.

    Parametrised over the declared tuple and asserted through `build_snapshot`,
    so the tuple cannot drift from the builder silently: a type dropped from
    `TreeRoles` while left in `MODELLED_TYPES` would make its devices invisible
    to both paths at once, and this is what fails instead.
    """
    tree = _with(_tree(), "extra-device", _device(modelled))
    assert "extra-device" not in _adopted(_snapshot(tree))


@pytest.mark.parametrize("subtype", [f"{TYPE}.upstream" for TYPE in MODELLED_TYPES])
def test_a_subtype_of_a_modelled_type_is_not_adopted(subtype: str) -> None:
    """Firmware may subtype a device class, and the builder matches lugs by prefix.

    A subtype adopted behind the builder's back is the same duplicate-device
    failure, arriving through a spelling rather than through a type.
    """
    tree = _with(_tree(), "extra-device", _device(subtype))
    assert "extra-device" not in _adopted(_snapshot(tree))


def test_a_device_declaring_no_type_yet_is_skipped_rather_than_adopted() -> None:
    """A device describing itself without a type yet is not an unmodelled device.

    Mid-discovery is a normal state rather than an error -- `device_type` answers
    `""` for it by design. Adopting on that would mint a device card for
    something whose type is about to arrive, and then leave it standing when the
    real type turns out to be one this adapter models.
    """
    untyped = json.dumps({"homie": "5.0", "version": 1, "name": "Arriving", "nodes": {}})
    tree = _with(_tree(), "still-arriving", {"$description": untyped, "$state": "init"})
    assert _snapshot(tree).adopted_devices == ()


# -- An unmodelled type is adopted whole -------------------------------------


def test_an_unmodelled_type_is_adopted() -> None:
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE))
    adopted = _adopted(_snapshot(tree))

    assert set(adopted) == {"generator-1"}
    assert adopted["generator-1"].device_type == UNMODELLED_TYPE
    assert adopted["generator-1"].name == "Backup Generator"


def test_adoption_carries_the_value_where_discovery_carries_only_the_declaration() -> None:
    """The one difference between the two records, and the reason they are two types.

    A discovery row is built to be forwarded in diagnostics, which leave the
    machine, so it has no member a reading can go in. An adopted property is
    built to become an entity on the machine that made it, so it must carry one.
    """
    nodes = {"meter": {"properties": {"active-power": {"datatype": "float", "unit": "W"}}}}
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes, values={"meter/active-power": "2400"}))

    (reading,) = _adopted(_snapshot(tree))["generator-1"].properties
    assert (reading.node_id, reading.property_id) == ("meter", "active-power")
    assert (reading.datatype, reading.unit) == ("float", "W")
    assert reading.value == "2400"
    assert reading.path == "meter/active-power"


def test_a_declared_property_with_nothing_published_adopts_with_no_value() -> None:
    """Declared-and-never-valued is a state to report, not a property to drop.

    Dropping it would make the entity appear only once the panel first published,
    which reads to a user as an entity that comes and goes.
    """
    nodes = {"meter": {"properties": {"active-power": {"datatype": "float", "unit": "W"}}}}
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes))

    (reading,) = _adopted(_snapshot(tree))["generator-1"].properties
    assert reading.value is None


def test_the_declared_format_and_settable_flag_survive_adoption() -> None:
    """Both halves of what a consumer needs to build a control rather than a reading.

    `settable` says a write is accepted; `format` is the value domain that makes
    the control constructible. A select with no option list is not a safer
    control, it is a broken one, so the consumer needs to see both.
    """
    nodes = {
        "generator": {
            "properties": {
                "mode": {
                    "datatype": "enum",
                    "format": "AUTO,MANUAL,OFF",
                    "settable": True,
                }
            }
        }
    }
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes))

    (control,) = _adopted(_snapshot(tree))["generator-1"].properties
    assert control.settable is True
    assert control.format == "AUTO,MANUAL,OFF"
    assert control.unit is None


# -- info and connection resolve away from entities --------------------------


def test_info_becomes_the_device_card_and_not_properties() -> None:
    """`info` describes the thing rather than reporting a reading.

    The same treatment `bess_device_info` has given a curated device since v1.0,
    applied to an adopted one for the same reason.
    """
    nodes = {
        "info": {
            "properties": {
                "vendor-name": {"datatype": "string"},
                "model": {"datatype": "string"},
                "serial-number": {"datatype": "string"},
                "firmware-version": {"datatype": "string"},
                "hardware-version": {"datatype": "string"},
            }
        }
    }
    values = {
        "info/vendor-name": "Example Power",
        "info/model": "GEN-9000",
        "info/serial-number": "EX-0000-0001",
        "info/firmware-version": "3.2.1",
        "info/hardware-version": "rev-C",
    }
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes, values=values))

    device = _adopted(_snapshot(tree))["generator-1"]
    assert device.properties == ()
    assert device.vendor_name == "Example Power"
    assert device.model == "GEN-9000"
    assert device.serial_number == "EX-0000-0001"
    assert device.software_version == "3.2.1"
    assert device.hardware_version == "rev-C"


def test_connection_is_dropped_rather_than_surfaced() -> None:
    """`connection` is the device tree, which is `via_device`, not a sensor.

    Excluded by node rather than by property name on purpose. The catalogs carry
    no marker for "this string is a device reference", so a name list is the only
    alternative -- and a name list goes stale silently, which is what `ebus-sdk`'s
    own `topology.py` does by covering two such properties and omitting a third.
    """
    nodes = {
        "connection": {
            "properties": {
                "fed-by-device-id": {"datatype": "string"},
                "feeds-device-type": {"datatype": "string"},
            }
        },
        "meter": {"properties": {"active-power": {"datatype": "float", "unit": "W"}}},
    }
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes))

    device = _adopted(_snapshot(tree))["generator-1"]
    assert [reading.path for reading in device.properties] == ["meter/active-power"]


def test_an_info_property_the_card_has_no_field_for_is_not_promoted_to_an_entity() -> None:
    """`info` is excluded by node, so an unrecognised member of it is dropped too.

    The alternative -- dropping only the five the card reads -- would surface
    `info/nominal-power` and its siblings as string sensors the moment a vendor
    declared one, which is the metadata-as-entities failure the node rule exists
    to prevent.
    """
    nodes = {"info": {"properties": {"nominal-power": {"datatype": "float", "unit": "W"}}}}
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes, values={"info/nominal-power": "9000"}))

    assert _adopted(_snapshot(tree))["generator-1"].properties == ()


# -- Multiplicity is not adoption --------------------------------------------


def test_a_second_bess_is_not_adopted() -> None:
    """A modelled type arriving twice is a multiplicity limit, not an unmodelled device.

    `TreeRoles` keeps the first BESS and silently ignores the rest, which is a
    real gap -- but adopting the extra one would answer it with a machine-named
    device card standing beside the curated Battery, describing the same
    hardware. The gap stays visible as a gap instead.
    """
    tree = _with(_tree(), "bess-2", _device("energy.ebus.device.bess", name="Second Battery"))
    assert _snapshot(tree).adopted_devices == ()


# -- schema_0 adopts nothing -------------------------------------------------


def test_the_snapshot_field_defaults_empty() -> None:
    """What makes the field additive rather than a protocol change.

    schema_0 never populates it: flat has no device tree to find an unmodelled
    device in. A default of `()` is what lets that adapter stay untouched and
    keeps `adopted_devices` off `SchemaAdapter`, whose members are required of
    every adapter package.
    """
    assert SpanPanelSnapshot.__dataclass_fields__["adopted_devices"].default == ()


# -- The set topic exists only where a write is legal ------------------------


def test_a_settable_property_carries_the_topic_a_write_goes_to() -> None:
    nodes = {"generator": {"properties": {"mode": {"datatype": "enum", "format": "AUTO,OFF", "settable": True}}}}
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes))

    (control,) = _adopted(_snapshot(tree))["generator-1"].properties
    assert control.set_topic == "ebus/5/generator-1/generator/mode/set"


def test_a_property_the_device_does_not_declare_settable_carries_no_topic() -> None:
    """The absence is the authorisation, not a flag a caller is trusted to read.

    A consumer cannot construct a write for a property that carries no topic, so
    "is this writable" is answered by the declaration once, here, rather than by
    every caller remembering to ask.
    """
    nodes = {"meter": {"properties": {"active-power": {"datatype": "float", "unit": "W"}}}}
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE, nodes=nodes))

    (reading,) = _adopted(_snapshot(tree))["generator-1"].properties
    assert reading.set_topic is None


def test_no_topic_reachable_this_way_can_name_a_modelled_device() -> None:
    """The property that keeps this from being a generic write.

    A generic `set_property_topic(device, node, property)` would put every
    curated control one argument away -- including the two that do real work on
    the way out: the islanding assertion translates its value, and the charge
    ceiling refuses one above what the charger was commissioned for. Because a
    modelled device produces no `AdoptedDevice` at all, no topic produced here
    can address one, whatever a caller passes.
    """
    tree = _with(_tree(), "generator-1", _device(UNMODELLED_TYPE))
    snapshot = _snapshot(tree)

    addressable = {device.device_id for device in snapshot.adopted_devices for prop in device.properties if prop.set_topic}
    modelled = {device_id for device_id in _tree() if device_id != PANEL}
    assert not (addressable & modelled)


def test_a_settable_property_on_a_modelled_device_is_never_adopted_and_so_never_writable() -> None:
    """Stated against a circuit, which really does declare settable properties.

    The reference tree's circuits declare `switch/relay` and `load-shed/priority`
    settable, and both have curated setters. Adoption must not offer a second
    route to either.
    """
    snapshot = _snapshot(_tree())
    assert snapshot.adopted_devices == ()
