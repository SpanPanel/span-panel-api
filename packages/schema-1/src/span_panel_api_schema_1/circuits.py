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


def _priority_is_settable(device: DiscoveredDevice) -> bool:
    """Whether ``load-shed/priority`` is user-settable on this circuit.

    This is the successor to the flat ``never-backup`` boolean, and it is read
    from the description rather than from a value topic — v1.0 expresses
    never-backup as *mutability*, so the signal is the Homie ``$settable``
    attribute on the property definition.

    Absent means settable: locking is the exception a panel announces, so
    treating an unannounced circuit as locked would mark every circuit
    never-backup on a panel that does not publish the attribute.
    """
    definition = device.get_node_properties(NODE_LOAD_SHED).get(PROP_PRIORITY)
    if not isinstance(definition, dict):
        return True
    settable = definition.get(ATTR_SETTABLE)
    if settable is None:
        return True
    if isinstance(settable, bool):
        return settable
    return str(settable).strip().lower() != "false"


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
    priority_settable = _priority_is_settable(device)

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
