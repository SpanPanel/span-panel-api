"""The enclosure's view of the link to each circuit-fed DER.

`connection` 0.1 states the enclosure/DER relationship on the **circuit**, not
on the DER: a circuit that feeds a commissioned device publishes
`feeds-device-id` naming it and `feeds-device-status` saying how the link is.
So a PV's or a charger's link health arrives on a different device from the one
it describes, and the mapper's job is to put it back where it belongs.

**Three things make that easy to get wrong, and every test here is aimed at one
of them.**

*Absence is a value.* Two of the capture's five circuits publish no connection
record at all — the spec calls that normal for a mixed-load or unsurveyed
circuit — and the enum firmware does publish is `OK,LOST,DEGRADED`, with no
UNKNOWN member. So "nobody has said" can only be expressed by the property not
being there, and it has to stay distinct from "the link is down".

*The capture agrees with itself.* All three published records read `OK`, which
means an assertion that both chargers are connected is satisfied by a mapper
that returns a constant, one that reads the wrong circuit, and one that gives
every DER the first record it finds. Nothing below rests on the captured values:
each is read out of the tree, and every reading is proved by republishing values
that differ per DER.

*Two chargers.* The capture has two, fed by two circuits, so the wiring is
falsifiable — republish differing statuses and each charger has to report its
own.
"""

from __future__ import annotations

import json

import pytest

from reference_payloads.schema_one import (
    RetainedTopicTree,
    device_from_topics,
    parent_child_tree,
)
from span_panel_api.models import SpanEvseSnapshot, SpanPanelSnapshot
from span_panel_api_schema_1.const import NODE_CONNECTION
from span_panel_api_schema_1.devices import (
    PROP_FEEDS_DEVICE_ID,
    PROP_FEEDS_DEVICE_STATUS,
    STATUS_OK,
    feed_connection_statuses,
)
from span_panel_api_schema_1.field_metadata import build_field_metadata
from span_panel_api_schema_1.snapshot import build_snapshot

PANEL = "example-40t-001"

FEEDS_ID_TOPIC = f"{NODE_CONNECTION}/{PROP_FEEDS_DEVICE_ID}"
FEEDS_STATUS_TOPIC = f"{NODE_CONNECTION}/{PROP_FEEDS_DEVICE_STATUS}"

# The DER device ids the capture commissions. Named rather than derived so a
# capture that stopped carrying one fails saying so, instead of quietly
# reducing every test below to a smaller panel.
PV = "pv"
EVSE = "evse"
EVSE_2 = "evse-2"


def _mutable_tree() -> dict[str, dict[str, str]]:
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _snapshot(tree: RetainedTopicTree) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL, tree[PANEL])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL]
    return build_snapshot(panel, children)


def _feeding_circuit(tree: RetainedTopicTree, device_id: str) -> str:
    """The circuit the capture says feeds `device_id`, or fail saying none does."""
    feeders = [circuit_id for circuit_id, topics in tree.items() if topics.get(FEEDS_ID_TOPIC) == device_id]
    assert len(feeders) == 1, f"the capture has {len(feeders)} circuits feeding {device_id}, expected 1"
    return feeders[0]


def _evse_fed_by(snapshot: SpanPanelSnapshot, circuit_id: str) -> SpanEvseSnapshot:
    """The charger the snapshot says that circuit feeds.

    Looked up by feed rather than by snapshot key: the key is the harmonised
    serial, and a test that hardcoded one would still pass if the mapper
    attached every record to the same charger.
    """
    matches = [evse for evse in snapshot.evse.values() if evse.feed_circuit_id == circuit_id]
    assert len(matches) == 1, f"{len(matches)} chargers report circuit {circuit_id} as their feed"
    return matches[0]


def _status_options() -> list[str]:
    """The enum as the circuit's own `$description` declares it.

    Read from the wire rather than written here, because the property's legal
    values are the panel's claim and not this test's. It is also the assertion
    that there is no UNKNOWN member — which is *why* absence has to carry that
    meaning instead.
    """
    tree = parent_child_tree()
    circuit = _feeding_circuit(tree, PV)
    description = json.loads(tree[circuit]["$description"])
    node = description["nodes"][NODE_CONNECTION]["properties"][PROP_FEEDS_DEVICE_STATUS]
    assert node["datatype"] == "enum"
    return str(node["format"]).split(",")


def _not_ok() -> list[str]:
    """Every declared status that is not `OK`, in declaration order."""
    return [option for option in _status_options() if option != STATUS_OK]


def _with_status(tree: dict[str, dict[str, str]], device_id: str, status: str) -> None:
    """Republish the link status of whichever circuit feeds `device_id`."""
    tree[_feeding_circuit(tree, device_id)][FEEDS_STATUS_TOPIC] = status


# ---------------------------------------------------------------------------
# What the capture declares and publishes
# ---------------------------------------------------------------------------


def test_the_status_enum_has_no_unknown_member() -> None:
    """The premise every absence test below rests on.

    If firmware ever gains an UNKNOWN member, "unpublished means unknown" stops
    being the only way to say it and this design should be revisited — so the
    premise is asserted rather than assumed.
    """
    options = _status_options()

    assert STATUS_OK in options
    assert "UNKNOWN" not in options
    assert _not_ok(), "the enum declares nothing but OK, so no test here can observe a bad link"


def test_only_the_circuits_feeding_a_der_publish_a_connection_record() -> None:
    """The negative case is in the capture, not manufactured by a test.

    Five circuits, three of which feed a commissioned DER. The other two feed
    ordinary loads and publish neither half of the record — which
    `distribution-enclosure.md` describes as the normal state for a mixed-load
    circuit, and which is exactly the shape a mapper must not read as a fault.
    """
    tree = parent_child_tree()
    circuits = {
        device_id for device_id, topics in tree.items() if json.loads(topics["$description"])["type"].endswith(".circuit")
    }
    publishing = {device_id for device_id in circuits if FEEDS_ID_TOPIC in tree[device_id]}

    assert publishing == {_feeding_circuit(tree, der) for der in (PV, EVSE, EVSE_2)}
    silent = circuits - publishing
    assert silent, "the capture has no DER-less circuit, so the absence case is untested"
    for device_id in silent:
        declared = json.loads(tree[device_id]["$description"])["nodes"]
        assert NODE_CONNECTION in declared, (
            f"{device_id} does not even declare the node, so its silence proves nothing "
            "about a circuit that declares the record and publishes none of it"
        )
        assert not [topic for topic in tree[device_id] if topic.startswith(f"{NODE_CONNECTION}/")]


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------


def test_each_der_takes_the_link_health_of_the_circuit_that_feeds_it() -> None:
    """Every expectation computed from the capture, none of them written here."""
    tree = parent_child_tree()
    snapshot = _snapshot(tree)

    for der in (PV, EVSE, EVSE_2):
        circuit = _feeding_circuit(tree, der)
        published = tree[circuit][FEEDS_STATUS_TOPIC]
        expected = published == STATUS_OK
        reported = snapshot.pv.connected if der == PV else _evse_fed_by(snapshot, circuit).connected
        assert reported is expected, f"{der}: circuit {circuit} publishes {published!r}"


def test_two_chargers_do_not_share_one_link() -> None:
    """The cross-wiring case, and the reason two EVSE are worth the fixture.

    Both chargers read `OK` in the capture, so the baseline assertion above is
    satisfied by a mapper that hands every charger the first record it finds.
    Here they are republished differing, then swapped: a mapper keyed on the
    wrong thing gets one of the two arrangements right by luck and never both.
    """
    down, degraded = _not_ok()[0], _not_ok()[-1]

    for first, second in ((down, STATUS_OK), (STATUS_OK, down), (degraded, STATUS_OK)):
        tree = _mutable_tree()
        _with_status(tree, EVSE, first)
        _with_status(tree, EVSE_2, second)
        snapshot = _snapshot(tree)

        assert _evse_fed_by(snapshot, _feeding_circuit(tree, EVSE)).connected is (first == STATUS_OK)
        assert _evse_fed_by(snapshot, _feeding_circuit(tree, EVSE_2)).connected is (second == STATUS_OK)


def test_the_pv_link_is_not_the_chargers_link() -> None:
    """The third DER, held apart from the two chargers the same way."""
    down = _not_ok()[0]
    tree = _mutable_tree()
    _with_status(tree, PV, down)

    snapshot = _snapshot(tree)

    assert snapshot.pv.connected is False
    for evse in snapshot.evse.values():
        assert evse.connected is True


@pytest.mark.parametrize("status", _not_ok())
def test_every_status_that_is_not_ok_reads_as_a_broken_link(status: str) -> None:
    """DEGRADED is not OK, and the boolean has to say so.

    Both non-OK members are exercised, so a mapper testing `!= "LOST"` — which
    passes the LOST case and calls a degraded link healthy — fails here.
    """
    tree = _mutable_tree()
    _with_status(tree, PV, status)
    _with_status(tree, EVSE, status)

    snapshot = _snapshot(tree)

    assert snapshot.pv.connected is False
    assert _evse_fed_by(snapshot, _feeding_circuit(tree, EVSE)).connected is False


# ---------------------------------------------------------------------------
# Absence, in each of its three shapes
# ---------------------------------------------------------------------------


def test_a_circuit_that_stops_publishing_the_status_reports_unknown_not_disconnected() -> None:
    """Retained topics vanish; the reading has to vanish with them.

    `None` rather than `False`, because the enum cannot say "unknown" and a
    `False` here would tell a user their charger is unreachable on the strength
    of the panel having said nothing at all.
    """
    tree = _mutable_tree()
    del tree[_feeding_circuit(tree, PV)][FEEDS_STATUS_TOPIC]

    snapshot = _snapshot(tree)

    assert snapshot.pv.connected is None
    for evse in snapshot.evse.values():
        assert evse.connected is True, "removing one circuit's status changed another DER's reading"


def test_a_der_no_circuit_claims_is_unknown_rather_than_disconnected() -> None:
    """The unclaimed case: a status with no id names nobody.

    Half a record is not a record. A circuit still publishing `OK` while no
    longer naming the device it feeds says nothing about that device, and the
    id is what the mapper matches on.
    """
    tree = _mutable_tree()
    circuit = _feeding_circuit(tree, PV)
    del tree[circuit][FEEDS_ID_TOPIC]
    assert tree[circuit][FEEDS_STATUS_TOPIC] == STATUS_OK

    assert _snapshot(tree).pv.connected is None


def test_a_circuit_publishing_neither_half_leaves_its_der_unknown() -> None:
    """Both halves gone, which is what a decommissioned DER's circuit looks like."""
    tree = _mutable_tree()
    circuit = _feeding_circuit(tree, EVSE)
    del tree[circuit][FEEDS_ID_TOPIC]
    del tree[circuit][FEEDS_STATUS_TOPIC]

    snapshot = _snapshot(tree)

    unclaimed = [evse for evse in snapshot.evse.values() if evse.connected is None]
    assert len(unclaimed) == 1
    assert unclaimed[0].feed_circuit_id == "", "a charger with no feeding circuit still reports one"


def test_the_status_map_ignores_a_circuit_that_publishes_only_one_half() -> None:
    """The rule stated once, at the function that enforces it."""
    tree = _mutable_tree()
    solar, garage = _feeding_circuit(tree, PV), _feeding_circuit(tree, EVSE)
    del tree[solar][FEEDS_STATUS_TOPIC]
    del tree[garage][FEEDS_ID_TOPIC]

    statuses = feed_connection_statuses([device_from_topics(device_id, tree[device_id]) for device_id in (solar, garage)])

    assert statuses == {}


# ---------------------------------------------------------------------------
# The facts this must not be confused with
# ---------------------------------------------------------------------------


def test_the_charger_link_is_independent_of_whether_a_car_is_plugged_in() -> None:
    """`evse.status` is the session; `evse.connected` is the link.

    A charger reporting CHARGING over a link the enclosure has lost is the case
    that tells the two apart, and it is a state real hardware reaches — the
    charger keeps charging while the panel stops hearing from it.
    """
    down = _not_ok()[0]
    tree = _mutable_tree()
    circuit = _feeding_circuit(tree, EVSE)
    _with_status(tree, EVSE, down)

    evse = _evse_fed_by(_snapshot(tree), circuit)

    assert evse.connected is False
    assert evse.status == parent_child_tree()[EVSE]["status/status"]


def test_the_battery_link_still_comes_from_the_lugs_not_from_a_circuit() -> None:
    """The two halves of `connection` stay on their own devices.

    `battery.connected` is the upstream lugs' `fed-by-*` view. Breaking every
    circuit-side record must not touch it, or the new route has quietly taken
    over a field that was already right.
    """
    down = _not_ok()[0]
    tree = _mutable_tree()
    for der in (PV, EVSE, EVSE_2):
        _with_status(tree, der, down)

    assert _snapshot(tree).battery.connected is True


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_both_der_link_fields_take_their_type_from_the_circuit_description() -> None:
    """One property, two field paths, because one circuit's record is a PV's and
    another's is a charger's."""
    tree = parent_child_tree()
    metadata = build_field_metadata([device_from_topics(device_id, topics) for device_id, topics in tree.items()])

    for path in ("pv.connected", "evse.connected"):
        assert metadata[path].datatype == "enum"
        assert metadata[path].unit is None
        assert metadata[path].resolved is True


def test_a_circuit_declaring_the_node_without_the_property_reports_a_gap() -> None:
    """The three-way contract, on the property that now carries a row.

    Node present and property missing is a declared gap, which is what makes
    the difference between hardware that lacks the capability and firmware that
    dropped a property visible to a consumer.
    """
    tree = _mutable_tree()
    devices = []
    for device_id, topics in tree.items():
        description = json.loads(topics["$description"])
        properties = description.get("nodes", {}).get(NODE_CONNECTION, {}).get("properties")
        if properties is not None:
            properties.pop(PROP_FEEDS_DEVICE_STATUS, None)
            topics["$description"] = json.dumps(description)
        devices.append(device_from_topics(device_id, topics))

    metadata = build_field_metadata(devices)

    for path in ("pv.connected", "evse.connected"):
        assert metadata[path].resolved is False
