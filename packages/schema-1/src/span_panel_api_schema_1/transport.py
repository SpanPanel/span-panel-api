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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from paho.mqtt.client import topic_matches_sub

if TYPE_CHECKING:
    from collections.abc import Callable


class ControllerRoutes:
    """Record `Controller`'s subscriptions and route messages back to them.

    Structurally satisfies `ebus_sdk.MqttControllerTransport`. Receive-only by
    design — see :meth:`publish`.
    """

    def __init__(self) -> None:
        # Insertion-ordered; dispatch walks it in reverse — see dispatch().
        self._routes: dict[str, Callable[[str, bytes], None]] = {}

    # -- MqttControllerTransport -------------------------------------------

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> None:
        """Not supported, and deliberately loud about it.

        Commands do not travel this way. `SchemaAdapter` exposes
        ``set_circuit_relay_topic`` and friends, and the transport publishes to
        the topic it is handed — so an adapter never needs a socket to command a
        panel, and neither does this class.

        Raising beats a silent no-op: a dropped command leaves the panel in the
        state the user was trying to change, with the UI reporting they changed
        it.
        """
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
        # keeps the key's original position, which would leave a re-registered
        # pattern behind whatever was added after it in dispatch's match order.
        self._routes.pop(sub, None)
        self._routes[sub] = param

    def unsubscribe(self, sub: str) -> None:
        """Forget the callback for `sub`.

        The broad wire subscription stays. Messages for a device the SDK has
        dropped simply stop matching a route, and dispatch discards them.
        """
        self._routes.pop(sub, None)

    # -- our side ----------------------------------------------------------

    def dispatch(self, topic: str, payload: str) -> None:
        """Deliver one message to the callback of the route that matches.

        Walked most-recent-first, which is defensive rather than currently
        required. Tree-rooted discovery subscribes four **device-scoped**
        patterns per device — `$state`, `$description`, `+/+`, `+/+/$target`
        (`Controller._subscribe_device_topics`) — which cannot overlap each
        other or another device's, so today exactly one route matches any topic.

        Pinned anyway because the SDK's wildcard discovery mode subscribes
        `<domain>/5/+/$state`, overlapping every per-device `$state`. Under
        insertion order that would hand a device's state to the wildcard
        handler — a silent misattribution rather than an error. Preferring the
        most recently recorded route costs nothing while overlap does not
        occur, and is correct if it ever does.

        A topic matching no route is dropped: the wire subscription is broader
        than the SDK's interest by construction.

        The SDK hands callbacks `bytes`; the transport hands us `str`.
        """
        for sub in reversed(self._routes):
            if topic_matches_sub(sub, topic):
                self._routes[sub](topic, payload.encode())
                return

    @property
    def routes(self) -> tuple[str, ...]:
        """The recorded subscription patterns, most recent last. Diagnostics only."""
        return tuple(self._routes)
