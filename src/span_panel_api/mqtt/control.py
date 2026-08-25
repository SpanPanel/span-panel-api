"""What happened to a control command, said precisely enough to act on.

Every setter used to return `None`, which meant the caller could not tell a
breaker that opened from one whose command was dropped on the floor. These types
are the vocabulary for saying which.

The distinctions here are not decoration. A consumer renders each of them
differently to a person, and two of them are the difference between "your panel
did the thing" and "your panel may do the thing in four minutes".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PublishState(StrEnum):
    """How far a control command got.

    Ordered from most to least evidence. Only `FAILED` is a promise about the
    future; the rest describe what was observed by a deadline and say nothing
    about what happens after it.
    """

    CONFIRMED = "confirmed"
    """The property reported the requested value on its own topic.

    The strongest statement available, and still not proof that *this* write
    caused it -- see the correlation caveat on the setters.
    """

    ACCEPTED = "accepted"
    """The broker acknowledged the message (QoS-1 PUBACK); no transition seen.

    A real and separate diagnosis, kept apart from `UNCONFIRMED` because paho
    already gives it away for free and folding the two together discards
    information the library is holding anyway. "The broker took it and the panel
    did not act" and "nothing ever acknowledged it" send an investigation in
    different directions.
    """

    UNCONFIRMED = "unconfirmed"
    """Handed over, and nothing came back within the deadline.

    **Not an error, and must not raise.** It is the expected result of a write
    whose value was already current, and it is indistinguishable from a silent
    policy rejection by the panel until SPAN ships a reason code. A consumer
    should say what it means -- the panel accepted the command and never reported
    a change, most often because there was no change to report -- rather than
    dressing it as a failure.
    """

    FAILED = "failed"
    """Never handed to the broker. Will not be delivered.

    The one state that is a promise, and the reason the transport refuses to
    publish while disconnected rather than checking paho's return code
    afterwards. paho *queues* a QoS-1 publish across a disconnect and sends it
    when the connection returns, so a command reported failed on a return code
    could still fire minutes later against a panel nobody is watching. A user
    told "failed" acts on that. `FAILED` is only ever produced by a refusal that
    happens before paho sees the message at all.
    """


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """The result of one control command.

    `detail` is free text for a human reading a log or an audit row -- which
    refusal, which deadline. **It never carries a credential**, and nothing
    should parse it; `state` is the machine-readable half.
    """

    state: PublishState
    topic: str
    value: str
    no_op: bool = False
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ControlDeadlines:
    """How long each control waits for the panel to report the change.

    Per property rather than one number, because the thing being waited on
    differs in kind. A relay is a physical actuation with a contactor at the end
    of it; the rest are values the panel writes down. Sized so the common case
    returns as soon as the transition arrives -- the deadline is only reached
    when nothing does.

    Injectable rather than constant so tests do not each pay a real deadline to
    assert a refusal.
    """

    relay: float = 5.0
    priority: float = 2.0
    dominant_power_source: float = 2.0
    evse_charge_limit: float = 2.0
    adopted_property: float = 2.0
