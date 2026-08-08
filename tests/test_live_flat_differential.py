"""Measure the frozen flat simulator against a real panel running flat firmware.

Phase 3b. The migration classification in `test_schema_migration_delta.py` uses
the frozen simulator as its flat reference, which is a *proxy* for firmware. This
measures the proxy: run `schema_0` over both a live capture and the simulator
capture and diff which `SpanPanelSnapshot` fields each populates.

- agree  → the simulator is attested for that field, not merely assumed
- differ → the panel is ground truth and the simulator is wrong

**Skips without a capture, and that is the normal state.** The capture is
gitignored — it carries the panel's serial (which is also its MQTT username), the
household's circuit names and real consumption. Take one with
`scripts/capture_live_flat.py`; what lands in the repository is this file's
verdict, never the data.

**Values are never asserted and never printed.** Only field *names* and counts
appear in output, so a failure message cannot leak a circuit name or a reading.
Population is also the only comparable thing: two panels in different houses at
different moments share no values.

What this can and cannot settle:

- **Can:** the panel and circuit rows, which are 96% of the entity surface and
  where both of the migration's real orphans live.
- **Cannot:** the four `PROVISIONAL_DER` rows. The available panel has no BESS and
  no Drives, so it is silent on exactly the fields the simulator is silent on.
  A differential between two silences proves nothing, and `test_the_live_panel_
  cannot_attest_der_identity` records that rather than letting the agreement
  count as evidence.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_0 import SchemaZeroAdapter

_FIXTURES = Path(__file__).parent / "fixtures"
_LIVE = _FIXTURES / "live_flat_wire.json"
_SIM = _FIXTURES / "flat_wire.json"

pytestmark = pytest.mark.skipif(
    not _LIVE.exists(),
    reason="no live panel capture; run scripts/capture_live_flat.py (see .env.example)",
)

DER_SCOPES = ("battery", "pv")
"""Scopes the available panel cannot speak to: it has no BESS and no Drives."""

_SENTINEL = "UNKNOWN"
"""A value that occupies a field without informing it — see the degradation test."""


def _schema(panel_size: int) -> V2HomieSchema:
    """No `data_model_version`: its absence is what marks a payload as flat."""
    return V2HomieSchema(
        firmware_version="flat",
        types_schema_hash="sha256:differential",
        types={
            "energy.ebus.device.circuit": {
                "space": {"datatype": "integer", "format": f"1:{panel_size}:1"},
            },
        },
    )


def _snapshot(path: Path) -> Any:
    capture = json.loads(path.read_text())
    serial = next(iter(capture))
    adapter = SchemaZeroAdapter(serial_number=serial, schema=_schema(40))
    for device in sorted(capture):
        for key in sorted(capture[device]):
            adapter.handle_message(f"ebus/5/{device}/{key}", capture[device][key])
    return adapter.build_snapshot()


def _populated(obj: Any) -> set[str]:
    if obj is None:
        return set()
    return {f.name for f in dataclasses.fields(obj) if getattr(obj, f.name) is not None}


@pytest.fixture(scope="module")
def live() -> Any:
    return _snapshot(_LIVE)


@pytest.fixture(scope="module")
def sim() -> Any:
    return _snapshot(_SIM)


def test_the_simulator_populates_every_panel_field_the_panel_does(live: Any, sim: Any) -> None:
    """The claim the migration classification rests on.

    A panel field the real panel publishes and the simulator does not is a field
    the classification never saw — so it could be silently orphaned by the
    migration with nothing to notice.
    """
    missing = sorted(_populated(live) - _populated(sim) - {"circuits", "battery", "pv", "evse"})

    assert not missing, (
        "the real panel populates these and the frozen simulator does not, so the "
        f"migration classification has no evidence about them: {missing}"
    )


def test_the_simulator_invents_no_panel_field_the_panel_lacks(live: Any, sim: Any) -> None:
    """The other direction, which is the subtler error.

    A field only the simulator populates makes the classification treat something
    as surviving the migration when no real panel ever had it. Held separately
    from the test above because the remedy differs: one is a simulator gap, this
    is a simulator fiction.
    """
    invented = sorted(_populated(sim) - _populated(live) - {"circuits", "battery", "pv", "evse"})

    assert not invented, (
        "the frozen simulator populates these and the real panel does not; the "
        f"classification may be treating a simulator artifact as a real entity: {invented}"
    )


def test_circuit_fields_agree_between_the_panel_and_the_simulator(live: Any, sim: Any) -> None:
    """Circuits are 96% of the entity surface, so this is the bulk of the attestation.

    Compares the *set of populated field names* per circuit, not values and not
    circuit identities — the panel's circuits are a different household's and
    their ids are not ours to compare against a fixture.
    """
    if not live.circuits or not sim.circuits:
        pytest.skip("one side published no circuits")

    live_shape = {frozenset(_populated(c)) for c in live.circuits.values()}
    sim_shape = {frozenset(_populated(c)) for c in sim.circuits.values()}

    only_live = sorted({name for shape in live_shape for name in shape} - {name for shape in sim_shape for name in shape})
    only_sim = sorted({name for shape in sim_shape for name in shape} - {name for shape in live_shape for name in shape})

    assert not only_live and not only_sim, (
        f"circuit fields the panel populates and the simulator does not: {only_live}; "
        f"fields the simulator populates and the panel does not: {only_sim}"
    )


def test_no_panel_field_answers_on_one_side_and_reads_unknown_on_the_other(live: Any, sim: Any) -> None:
    """Population alone cannot see a field degrade, which is how this was found.

    Deleting `core/door` from a capture changes nothing in the two tests above,
    because `door_state` falls back to `UNKNOWN` rather than `None` — the field
    stays "populated" while carrying no answer. So the simulator could differ from
    a real panel on any sentinel-defaulting field and the diff would report
    agreement.
    """
    ignore = {"circuits", "battery", "pv", "evse"}
    disagreeing = sorted(
        f.name
        for f in dataclasses.fields(live)
        if f.name not in ignore and (getattr(live, f.name) == _SENTINEL) != (getattr(sim, f.name, None) == _SENTINEL)
    )

    assert not disagreeing, (
        f"these read {_SENTINEL!r} on one side and carry an answer on the other, so the "
        f"simulator is not faithful for them: {disagreeing}"
    )


def test_the_live_panel_cannot_attest_der_identity(live: Any) -> None:
    """Records the limit, so agreement elsewhere is not over-read.

    The available panel has no BESS and no Drives. It is therefore silent on
    exactly the fields the simulator is silent on, and a differential between two
    silences is not evidence. If a panel with DER hardware is ever captured, this
    fails and `PROVISIONAL_DER` in the migration classification can finally be
    re-derived against something real.
    """
    attested = sorted(scope for scope in DER_SCOPES if _populated(getattr(live, scope, None)))

    assert not attested, (
        f"this panel now reports {attested}; re-derive PROVISIONAL_DER in "
        "test_schema_migration_delta.py against it instead of assuming those fields are additions"
    )
