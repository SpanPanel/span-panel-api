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
from typing import Protocol, runtime_checkable


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

    `topic` is None for a command that never had one: a refusal made while
    resolving the address -- a relay the panel declares non-commandable, a
    charger with no settable limit -- happens before any topic exists, and
    naming one anyway would put a string in an audit row that nothing was ever
    going to publish to. `state` is `FAILED` whenever it is None.
    """

    state: PublishState
    topic: str | None
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


@dataclass(frozen=True, slots=True)
class ControlCommand:
    """One control command, described in the terms a policy or an audit needs.

    Built from the `ControlTarget` the adapter produced plus the translated
    payload, so the identifying fields are the ones actually on the wire rather
    than the caller's arguments -- a dominant-power-source request of `BATTERY`
    arrives here as the `OFF_GRID` that will be published under v1.0.

    **A command the library refused before resolving an address still arrives
    here**, because `after_publish` is contracted to see every command and an
    audit missing exactly the commands a panel declared unsafe is worse than no
    audit. Such a command carries what is known and no more: `topic` is None,
    and the identifying fields fall back to the caller's arguments, since the
    wire spellings the adapter would have supplied are the very thing that did
    not resolve. `node_id` and `property_id` are empty strings where even those
    are unknown -- under the flat schema a relay lives at
    `<serial>/<circuit>/relay` and under v1.0 at `<circuit>/switch/relay`, and
    the bootstrap is the one component that must not know which.
    """

    device_id: str
    node_id: str
    property_id: str
    value: str
    topic: str | None


@runtime_checkable
class ControlInterceptor(Protocol):
    """One veto-and-observe point covering every control command.

    **This is a boundary against callers of this library, and nothing more.**
    It does not constrain anything holding the broker credential: a process with
    the credential publishes to the panel's broker directly and never reaches
    this code, and neither does another copy of this library in another process.
    Presenting it as a security boundary around the *panel* would be the most
    damaging thing this feature could do, because a user would stop looking for
    the real one.

    What it is good for is the boundary it can actually hold: everything
    arriving through this client. A consumer with a notion of who is asking --
    Home Assistant has one, per service call -- can refuse here, and can record
    every command in one place rather than in five setters that will drift.

    One interceptor at a time, replaceable. Several would raise ordering
    questions with no principled answer.
    """

    async def before_publish(self, command: ControlCommand) -> None:
        """Called before anything is published. Raise to veto.

        **The exception propagates to the caller unchanged.** The library does
        not translate it: the interceptor raised it, and owns its type and its
        message. That is load-bearing for a consumer that raises a
        framework-specific error carrying a translated message and needs it to
        reach the user intact.

        A veto still produces an `after_publish` with `PublishState.FAILED`, so
        an audit built on this cannot silently omit the refusals -- which would
        make it worse than no audit.
        """

    async def after_publish(self, command: ControlCommand, outcome: PublishOutcome) -> None:
        """Called with the result of every command, refusals included.

        Every command, including one this library refused before it had a topic
        to publish to -- a relay the panel declares non-commandable is the
        highest-consequence control in the system, and an audit that recorded
        the attempts that reached the wire and not the ones that were stopped
        would have its hole exactly where the interesting cases are. Those
        arrive with `PublishState.FAILED`, a `detail` naming the refusal, and
        `command.topic` / `outcome.topic` set to None. `before_publish` is not
        consulted for them: there is nothing to authorise, and a veto would
        replace a specific reason with "vetoed".

        **Fired as a task and not awaited on the control path.** A sink that
        merely hangs -- a slow event bus, a blocked writer -- would otherwise
        stall every control call in the process. The consequences are the price:
        ordering across commands is not guaranteed, so an audit consumer must
        not assume it, and an exception raised here is logged and discarded
        rather than reaching the caller who issued the command.
        """
