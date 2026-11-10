"""Every reference capture replays through the adapter into the panel it declares.

Properties over the whole capture set, never one capture's literals: each test
reads what it expects out of the same capture it replays, so a capture added to
`fixtures/captures/` is covered by every test here without an edit. What the
adapter makes of a particular shape belongs to the tests of that behaviour;
these pin that every capture is a usable snapshot of its own panel, and that the
selectors other capture tests rely on find what the files hold.

The selectors are checked against evidence they do not read themselves: the
capture file parsed again here with plain `json`, the adapter's own reading of
each circuit's spaces, the feeding circuit's own statement of what it feeds,
and each peer's list of its peers.
"""

from __future__ import annotations

import json

import pytest

from reference_payloads.captures import (
    CAPTURES,
    capture_adapter,
    capture_path,
    capture_snapshot,
    capture_tree,
    circuit_fed_batteries,
    hosted_circuits,
    shared_meter_peers,
    space_less_meters,
)

_CIRCUIT_TYPE = "energy.ebus.device.circuit"
_BATTERY_TYPE = "energy.ebus.device.bess"


def _file_devices(stem: str) -> dict[str, dict[str, object]]:
    """The capture's devices, parsed from the file with plain `json` and nothing of the helper's."""
    document: object = json.loads(capture_path(stem).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    devices = document["devices"]
    assert isinstance(devices, dict)
    parsed: dict[str, dict[str, object]] = {}
    for device_id, body in devices.items():
        assert isinstance(device_id, str) and isinstance(body, dict)
        parsed[device_id] = {str(key): value for key, value in body.items()}
    return parsed


def _type(body: dict[str, object]) -> str:
    description = body["description"]
    assert isinstance(description, dict)
    device_type = description["type"]
    assert isinstance(device_type, str)
    return device_type


def _declares_spaces(body: dict[str, object]) -> bool:
    description = body["description"]
    assert isinstance(description, dict)
    nodes: object = description["nodes"]
    assert isinstance(nodes, dict)
    info: object = nodes.get("info", {})
    assert isinstance(info, dict)
    properties: object = info.get("properties", {})
    assert isinstance(properties, dict)
    return "spaces" in properties


def _string_value(body: dict[str, object], topic: str) -> str | None:
    """A string-typed retained value; numbers live under `numeric_properties`."""
    properties = body["properties"]
    assert isinstance(properties, dict)
    value = properties.get(topic)
    assert value is None or isinstance(value, str)
    return value


@pytest.mark.parametrize("stem", CAPTURES)
def test_every_capture_is_ready_once_replayed(stem: str) -> None:
    assert capture_adapter(stem).is_ready()


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_snapshot_identifies_the_panel_its_tree_declares(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    root = tree.root_id
    declared = {topic: tree.value(root, topic) for topic in ("info/serial-number", "info/firmware-version", "info/model")}
    assert None not in declared.values(), declared
    assert snapshot.serial_number == declared["info/serial-number"]
    assert snapshot.firmware_version == declared["info/firmware-version"]
    # `UNKNOWN` is a member of the model enum that names no model, so it reads as absent.
    assert snapshot.model == (None if declared["info/model"] == "UNKNOWN" else declared["info/model"])


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_snapshot_holds_exactly_the_circuits_its_tree_declares(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    assert tree.circuits()
    assert sorted(snapshot.circuits) == sorted(tree.circuits())


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_selectors_agree_with_the_file_read_independently(stem: str) -> None:
    devices = _file_devices(stem)
    circuits = {device_id for device_id, body in devices.items() if _type(body) == _CIRCUIT_TYPE}
    fed: dict[str, str] = {}
    peers: dict[str, tuple[str, ...]] = {}
    for circuit_id in circuits:
        body = devices[circuit_id]
        target = _string_value(body, "connection/feeds-device-id")
        if target is not None and target in devices and _type(devices[target]) == _BATTERY_TYPE:
            fed[target] = circuit_id
        listed = _string_value(body, "meter/shared-with-device-ids")
        if listed:
            peers[circuit_id] = tuple(part.strip() for part in listed.split(","))

    tree = capture_tree(stem)
    assert set(tree.circuits()) == circuits
    assert set(hosted_circuits(tree)) == {c for c in circuits if _declares_spaces(devices[c])}
    assert set(space_less_meters(tree)) == {c for c in circuits if not _declares_spaces(devices[c])}
    assert dict(circuit_fed_batteries(tree)) == fed
    assert dict(shared_meter_peers(tree)) == peers


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_adapter_places_exactly_the_hosted_circuits_in_breaker_spaces(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    assert all(snapshot.circuits[circuit_id].tabs for circuit_id in hosted_circuits(tree))
    assert all(snapshot.circuits[meter_id].tabs == [] for meter_id in space_less_meters(tree))


@pytest.mark.parametrize("stem", CAPTURES)
def test_a_fed_batterys_circuit_says_it_feeds_a_battery(stem: str) -> None:
    tree = capture_tree(stem)
    for circuit_id in circuit_fed_batteries(tree).values():
        assert tree.value(circuit_id, "connection/feeds-device-type") == _BATTERY_TYPE


@pytest.mark.parametrize("stem", CAPTURES)
def test_shared_meter_peers_name_each_other(stem: str) -> None:
    tree = capture_tree(stem)
    peers = shared_meter_peers(tree)
    for circuit_id, listed in peers.items():
        assert circuit_id not in listed
        for peer_id in listed:
            assert peer_id in tree.circuits()
            assert circuit_id in peers.get(peer_id, ())


def test_the_captures_hold_every_shape_a_selector_finds() -> None:
    """A selector that matched nothing anywhere would leave its properties vacuous."""
    trees = [capture_tree(stem) for stem in CAPTURES]
    assert any(space_less_meters(tree) for tree in trees)
    assert any(circuit_fed_batteries(tree) for tree in trees)
    assert any(shared_meter_peers(tree) for tree in trees)


@pytest.mark.parametrize("stem", CAPTURES)
def test_withholding_a_circuits_values_keeps_it_declared(stem: str) -> None:
    """A declared device whose values have not arrived is still the tree's device."""
    circuit_id = hosted_circuits(capture_tree(stem))[0]
    assert circuit_id in capture_snapshot(stem, drop_values_of=circuit_id).circuits
    assert circuit_id in capture_adapter(stem, drop_values_of=circuit_id).circuit_nodes_missing_names()
    assert circuit_id not in capture_adapter(stem).circuit_nodes_missing_names()
