"""The seam that lets `ebus_sdk.Controller` parse a tree it owns no socket for.

`Controller` normally holds an MQTT client and subscribes as it walks a device
tree — for the root first, then per child as each announces. A `SchemaAdapter`
cannot work that way: the transport builds the parser *before* the connection
exists and never hands it one, so a parser has no way to subscribe to anything.

It turns out not to need one. `Controller` is given a transport that only
*records* its subscriptions, and the adapter asks the transport layer for one
broad subscription up front — the same thing the flat adapter does with
``ebus/5/{serial}/#``. Every message then arrives through
``SchemaAdapter.handle_message`` and is routed here to whichever SDK callback
asked for it.

Two consequences, both load-bearing:

* **The adapter stays connection-free**, so it works under a protocol that
  hands it messages rather than a socket.
* **Reconnect needs no special handling.** The transport re-subscribes the same
  static list on every reconnect, the broker replays the retained tree, and the
  SDK repopulates from it. There is no hand-wired ``resync`` hook to forget —
  which was the failure mode most likely to go unnoticed, because it produces
  stale readings rather than an error.

One thing the single subscription does have to make up for. `Controller` learns
which topics it wants *as it goes*: the root's routes exist from construction,
a child's only once the root's description has been parsed and the root has
reached ready. But one wire subscription delivers the whole tree in a single
burst, in whatever order the broker replays its retained store — and a broker
is under no obligation to hand back the parent before its children. A message
that arrives before the SDK asks for it is therefore held, and delivered the
moment the matching route is registered. Under a real per-device subscription
the SDK would have got that value as a retained message at subscribe time, so
holding it reproduces what it would otherwise have seen rather than inventing
anything. Dropping it instead is silent and total: an entire panel parses as
zero circuits.
"""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from paho.mqtt.matcher import MQTTMatcher

from span_panel_api.models import ControlTarget
from span_panel_api_schema_1.const import HOMIE_DOMAIN, HOMIE_VERSION

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


def control_target(device_id: str, node: str, prop: str) -> ControlTarget:
    """One device/node/property address as both a set topic and an observation key.

    The triple is returned alongside the topic rather than left for the
    transport to parse back out of it: the transport is the one component
    that is supposed to know nothing about this schema's topic grammar, and
    under parent/child the device is a peer of the panel rather than a node
    beneath it, so the grammar is not even the flat one.

    The spelling here is the spelling the adapter's property callbacks report
    under, because a write is verified by matching one against the other.
    """
    return ControlTarget(
        topic=f"{HOMIE_DOMAIN}/{HOMIE_VERSION}/{device_id}/{node}/{prop}/set",
        device_id=device_id,
        node_id=node,
        property_id=prop,
    )


# Ceiling on messages held for a route that has not appeared. Sized well past a
# full panel — a 48-space enclosure with every DER runs to a few thousand
# topics — so reaching it means messages are arriving for a subtree the SDK
# will never ask about, and holding more would be a slow leak in a process that
# runs for months.
MAX_HELD_MESSAGES = 4096


class _Route(NamedTuple):
    """A recorded callback, and when it was recorded, which decides an overlap."""

    recorded: int
    callback: Callable[[str, bytes], None]


class ControllerRoutes:
    """Record `Controller`'s subscriptions and route messages back to them.

    Structurally satisfies `ebus_sdk.MqttControllerTransport`. Receive-only by
    design — see :meth:`publish`.
    """

    def __init__(self) -> None:
        self._routes: dict[str, _Route] = {}
        # The same patterns, indexed for matching: each maps to itself, and
        # dispatch resolves the patterns that match through `_routes`.
        self._matcher = MQTTMatcher()
        self._recordings = itertools.count()
        # Last payload per topic that matched no route yet, keyed by topic so a
        # newer value supersedes an older one — the same last-value-wins rule
        # the broker applies to the retained message this stands in for.
        self._held: dict[str, str] = {}
        self._discarded = 0

    # -- MqttControllerTransport -------------------------------------------

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> None:
        """Not supported, and deliberately loud about it.

        Commands do not travel this way. `SchemaAdapter` exposes
        ``set_circuit_relay_target`` and friends, and the transport publishes to
        the topic it is handed — so an adapter never needs a socket to command a
        panel, and neither does this class.

        Raising beats a silent no-op: a dropped command leaves the panel in the
        state the user was trying to change, with the UI reporting they changed
        it.
        """
        # Named exactly as `MqttClient.publish` names them, so a real client
        # satisfies the same protocol this class does. Discarded rather than
        # renamed, because nothing here is ever sent.
        del topic, data, qos, retain
        raise NotImplementedError(
            "ControllerRoutes is receive-only. Publish through the adapter's "
            "set_*_topic methods, which the transport layer sends for you."
        )

    def subscribe(self, sub: str, param: Any, qos: int = 1) -> None:  # pylint: disable=unused-argument
        """Record the callback for `sub`. Nothing reaches the wire.

        `param` is the SDK's name for the callback, and this signature mirrors
        `MqttClient.subscribe` exactly — including its `Any` — so a real
        `MqttClient` still satisfies the same protocol this class does.

        `qos` is accepted and ignored, which is why it is disabled above rather
        than removed: the protocol fixes the signature, and quality of service
        is a property of the one wire subscription the transport layer makes on
        the adapter's behalf, not of a route recorded in a dict.
        """
        # Pop before insert: assigning an existing key updates the value but
        # keeps the key's original position, and `routes` lists most recent last.
        self._routes.pop(sub, None)
        self._routes[sub] = _Route(next(self._recordings), param)
        self._matcher[sub] = sub
        self._release(sub, param)

    def unsubscribe(self, sub: str) -> None:
        """Forget the callback for `sub`.

        The broad wire subscription stays. Messages for a device the SDK has
        dropped simply stop matching a route, and dispatch holds them.
        """
        # Guarded because the matcher raises for a pattern it never held, and
        # forgetting one that was never recorded is harmless.
        if self._routes.pop(sub, None) is not None:
            del self._matcher[sub]

    # -- our side ----------------------------------------------------------

    def dispatch(self, topic: str, payload: str) -> None:
        """Deliver one message to the callback of the route that matches.

        Matched through a prefix tree of the recorded patterns, so the cost
        follows the topic's depth rather than the number of routes. Every
        message takes this path, and a linear scan grew with each device the
        panel declares, at four routes per device (#223).

        The most recently recorded match wins, which is defensive rather than
        currently required. Tree-rooted discovery subscribes four **device-scoped**
        patterns per device — `$state`, `$description`, `+/+`, `+/+/$target`
        (`Controller._subscribe_device_topics`) — which cannot overlap each
        other or another device's, so today exactly one route matches any topic.

        Pinned anyway because the SDK's wildcard discovery mode subscribes
        `<domain>/5/+/$state`, overlapping every per-device `$state`. Under
        insertion order that would hand a device's state to the wildcard
        handler — a silent misattribution rather than an error. Preferring the
        most recently recorded route costs nothing while overlap does not
        occur, and is correct if it ever does.

        A topic matching no route is held rather than dropped, because the
        route it belongs to may simply not exist yet — see the module
        docstring. The wire subscription is broader than the SDK's interest by
        construction, so some held messages are never claimed; the ceiling
        keeps that from growing without bound.

        The SDK hands callbacks `bytes`; the transport hands us `str`.
        """
        # The matcher is untyped and yields what it was given, the pattern
        # string; `_routes` turns each back into a typed route. The list is
        # complete before any callback runs, so a callback that subscribes
        # changes a matcher nothing is still walking.
        matched = [self._routes[sub] for sub in self._matcher.iter_match(topic)]
        if not matched:
            self._hold(topic, payload)
            return
        max(matched, key=lambda route: route.recorded).callback(topic, payload.encode())

    def _hold(self, topic: str, payload: str) -> None:
        """Keep a message for a route that has not been registered yet."""
        if topic not in self._held and len(self._held) >= MAX_HELD_MESSAGES:
            self._discarded += 1
            if self._discarded == 1:
                _LOGGER.warning(
                    "Holding %d unrouted topics; discarding %r and further new ones. "
                    "The device tree is larger than expected, or the broker carries "
                    "topics outside it.",
                    MAX_HELD_MESSAGES,
                    topic,
                )
            return
        self._held[topic] = payload

    def _release(self, sub: str, callback: Callable[[str, bytes], None]) -> None:
        """Deliver everything held that this newly registered route matches.

        Re-entrant by necessity: a released `$description` makes the SDK
        subscribe to that device's children, which releases their held messages
        in turn, so a whole tree unfolds from one root subscription. The
        candidate list is therefore taken up front and each entry re-checked,
        since a nested release may have claimed it already.

        The pattern is indexed once and matched against every held topic,
        rather than indexed afresh for each of them.
        """
        pattern = MQTTMatcher()
        pattern[sub] = sub
        for topic in [held for held in self._held if next(pattern.iter_match(held), None) is not None]:
            payload = self._held.pop(topic, None)
            if payload is not None:
                callback(topic, payload.encode())

    @property
    def routes(self) -> tuple[str, ...]:
        """The recorded subscription patterns, most recent last. Diagnostics only."""
        return tuple(self._routes)

    @property
    def held(self) -> int:
        """Messages waiting for a route to be registered. Diagnostics only."""
        return len(self._held)
