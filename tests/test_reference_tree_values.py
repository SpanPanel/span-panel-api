"""What the reference tree leaves unvalued must be what the producer leaves unvalued.

`parent_child_tree.json` is the fixture every schema_1 test is written against, so what it *publishes* is the whole evidence base for
"does this adapter read that property". A property the producer values and this
capture does not is therefore invisible in both directions at once: no test can
fail for not reading it, and no consumer test can fail for not surfacing it.

That is not hypothetical. The capture was trimmed and renamed by hand from a
panelbench run, and by 2026-08-19 it had drifted eight properties behind — MID
`info/{model,serial-number,firmware-version,hardware-version}`, BESS
`info/{part-number,serial-number,firmware-version}` and PV
`info/firmware-version` were all published by the producer and absent here. Four
library tests had been written to inject the values by hand precisely because
the fixture did not carry them, which reads as coverage and is not: injecting a
value asks whether the mapper can read a property, never whether the panel sends
one. The drift was found by comparing the two artifacts by hand, which is a
thing nobody does twice — hence this.

**Compared at device-type granularity, and it has to be.** The two artifacts
describe different panels: this one is a five-circuit synthetic enclosure with
`example-*` identifiers, panelbench's is a twenty-eight-circuit one, so a
per-device comparison would fail on the names rather than on a value. Type
granularity is also the granularity the question is asked at: five circuits
declare the same properties, and the same one going unvalued on all five is one
gap, not five. The same choice the integration's `test_declared_but_unread`
makes, for the same reason.

The reduction loses nothing here, and that is measured rather than assumed:
reduced the same way, panelbench's baseline is exactly the declared-but-unvalued
set of its own committed wire capture (`tests/conformance/fixtures/golden_wire.json`).

Refresh the vendored copy with:

    cp ../panelbench/tests/fidelity/fixtures/unvalued_by_both_baseline.json \
       tests/fixtures/panelbench_unvalued_by_both.json

It is vendored verbatim rather than pre-reduced so that refreshing it is a copy
whose correctness a reader can check with `diff`, and so the reduction stays
here where it is explained.

**A failure here does not say which side moved, and both have.** Refreshing the
vendored baseline is the fix when panelbench has already re-captured, which was
the case the first time this fired: the copy carried 32 `connection/count`
entries that the pinned panelbench commit had itself already dropped, so the two
artifacts agreed only because both were stale. Regenerating the reference tree
is the fix when the producer this side follows has moved -- see
`scripts/capture_parent_child_reference.py`, which reproduces every identifier
and every device in this capture, so the old instruction to port values in by
hand rather than recapture no longer applies.
"""

from __future__ import annotations

from collections import defaultdict
import json
import pathlib

from reference_payloads.schema_one import parent_child_tree

_PANELBENCH_BASELINE = pathlib.Path(__file__).parent / "fixtures" / "panelbench_unvalued_by_both.json"


def _device_type(qualified: str) -> str:
    """`energy.ebus.device.circuit` -> `circuit`."""
    return qualified.rsplit(".", 1)[-1]


def _fixture_unvalued() -> dict[str, set[str]]:
    """Every `node/property` this capture declares and never publishes, by device type."""
    unvalued: dict[str, set[str]] = defaultdict(set)
    for topics in parent_child_tree().values():
        description = json.loads(topics["$description"])
        declared = {
            f"{node_id}/{property_id}"
            for node_id, node in description.get("nodes", {}).items()
            for property_id in node.get("properties", {})
        }
        published = {topic for topic in topics if not topic.startswith("$")}
        unvalued[_device_type(description["type"])] |= declared - published
    return {device_type: topics for device_type, topics in unvalued.items() if topics}


def _panelbench_unvalued() -> dict[str, set[str]]:
    """Panelbench's baseline, reduced the same way.

    Its lines are `{device type}::{device name}  {node}/{property}`; the name is
    what the trim and the rename make uncomparable, so it is what the reduction
    drops.
    """
    unvalued: dict[str, set[str]] = defaultdict(set)
    for line in json.loads(_PANELBENCH_BASELINE.read_text(encoding="utf-8")):
        identity, _, topic = line.partition("  ")
        unvalued[_device_type(identity.split("::", 1)[0])].add(topic)
    return dict(unvalued)


def test_the_reference_tree_values_everything_the_producer_values() -> None:
    """Fails in both directions, so neither drift nor a stale baseline survives.

    A property the producer starts valuing and this capture does not fails as a
    gap in the evidence base. A property this capture values that the producer
    does not fails too: the capture would be asserting a value nothing on the
    wire produces, which is a fixture that tests the parser against fiction.
    """
    fixture = _fixture_unvalued()
    producer = _panelbench_unvalued()

    missing = {
        device_type: sorted(topics - producer.get(device_type, set()))
        for device_type, topics in fixture.items()
        if topics - producer.get(device_type, set())
    }
    invented = {
        device_type: sorted(topics - fixture.get(device_type, set()))
        for device_type, topics in producer.items()
        if topics - fixture.get(device_type, set())
    }

    assert fixture == producer, (
        "the reference tree and the producer disagree about what stays unvalued.\n"
        f"  unvalued here, valued by the producer (this capture is behind):\n    {missing}\n"
        f"  unvalued by the producer, valued here (the baseline may be behind):\n    {invented}\n\n"
        "Decide which side moved: refresh tests/fixtures/panelbench_unvalued_by_both.json from "
        "the panelbench commit spec_lock.json pins, or recapture the reference tree with "
        "scripts/capture_parent_child_reference.py."
    )


def test_the_held_pv_serial_is_still_the_only_singleton_left() -> None:
    """PV `info/serial-number` is unvalued on purpose, and must stay that way.

    `_der_identifier` prefers a serial over the instance id, so valuing it moves
    the PV device id from `<panel>-pv-1` to `<panel>-<serial>`. A consumer keys
    its device registry on that id, which turns an upgrade rehearsal into a
    device-replacement rehearsal. Pinned separately from the set comparison
    above because that one would go on passing if both sides gained the value
    together, and this is the one line where agreeing would be the mistake.
    """
    assert _fixture_unvalued()["pv"] == {"info/serial-number"}


def test_every_property_the_producer_values_on_the_panel_is_valued_here() -> None:
    """The enclosure carries no unvalued declaration at all, and that is the point.

    `status/wifi-ssid` was the last one, and it is the property whose absence
    hid a flat -> v1.0 regression: nothing read it because nothing published it,
    and nothing published it because the capture had not been refreshed. An
    empty set here is a measurement, and this test is what keeps it one.
    """
    assert "distribution-enclosure" not in _fixture_unvalued()
