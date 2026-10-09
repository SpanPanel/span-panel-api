"""What is right for the reference-capture cells, and the code that makes each entry so.

The transforms and the drop reasons are the ones every cell shares
(`rules.TRANSFORMS`, `rules.DROPS`): a transform states what the library does,
whichever capture shows it. The derivations are only the ones these captures
need, as `test_every_derivation_is_needed` holds them to, so they are chosen
here rather than inherited.

Four of them are exceptions rather than derivations, and say so: the snapshot
holds `"UNKNOWN"` in a `str` field where the panel publishes nothing for it.
That is the field's long-standing absence value, not a reading; whether those
fields become optional is a type question deferred past 3.8.0b1.
"""

from __future__ import annotations

from typing import Final

from .harness import CellRules, Derivation
from .rules import DROPS, MAIN32_RULES, TRANSFORMS

_SHARED: Final = frozenset(
    {
        "battery.present",
        "circuit.circuit_id",
        "circuit.device_type",
        "circuit.is_never_backup",
        "circuit.is_sheddable",
        "circuit.measures_outside_panel",
        "ctl.dps_target",
        "ctl.priority_target",
        "panel.publishes_solar_roles",
    }
)
"""The MAIN 32 derivations these captures need as well."""

_NO_RELAY = (
    "circuits.build_circuit: a device that declares no switch node has no relay, "
    "so it is not controllable, not sheddable and not always on"
)
_STR_SENTINEL = "pre-existing str sentinel; type question deferred past 3.8.0b1"

_DERIVATIONS: Final = (
    *(derivation for derivation in MAIN32_RULES.derivations if derivation.family in _SHARED),
    Derivation("circuit.is_user_controllable", _NO_RELAY, values=(False,)),
    Derivation("circuit.is_sheddable", _NO_RELAY, values=(False,)),
    Derivation("circuit.always_on", _NO_RELAY, values=(False,)),
    Derivation(
        "panel.dominant_power_source",
        "panel.resolve_dominant_power_source: with no MID, GRID is an elimination, since nothing else can form the grid",
        values=("GRID",),
    ),
    Derivation(
        "circuit.relay_state",
        f"{_STR_SENTINEL}: circuits.build_circuit reads UNKNOWN where the device publishes no switch/relay",
        values=("UNKNOWN",),
    ),
    Derivation(
        "circuit.relay_requester",
        f"{_STR_SENTINEL}: circuits.build_circuit reads UNKNOWN where the device publishes no switch/relay-requester",
        values=("UNKNOWN",),
    ),
    Derivation(
        "circuit.priority",
        f"{_STR_SENTINEL}: circuits.build_circuit reads UNKNOWN where the device publishes no load-shed/priority",
        values=("UNKNOWN",),
    ),
    Derivation(
        "panel.main_relay_state",
        f"{_STR_SENTINEL}: panel.PanelFields reads UNKNOWN while status/relay is unpublished",
        values=("UNKNOWN",),
    ),
)

CAPTURED_RULES: Final = CellRules(transforms=TRANSFORMS, derivations=_DERIVATIONS, drops=DROPS, deferred={})
