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
    ATTR_SETTABLE,
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


def _declared_settable(device: DiscoveredDevice, node: str, prop: str, *, when_unannotated: bool) -> bool:
    """Read the Homie ``$settable`` attribute off one property's definition.

    **An undeclared property is never settable, whatever ``when_unannotated``
    says.** The two absences are different claims and were once collapsed onto
    one answer: a device that declares ``load-shed/priority`` and omits
    ``$settable`` has left a mutable property unannotated, while a device with no
    ``load-shed`` node at all — a BESS, a MID, the lugs — has not declared the
    property, and there is nothing on it to be settable. Answering the second
    with the first's default produced a write target for a control the device
    never offered, which is the defect the refusal exists to prevent.

    ``when_unannotated`` is therefore the narrower question it now names: what a
    *declared* property means when it carries no ``$settable``. That is a
    per-property judgement rather than one rule — which is why it is a parameter
    instead of a default. See the two callers below for why they answer it
    differently.

    A definition that is not a mapping is treated as undeclared for the same
    reason: a malformed declaration has not said the property is settable.
    """
    definition = device.get_node_properties(node).get(prop)
    if not isinstance(definition, dict):
        return False
    settable = definition.get(ATTR_SETTABLE)
    if settable is None:
        return when_unannotated
    if isinstance(settable, bool):
        return settable
    return str(settable).strip().lower() != "false"


def priority_is_settable(device: DiscoveredDevice) -> bool:
    """Whether ``load-shed/priority`` is user-settable on this circuit.

    ``load-shed`` 0.3 declares ``priority`` settable and states no condition on
    it, so mutability is the property's ordinary state and a lock is something a
    panel announces. This is the successor to the flat ``never-backup`` boolean,
    and it is read from the description rather than from a value topic — v1.0
    expresses never-backup as *mutability*, so the signal is the Homie
    ``$settable`` attribute on the property definition.

    An unannounced ``$settable`` therefore means settable: treating an
    unannounced circuit as locked would mark every circuit never-backup on a
    panel that does not publish the attribute.

    **An unannounced attribute is not an undeclared property**, and only the
    first of those means settable. A device carrying no ``load-shed`` node, or a
    ``load-shed`` node with no ``priority`` on it, has not published a shed
    priority for anything to be settable *on* — every non-circuit device in the
    tree is in that position, the BESS and the MID and the lugs among them. The
    permissive default is for the documented case where firmware declares the
    property and omits the attribute, and reading it as permission for a device
    that declared neither produced a write target for a control that device
    never offered. `_declared_settable` separates the two.
    """
    return _declared_settable(device, NODE_LOAD_SHED, PROP_PRIORITY, when_unannotated=True)


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

    **Absent ``$settable`` reads as locked here, the opposite of
    `priority_is_settable`.** Homie 5 defaults the attribute to false, and the
    catalog's condition means a locked relay is *not* settable, so a publisher
    describing one correctly omits the attribute rather than publishing
    ``false``. Absence is therefore the announcement. Priority answers the other
    way because its catalog entry carries no condition at all. The two
    properties are not making the same kind of claim. Both refuse a device that
    declares no such property at all, which is a third case and not either
    default.

    Across the two production enclosures we hold captures from — 27 circuits —
    ``$settable`` is present on ``switch/relay`` exactly when
    ``relay-controllable`` is ``true``, without exception, which is the
    specification's rule showing up in hardware.
    """
    return _declared_settable(device, NODE_SWITCH, PROP_RELAY, when_unannotated=False) and _flag(
        device, NODE_SWITCH, PROP_RELAY_CONTROLLABLE, default=True
    )


def build_circuit(
    device: DiscoveredDevice, device_type: str = "circuit", relative_position: str = ""
) -> SpanCircuitSnapshot:
    """Build one circuit snapshot from its v1.0 device."""
    raw_power = _number(device, NODE_METER, PROP_ACTIVE_POWER) or 0.0
    # Negate so positive means consumption. The guard keeps -0.0 out of the
    # snapshot, where it would compare equal to 0.0 but format as "-0.0".
    instant_power_w = 0.0 if raw_power == 0.0 else -raw_power

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
        produced_energy_wh=_number(device, NODE_METER, PROP_IMPORTED_ENERGY) or 0.0,
        consumed_energy_wh=_number(device, NODE_METER, PROP_EXPORTED_ENERGY) or 0.0,
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
