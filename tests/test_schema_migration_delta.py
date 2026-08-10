"""Phase 3: what happens to a user's entities when firmware moves flat → v1.0.

The acceptance criterion is that a user upgrades and **nothing in their Home
Assistant changes**. This produces the classification mechanically rather than by
argument: drive both adapters over a capture of the *same logical panel*, and
diff which `SpanPanelSnapshot` fields each populates.

Same panel is not a claim, it is checked below — serial `sim-40t-001`, 30
configured circuits, and every circuit UUID identical across both captures.
That last one is the load-bearing fact for entity survival: `unique_id` is
circuit-UUID-derived, so identical UUIDs mean the registry keeps the same
`entity_id`, which means `statistic_id` is unchanged and long-term history
survives.

**Population, not values.** The two captures are different runs of different
simulators, so values cannot match and asserting them would be noise. What
matters is whether a field a user has today still arrives tomorrow.

Three buckets:

- **identity** — populated on both sides. The entity survives unremarked.
- **addition** — v1.0 only. New; a product decision about whether to surface it,
  never a migration risk.
- **orphan** — flat only. **The dangerous bucket.** An entity that exists today
  and stops updating, which HA shows as stale rather than gone.

An orphan not on `EXPECTED_ORPHANS` fails. That is the whole point: the list is
short, every member is a decision someone made on purpose, and anything else is a
regression that reached a user.

---

**What this cannot tell you, which matters as much as what it can.**

The flat side is the frozen simulator, a proxy for flat firmware rather than
firmware itself. The gap is narrower than "DER is unverified", and worth stating
precisely, because the two halves have very different support.

*Telemetry is attested.* The simulator models the BESS and the Drives, and the
integration renders their entities correctly against it — which is real evidence
for `soc`, `soe`, `connected`, `nameplate-capacity`, `relative-position` and the
EVSE surface, all of which it publishes.

*Identity is not published at all*, so nothing can attest it:

| device | identity keys the flat simulator publishes |
| --- | --- |
| panel | `model`, `serial-number`, `software-version` |
| BESS | **none** |
| PV | `vendor-name` only |
| EVSE | full |

`PROVISIONAL_DER` is exactly that unpublished set — not a hedge across DER
generally. Some members are probably misclassified, but **in the benign
direction**, and the reason is worth understanding because it generalises.

A user does not see which property a value came from; they see the value. So the
adapter is free to re-source a field as long as the *meaning* survives, and for
BESS identity it deliberately does:

    info/part-number  ->  battery.model         (the SKU stays in `model`)
    info/model        ->  battery.product_name  (the designation gets a new field)

Flat firmware publishes `bess/model` as the SKU. v1.0's `battery.model` is also
the SKU, by that mapping. So on a flat capture that carried BESS identity,
`battery.model` would reclassify as **identity** — not as a semantic change — and
`battery.serial_number` likewise. Only the two `product_name` fields look like
genuine additions, because the designation had no flat home at all.

The general point: a re-sourced field is a migration risk only when the mapper
passes the change through. Where it absorbs the change, the delta is real in the
wire and invisible in the entity, which is the outcome §1 is asking for.

Absorbing everything would be the wrong reading, though, and this harness should
not be mistaken for an argument to. Absorption protects stability, not value, and
v1.0 carries more data than flat did. The risk of changing a field scales with how
likely something compares it — state and telemetry drive automations and
statistics, metadata renders on a device card and essentially nothing hinges on
it. And adding a field is not the same act as changing one: a new field cannot
break an automation that never referenced it.

So `battery.product_name` is a free win rather than a hazard, and the delta
document treats "keep the SKU in `battery.model`" as a product call worth
revisiting rather than a default. `EXPECTED_ORPHANS` and `PROVISIONAL_DER` are
about entities that would *stop* arriving; nothing here argues against surfacing
new ones.

A live flat panel cannot settle these either: the one available has no BESS and
no Drives. It would attest the panel and circuit rows, which is where the two
real orphans are.

Circuits are 96% of the entity surface and are attested. That is the useful half,
and it is clean.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_0 import SchemaZeroAdapter
from span_panel_api_schema_1 import SchemaOneAdapter

_FIXTURES = Path(__file__).parent / "fixtures"
_FLAT = _FIXTURES / "flat_wire.json"
_PC = Path(__file__).parent.parent / "packages" / "schema-1" / "spec" / "fixtures" / "simulator_wire.json"
_SERIAL = "sim-40t-001"

EXPECTED_ORPHANS: dict[str, str] = {
    "panel.dominant_power_source": (
        "split upstream into grid/grid-forming-entity and shed/asserted-islanding-state, "
        "which are different controls on different devices; which successor is exposed, "
        "if any, is an open product decision"
    ),
    "panel.grid_islandable": (
        "no v1.0 source; the flat panel advertised islandability as a panel property and "
        "the redesign expresses it through the presence of a MID instead"
    ),
    "pv.relative_position": (
        "flat publishes pv/relative-position; schema_1 does not map the v1.0 equivalent yet. "
        "Unlike the two above this is a gap rather than a decision, and closing it is cheap"
    ),
}

EXPECTED_DEGRADED: dict[str, str] = {
    "panel.dsm_state": (
        "reads UNKNOWN on v1.0 where flat answered. Its authoritative input survives as "
        "the MID's grid/islanding-state and its fallback as grid power, so the UNKNOWN is "
        "more conservative than the data requires — reconstruction is an open item"
    ),
    "panel.current_run_config": (
        "reads UNKNOWN on v1.0 where flat answered; no v1.0 source identified yet. Severity 2 in the delta document"
    ),
}
"""Fields that survive the migration as entities but stop carrying an answer.

Worse for a user than an orphan, because an orphan goes stale and is noticeable
while `UNKNOWN` reads as a working sensor that does not know. Both members are
already documented; the test exists so a third cannot appear quietly.
"""

ATTESTED_AGAINST_FIRMWARE: dict[str, str] = {
    "pv.product_name": (
        "classified an addition here only because the frozen simulator never sends "
        "pv/product-name. A capture from real flat firmware does send it, so this is an "
        "IDENTITY — the entity exists today and survives the migration. Measured by "
        "test_live_flat_differential.py; the simulator gap is recorded there as KNOWN_GAPS"
    ),
}
"""Rows the mechanical diff gets wrong, corrected by a capture from real firmware.

The classification can only see what its flat reference sends, so a simulator gap
reads as a v1.0 addition. This is where Phase 3b pays for itself: one row moved
from *addition* to *identity* on evidence, and it moved in the direction that
matters — a field we thought was new turns out to be one users already have.
"""

PROVISIONAL_DER: frozenset[str] = frozenset(
    {
        "battery.model",
        "battery.product_name",
        "battery.serial_number",
    }
)
"""Additions that may not be additions, because the flat reference never sends them.

Each is classified `addition` only because the frozen flat simulator publishes no
BESS identity and no PV identity beyond `vendor-name`. This is narrower than "DER
is unverified": the simulator models both devices and the integration renders
their telemetry correctly against it, so `soc`, `soe`, `connected` and the rest
are attested. These four are the fields nothing sends and therefore nothing can
vouch for.

Expect this set to shrink toward **identity**, not toward semantic change. The
mapper re-sources `battery.model` from `info/part-number`, which is the SKU that
flat's `bess/model` also carried, so a flat capture with BESS identity would move
`battery.model` and `battery.serial_number` into the identity bucket. The two
`product_name` entries are likely genuine additions: the designation had no flat
home.

Resolving this needs a capture from flat firmware with a BESS attached, which no
available panel has.
"""


def _flat_schema(panel_size: int = 40) -> V2HomieSchema:
    """No `data_model_version`: its absence is what marks a payload as flat."""
    return V2HomieSchema(
        firmware_version="spanos2/r202627/01",
        types_schema_hash="sha256:flat-capture",
        types={
            "energy.ebus.device.circuit": {
                "space": {"datatype": "integer", "format": f"1:{panel_size}:1"},
            },
        },
    )


def _pc_schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:pc-capture",
        types={},
        data_model_version="1.0",
    )


def _feed(adapter: Any, capture_path: Path) -> Any:
    """Replay a capture the way the retained store does: sorted, one at a time."""
    capture = json.loads(capture_path.read_text())
    for device in sorted(capture):
        for key in sorted(capture[device]):
            adapter.handle_message(f"ebus/5/{device}/{key}", capture[device][key])
    return adapter


@pytest.fixture(scope="module")
def flat() -> Any:
    return _feed(SchemaZeroAdapter(serial_number=_SERIAL, schema=_flat_schema()), _FLAT).build_snapshot()


@pytest.fixture(scope="module")
def parent_child() -> Any:
    return _feed(SchemaOneAdapter(serial_number=_SERIAL, schema=_pc_schema()), _PC).build_snapshot()


_SENTINEL = "UNKNOWN"
"""A value that occupies a field without informing it.

Found by falsifying the differential in `test_live_flat_differential.py`: deleting
`core/door` from a capture changed nothing, because `door_state` falls back to
`UNKNOWN` rather than to `None`. A population diff cannot see a field degrade that
way, so a field that stopped being published would classify as *identity* — the
safest bucket — while a user sees a permanently useless entity.
"""


def _populated(obj: Any) -> set[str]:
    if obj is None:
        return set()
    return {f.name for f in dataclasses.fields(obj) if getattr(obj, f.name) is not None}


def _degraded(before: Any, after: Any) -> set[str]:
    """Fields carrying a real value on flat and only a sentinel on v1.0.

    A fourth bucket rather than folded into orphans, because `UNKNOWN` is a legal
    state for several of these enums. What makes it a delta is the *transition*:
    the flat panel answered and the v1.0 panel does not.
    """
    if before is None or after is None:
        return set()
    return {
        f.name
        for f in dataclasses.fields(after)
        if getattr(after, f.name) == _SENTINEL and getattr(before, f.name, None) not in (None, _SENTINEL)
    }


def _classify(scope: str, flat_obj: Any, pc_obj: Any) -> tuple[set[str], set[str]]:
    """Returns (additions, orphans) as dotted `scope.field` names."""
    before, after = _populated(flat_obj), _populated(pc_obj)
    return (
        {f"{scope}.{name}" for name in after - before},
        {f"{scope}.{name}" for name in before - after},
    )


def test_both_captures_describe_the_same_logical_panel(flat: Any, parent_child: Any) -> None:
    """The premise. Without it every difference below is ambiguous between a
    migration delta and two simulators being configured differently."""
    assert flat.serial_number == parent_child.serial_number == _SERIAL
    assert len(flat.circuits) == len(parent_child.circuits)
    # Count, not keys. The EVSE keys legitimately differ across the migration —
    # that is a delta, not a configuration difference, and asserting sameness here
    # would put a real finding in the premise where it reads as a broken fixture.
    # `test_evse_identity_does_not_survive_the_migration` holds it instead.
    assert len(flat.evse) == len(parent_child.evse)


def test_every_circuit_keeps_its_identity_across_the_migration(flat: Any, parent_child: Any) -> None:
    """The single fact that decides whether history survives.

    `unique_id` is circuit-UUID-derived, so identical UUIDs on both sides mean the
    registry keeps the same `entity_id`, `statistic_id` is unchanged, and
    long-term statistics stay continuous. A UUID that moved would orphan a
    circuit's entire history — 32 circuits' worth, silently.
    """
    assert set(flat.circuits) == set(
        parent_child.circuits
    ), "circuit identities diverge across the migration; every non-matching circuit loses its recorder history"


def test_evse_identity_survives_the_migration(flat: Any, parent_child: Any) -> None:
    """The circuit test's answer, for the other device class that carries an identity.

    An EVSE entity's `unique_id` and its device-registry `identifiers` are both built
    from what this library hands over -- the snapshot key and `node_id` -- so if those
    move between schemas, a user's charger orphans and a duplicate appears beside it.

    **The comparison is against firmware, not against the flat simulator.** On a real
    panel the EVSE node id *is* the Drive's serial: SpanPanel/span#214 has the topic
    `ebus/5/<panel-serial>/<drive-serial>`, diagnostics keyed
    `"evse": {"dt-2302-c1km3": ...}`, and a maintainer confirming that node id is what
    the `unique_id` is built from. The frozen flat simulator instead names its nodes
    `evse` / `evse-2`, positional slots no panel publishes -- so `set(flat.evse)` is
    the wrong thing to assert against, and asserting it is what previously produced an
    elaborate reconstruction of a naming scheme that does not exist.

    So flat's *serials* stand in for flat's keys, which is what firmware would have
    published. The simulator gap is recorded in the delta document.
    """
    flat_identity = {evse.serial_number for evse in flat.evse.values()}
    assert flat_identity == {None} or None not in flat_identity, "a flat EVSE published no serial to key on"

    assert set(parent_child.evse) == flat_identity, (
        "v1.0 EVSE keys do not match the serials flat publishes. On real firmware the "
        "flat node id is that serial, so a mismatch here is a charger that orphans its "
        "history and returns as a new device."
    )

    for key, evse in parent_child.evse.items():
        assert evse.node_id == key, (
            f"{key}: node_id drives the device-registry identifier and must match the " f"snapshot key, got {evse.node_id!r}"
        )

    assert {evse.feed_circuit_id for evse in flat.evse.values()} == {
        evse.feed_circuit_id for evse in parent_child.evse.values()
    }, "the two captures feed their EVSEs from different circuits, so they are not the same panel"


def test_no_circuit_field_is_orphaned(flat: Any, parent_child: Any) -> None:
    """Circuits are 96% of the entity surface and the attested part of the flat
    reference, so this is the strongest claim the harness can make."""
    orphans: set[str] = set()
    for circuit_id in sorted(set(flat.circuits) & set(parent_child.circuits)):
        _, found = _classify("circuit", flat.circuits[circuit_id], parent_child.circuits[circuit_id])
        orphans |= found

    assert not orphans, f"circuit fields that stop being published after the migration: {sorted(orphans)}"


def test_every_orphan_is_a_decision_someone_made(flat: Any, parent_child: Any) -> None:
    """Phase 3's exit criterion: zero unclassified orphans.

    An unexpected entry here is a user-visible regression — an entity that exists
    today, keeps its name, and stops updating.
    """
    orphans: set[str] = set()
    for scope, before, after in (
        ("panel", flat, parent_child),
        ("battery", flat.battery, parent_child.battery),
        ("pv", flat.pv, parent_child.pv),
    ):
        _, found = _classify(scope, before, after)
        orphans |= found

    unexplained = sorted(orphans - set(EXPECTED_ORPHANS))
    assert (
        not unexplained
    ), "these fields are populated on flat and absent on v1.0, and nobody decided that:\n  " + "\n  ".join(unexplained)

    stale = sorted(set(EXPECTED_ORPHANS) - orphans)
    assert (
        not stale
    ), f"these are recorded as orphans but no longer are; delete them so the list keeps meaning something: {stale}"


def test_every_degraded_field_is_a_known_one(flat: Any, parent_child: Any) -> None:
    """Fields that survive as entities but stop carrying an answer.

    Invisible to the population diff above — the entity exists and holds a string,
    so nothing looks wrong — which is why this is separate. To a user it is worse
    than an orphan: an orphan goes stale and is noticeable, while `UNKNOWN` looks
    like a working sensor reporting that it does not know.
    """
    degraded: set[str] = set()
    for scope, before, after in (
        ("panel", flat, parent_child),
        ("battery", flat.battery, parent_child.battery),
        ("pv", flat.pv, parent_child.pv),
    ):
        degraded |= {f"{scope}.{name}" for name in _degraded(before, after)}

    assert degraded == set(EXPECTED_DEGRADED), (
        f"the set of fields that answer on flat and read {_SENTINEL!r} on v1.0 moved: "
        f"{sorted(degraded)}. Both known members have a reconstruction recorded in the "
        "delta document; a new one is a regression."
    )


def test_der_additions_are_provisional_or_attested_but_never_unexamined(flat: Any, parent_child: Any) -> None:
    """Every DER addition is accounted for as one of exactly two things.

    Either the flat reference cannot vouch for it (`PROVISIONAL_DER`, because the
    frozen simulator never sends BESS identity), or a capture from real firmware
    has settled it (`ATTESTED_AGAINST_FIRMWARE`, which is how `pv.product_name`
    turned out to be an identity users already have rather than something new).

    A third option — an addition in neither list — means one appeared and nobody
    asked which it was.
    """
    additions: set[str] = set()
    for scope, before, after in (
        ("battery", flat.battery, parent_child.battery),
        ("pv", flat.pv, parent_child.pv),
    ):
        found, _ = _classify(scope, before, after)
        additions |= found

    accounted = set(PROVISIONAL_DER) | set(ATTESTED_AGAINST_FIRMWARE)
    assert additions == accounted, (
        f"the DER addition set moved: {sorted(additions)}. Each member is either a field "
        "the frozen simulator cannot vouch for or one real firmware has settled; a new one "
        "needs deciding which, because 'addition' is the bucket that hides a surviving entity."
    )


def test_the_flat_reference_publishes_no_bess_identity() -> None:
    """Why the set above is provisional, asserted rather than described.

    Reads the capture directly. If the flat simulator ever gains BESS identity,
    this fails and the provisional set can be re-derived against something real.
    """
    body = json.loads(_FLAT.read_text())[_SERIAL]
    identity = sorted(
        key
        for key in body
        if key.startswith("bess/") and key.split("/", 1)[1] in {"model", "product-name", "serial-number", "software-version"}
    )

    assert not identity, (
        f"the flat simulator now publishes BESS identity ({identity}); re-derive "
        "PROVISIONAL_DER against it instead of assuming those fields are additions"
    )
