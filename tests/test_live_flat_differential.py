"""Measure the frozen flat simulator against a real panel running flat firmware.

Phase 3b. The migration classification in `test_schema_migration_delta.py` uses
the frozen simulator as its flat reference, which is a *proxy* for firmware. This
measures the proxy, so the classification rests on something checked rather than
something assumed.

**Compares published property sets, not snapshot values.** The first draft diffed
`SpanPanelSnapshot` fields and produced three findings that were all artefacts of
the question rather than the answer: `grid_state` differed because flat sources it
from `bess/grid-state` and the panel has no BESS; `door_state` and `vendor_cloud`
differed because both sides publish them and two panels in different houses are
simply in different states. None of that is infidelity.

Fidelity is *does the simulator publish the same properties firmware does*, per
device class, for the classes both have. That question is stable across houses,
across time, and across which DER hardware is installed.

**Skips without a capture, and that is the normal state.** The capture is
gitignored — it carries the panel's serial (which is also its MQTT username), the
household's circuit names and real consumption. Take one with
`scripts/capture_live_flat.py`; what lands in the repository is this file's
verdict, never the data. Property *names* are asserted and printed; values never
are, so a failure cannot leak a circuit name or a reading.

The verdict as of 2026-08-08: the simulator is faithful for `core`, both `lugs`
and circuits — identical property sets — with exactly one gap, `pv/product-name`.
That gap corrected a real misclassification in Phase 3a, which is what this was
built to do.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"
_LIVE = _FIXTURES / "live_flat_wire.json"
_SIM = _FIXTURES / "flat_wire.json"

pytestmark = pytest.mark.skipif(
    not _LIVE.exists(),
    reason="no live panel capture; run scripts/capture_live_flat.py (see .env.example)",
)

_UUID = re.compile(r"^[0-9a-f]{32}$")

SHARED_PREFIXES = ("core", "pv", "lugs-upstream", "lugs-downstream")
"""Single-instance device classes present on both the panel and the simulator."""

KNOWN_GAPS: dict[str, tuple[str, ...]] = {
    "pv": ("product-name",),
}
"""Properties real firmware publishes that the frozen simulator does not.

One entry, and it earned its keep immediately: Phase 3a classified
`pv.product_name` as a v1.0 *addition* purely because the simulator never sent it.
Real firmware does, so it is an identity — the entity exists today and survives.
`PROVISIONAL_DER` shrank accordingly.

The flat simulator is frozen, so this is a permanent gap to compensate for rather
than a bug to file.
"""


def _body(path: Path) -> dict[str, str]:
    capture = json.loads(path.read_text())
    return capture[next(iter(capture))]


@pytest.fixture(scope="module")
def live() -> dict[str, str]:
    return _body(_LIVE)


@pytest.fixture(scope="module")
def sim() -> dict[str, str]:
    return _body(_SIM)


def _properties(body: dict[str, str], prefix: str) -> set[str]:
    return {key.split("/", 1)[1] for key in body if key.startswith(f"{prefix}/")}


def _circuit_properties(body: dict[str, str]) -> set[str]:
    ids = {key.split("/")[0] for key in body if _UUID.match(key.split("/")[0])}
    return {key.split("/", 1)[1] for key in body if key.split("/")[0] in ids}


@pytest.mark.parametrize("prefix", SHARED_PREFIXES)
def test_the_simulator_publishes_what_firmware_publishes(prefix: str, live: dict[str, str], sim: dict[str, str]) -> None:
    """Per device class, both directions, with the one known gap allowed.

    A property firmware sends and the simulator does not means the migration
    classification never saw it — `pv/product-name` is exactly that, and it was
    misclassified as an addition until this measured it. A property the simulator
    sends and firmware does not would be worse: the classification would be
    reasoning about an entity nobody has.
    """
    panel, simulated = _properties(live, prefix), _properties(sim, prefix)
    if not panel and not simulated:
        pytest.skip(f"neither side publishes {prefix}")

    missing = sorted(panel - simulated - set(KNOWN_GAPS.get(prefix, ())))
    invented = sorted(simulated - panel)

    assert not missing, (
        f"firmware publishes {prefix} properties the frozen simulator does not: {missing}. "
        "The migration classification has no evidence about them; add them to KNOWN_GAPS "
        "and check whether Phase 3a misclassified anything as an addition."
    )
    assert not invented, (
        f"the simulator publishes {prefix} properties firmware does not: {invented}. "
        "The classification may be reasoning about an entity no real panel has."
    )


def test_circuit_properties_are_identical(live: dict[str, str], sim: dict[str, str]) -> None:
    """Circuits are 96% of the entity surface, so this is the bulk of the attestation.

    Property names only. Circuit *ids* are not compared — the panel's are a
    different household's — and neither are values.
    """
    panel, simulated = _circuit_properties(live), _circuit_properties(sim)
    assert panel and simulated, "one side published no circuits"

    assert panel == simulated, (
        f"circuit properties firmware publishes and the simulator does not: {sorted(panel - simulated)}; "
        f"the reverse: {sorted(simulated - panel)}"
    )


def test_the_known_gap_is_still_exactly_one(live: dict[str, str], sim: dict[str, str]) -> None:
    """`KNOWN_GAPS` relaxes the check above, so it has to stay earned.

    Fails in both directions: a gap that closed should be deleted so the list keeps
    meaning something, and a gap that never existed should never have been added.
    """
    stale = {
        prefix: sorted(name for name in names if name in _properties(sim, prefix)) for prefix, names in KNOWN_GAPS.items()
    }
    still_gaps = {prefix: names for prefix, names in stale.items() if names}

    assert not still_gaps, f"the simulator now publishes these, so they are no longer gaps: {still_gaps}"


def test_which_der_hardware_this_panel_can_attest(live: dict[str, str]) -> None:
    """Records what the available panel does and does not settle.

    It has PV and no BESS, so it attests the `pv` rows of `PROVISIONAL_DER` and is
    silent on the `battery` ones — and a differential between two silences is not
    evidence. Pinned so that capturing a panel with a BESS fails here and prompts
    re-deriving that set against something real.
    """
    has_bess = any(key.startswith("bess/") for key in live)
    has_pv = any(key.startswith("pv/") for key in live)

    assert has_pv, "this panel no longer reports PV; the pv attestation in KNOWN_GAPS rests on it"
    assert not has_bess, (
        "this panel now reports a BESS. Re-derive PROVISIONAL_DER in "
        "test_schema_migration_delta.py against it — the battery rows have never been "
        "measured against real firmware."
    )
