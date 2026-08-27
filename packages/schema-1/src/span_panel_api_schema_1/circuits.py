"""Map a v1.0 circuit device onto ``SpanCircuitSnapshot``.

The snapshot's field names come from the v1 REST API and are preserved so the
integration's entities do not move. Three of them no longer have a property to
read, because v1.0 consolidated four flat mechanisms into two. Their
derivations are defined by the migration guide, not invented here:

======================  ===========================================================
Flat property           v1.0 source
======================  ===========================================================
``always-on``           ``switch/relay-controllable``, inverted
``never-backup``        ``$settable`` on ``load-shed/priority``, inverted
``sheddable``           computed: ``priority != NEVER and relay-controllable``
======================  ===========================================================

Sign and direction are unchanged from the flat schema, and both are the reverse
of what the property names suggest. Values are in the enclosure's reference
frame: a normal load reads **negative** ``active-power`` and accumulates
``exported-energy`` (the panel exported it *to* the circuit). The snapshot
reports consumption as positive, so power is negated and the two energy
accumulators are swapped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api.models import SpanCircuitSnapshot
from span_panel_api_schema_1.const import (
    NODE_BREAKER,
    NODE_INFO,
    NODE_LOAD_SHED,
    NODE_METER,
    NODE_PCS,
    NODE_SWITCH,
    PRIORITY_NEVER,
    PROP_ACTIVE_POWER,
    PROP_CURRENT,
    PROP_EXPORTED_ENERGY,
    PROP_IMPORTED_ENERGY,
    PROP_MANAGED,
    PROP_NAME,
    PROP_POLES,
    PROP_PRIORITY,
    PROP_RATING,
    PROP_RELAY,
    PROP_RELAY_CONTROLLABLE,
    PROP_RELAY_REQUESTER,
    PROP_SPACES,
    UNKNOWN,
)
from span_panel_api_schema_1.description import declared_settable, node_properties

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice


def _text(device: DiscoveredDevice, node: str, prop: str, default: str = "") -> str:
    value = device.get_property(node, prop)
    return default if value is None else str(value)


def _number(device: DiscoveredDevice, node: str, prop: str) -> float | None:
    """Read a numeric property, or None when it is absent or unparseable.

    Unparseable is treated as absent rather than as an error: a single
    malformed value must not take down a whole snapshot, and the field it
    feeds is optional.
    """
    raw = device.get_property(node, prop)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _flag(device: DiscoveredDevice, node: str, prop: str, *, default: bool) -> bool:
    """Read a Homie boolean. Absent means `default`, which is not always False.

    `relay-controllable` absent has to mean *controllable*, because the
    property exists to mark the exception (an always-on circuit). Defaulting it
    to False would silently make every circuit uncontrollable on a panel that
    omits it.
    """
    raw = device.get_property(node, prop)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() == "true"


def _optional_flag(device: DiscoveredDevice, node: str, prop: str) -> bool | None:
    """A boolean that distinguishes "published false" from "not published".

    `_flag` above collapses the two onto a caller-chosen default, which is right
    for the relay properties where absence has a defined meaning. It is wrong
    for `pcs/managed`, which the capability marks `MAY`: a circuit that says
    nothing about PCS participation has not said it is unmanaged, and reporting
    `False` would put that claim on a dashboard.
    """
    raw = device.get_property(node, prop)
    if raw is None or raw == "":
        return None
    return str(raw).strip().lower() == "true"


def _optional_integer(device: DiscoveredDevice, node: str, prop: str) -> int | None:
    """An `integer` property, or `None` when it is absent or unparseable.

    Parsed through `_number` for the reason `panel.integer` gives: a publisher
    sending `1.0` for an integer property has made a formatting choice, not
    withheld a reading.
    """
    raw = _number(device, node, prop)
    return None if raw is None else int(raw)


def _tabs(device: DiscoveredDevice) -> list[int]:
    """Breaker spaces from ``info/spaces``.

    v1.0 publishes the occupied spaces literally (``"36,38"``), where the flat
    schema published one space plus a `dipole` flag and left the consumer to
    infer the second as ``space + 2``. Reading the list means a 3-pole breaker
    reports three tabs instead of being silently truncated to two.
    """
    raw = _text(device, NODE_INFO, PROP_SPACES)
    if not raw:
        return []
    tabs: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            tabs.append(int(part))
        except ValueError:
            continue
    return tabs


def _settable(device: DiscoveredDevice, node: str, prop: str) -> bool:
    """Whether this device's declaration authorises writing one property.

    The device-level lookup; `description.declared_settable` holds the rule, and
    holds it once. `node_properties` drops a declaration that is not a mapping,
    so a malformed one arrives here as undeclared — which is the right answer for
    the same reason: it has not said the property is settable.
    """
    return declared_settable(node_properties(device, node).get(prop))


def priority_is_settable(device: DiscoveredDevice) -> bool:
    """Whether ``load-shed/priority`` is user-settable on this circuit.

    This is the successor to the flat ``never-backup`` boolean, and it is read
    from the description rather than from a value topic: v1.0 expresses
    never-backup as *mutability*, and the migration guide maps the retired
    property onto ``$settable = !never-backup``. So a circuit commissioned
    never-backup is one whose priority the panel declares unwritable, and the
    signal is the Homie attribute on the property definition.

    **A lock is announced by omission, not by ``settable: false``.** Homie 5
    defaults the attribute to false, and a conforming publisher emits it only
    where it is true — the eBus SDK's description builder does exactly that, and
    the vendored capture shows the same hand twice: ``switch/relay`` carries
    ``$settable`` on every controllable circuit and not on the one commissioned
    non-controllable, and ``load-shed/priority`` carries it on every circuit but
    the one commissioned never-backup. `description.declared_settable` gives the
    rule and the evidence for it.

    Reading omission the other way — as permission — is what this corrects. It
    offered a priority control on precisely the circuits commissioned not to
    accept one, and the panel refuses that write however the declaration last
    read. No producer we have seen has ever published ``settable: false`` for the
    permissive default to rescue: the capture's never-backup circuit announces
    its lock by saying nothing, which is what the misreading read as a yes.

    Absence of the *property* answers the same way and for a plainer reason: a
    device carrying no ``load-shed`` node, or one with no ``priority`` on it, has
    published no shed priority for anything to be settable *on*. Every
    non-circuit device in the tree is in that position — the BESS, the MID, the
    lugs — and a write target for one of them names a control it never offered.
    """
    return _settable(device, NODE_LOAD_SHED, PROP_PRIORITY)


def relay_is_settable(device: DiscoveredDevice) -> bool:
    """Whether this circuit's relay may be commanded.

    **The rule is the specification's, not an inference from what a producer
    does.** ``switch`` 0.3 — vendored at ``packages/schema-1/spec/catalogs/
    switch.json`` — declares ``relay`` as *"Settable when ``relay-controllable =
    true``"*, and defines ``relay-controllable`` as *"True = the relay can be
    opened and closed by command or automatic shed. False = locked (for example
    a circuit commissioned as permanently on)."* A consumer that published to a
    circuit whose ``relay-controllable`` is ``false`` would be writing to a
    property the specification says is not settable on that device.

    The condition is stated in the catalog's *prose*, where ``settable: true``
    stands unconditionally in the JSON beside it — the machine-readable field
    describes the property across the capability, and the condition that narrows
    it per device is not expressible there. So this reads the catalog and
    encodes the rule; it cannot derive it. `test_schema_one_control_refusal.py`
    pins the clause this depends on, so a specification that stops saying it
    fails here rather than leaving a stale rule in force.

    **Both signals, and it refuses when either says no.** The declaration and
    the value answer the same question, and SPAN reports a firmware defect in
    which the ``$settable`` re-toggle on the runtime re-commissioning path is
    skipped until the service restarts — so a consumer can meet a panel whose
    declaration is stale while the published value is current. The panel rejects
    an out-of-policy write regardless of what ``$settable`` last advertised, so
    the conjunction only ever refuses a write the panel would have refused.

    **What differs from `priority_is_settable` is the second signal, not the
    default.** Both read an absent ``$settable`` as locked, which is Homie 5's
    own default and the rule `description.declared_settable` states once. The
    relay reads a value property beside the declaration because ``switch``
    narrows it by one; ``load-shed`` states no condition on ``priority``, so
    there is no second signal there to consult.

    Across the two production enclosures we hold captures from — 27 circuits —
    ``$settable`` is present on ``switch/relay`` exactly when
    ``relay-controllable`` is ``true``, without exception, which is the
    specification's rule showing up in hardware. The vendored capture carries
    the same pattern on its five.
    """
    return _settable(device, NODE_SWITCH, PROP_RELAY) and _flag(device, NODE_SWITCH, PROP_RELAY_CONTROLLABLE, default=True)


def build_circuit(
    device: DiscoveredDevice, device_type: str = "circuit", relative_position: str = ""
) -> SpanCircuitSnapshot:
    """Build one circuit snapshot from its v1.0 device."""
    raw_power = _number(device, NODE_METER, PROP_ACTIVE_POWER)
    # Negate so positive means consumption. The guard keeps -0.0 out of the
    # snapshot, where it would compare equal to 0.0 but format as "-0.0".
    # A meter that has not reported stays `None` rather than becoming 0.0 W —
    # see `SpanCircuitSnapshot` for why absent and zero must not collapse.
    instant_power_w = None if raw_power is None else (0.0 if raw_power == 0.0 else -raw_power)

    relay_controllable = _flag(device, NODE_SWITCH, PROP_RELAY_CONTROLLABLE, default=True)
    priority = _text(device, NODE_LOAD_SHED, PROP_PRIORITY, UNKNOWN)
    priority_settable = priority_is_settable(device)

    return SpanCircuitSnapshot(
        circuit_id=device.device_id,
        name=_text(device, NODE_INFO, PROP_NAME),
        relay_state=_text(device, NODE_SWITCH, PROP_RELAY, UNKNOWN),
        instant_power_w=instant_power_w,
        # The panel *imported* this energy from the circuit, so the circuit
        # produced it. Named from the panel's perspective, reported from the
        # circuit's.
        produced_energy_wh=_number(device, NODE_METER, PROP_IMPORTED_ENERGY),
        consumed_energy_wh=_number(device, NODE_METER, PROP_EXPORTED_ENERGY),
        tabs=_tabs(device),
        priority=priority,
        # `always-on` is `not relay-controllable`, and the flat schema derived
        # user-controllability from `always-on` — so this is the same answer by
        # a shorter route.
        is_user_controllable=relay_controllable,
        is_sheddable=priority != PRIORITY_NEVER and relay_controllable,
        is_never_backup=not priority_settable,
        device_type=device_type,
        relative_position=relative_position,
        is_240v=(_number(device, NODE_BREAKER, PROP_POLES) or 1) >= 2,
        current_a=_number(device, NODE_METER, PROP_CURRENT),
        breaker_rating_a=_number(device, NODE_BREAKER, PROP_RATING),
        always_on=not relay_controllable,
        relay_requester=_text(device, NODE_SWITCH, PROP_RELAY_REQUESTER, UNKNOWN),
        relay_state_target=device.get_property_target(NODE_SWITCH, PROP_RELAY),
        priority_target=device.get_property_target(NODE_LOAD_SHED, PROP_PRIORITY),
        # The circuit half of `energy.ebus.capability.pcs`: participation only.
        # The arbitration that decides the enforced limit is the enclosure's,
        # and lands on `SpanPanelSnapshot.pcs`.
        #
        # `pcs/priority` is *not* `load-shed/priority` above, and the two share
        # neither a value space nor a purpose: this one is an integer shed
        # ordering under an import limit, that one is the backup tier
        # (`MUST_HAVE` / `NON_ESSENTIAL` / …). A circuit may participate in one
        # policy, both, or neither, so they are read separately and named apart.
        pcs_managed=_optional_flag(device, NODE_PCS, PROP_MANAGED),
        pcs_priority=_optional_integer(device, NODE_PCS, PROP_PRIORITY),
    )
