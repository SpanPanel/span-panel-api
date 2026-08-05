"""The seam that lets `ebus_sdk.Controller` parse a tree it owns no socket for.

These tests are about routing, not parsing. They pin the behaviour the SDK's own
MQTT client provides internally, which a `SchemaAdapter` cannot rely on because
it is built before any connection exists and never receives one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ebus_sdk import MqttControllerTransport

from span_panel_api_schema_1 import ControllerRoutes
from span_panel_api_schema_1.transport import MAX_HELD_MESSAGES


def test_it_satisfies_the_sdk_transport_protocol() -> None:
    """Structural conformance, checked rather than assumed.

    `MqttControllerTransport` is runtime_checkable and method-only, so this is a
    real check — and it is what upstream shipped in 0.17.0 specifically so a
    bring-your-own-transport consumer would not need a cast.
    """
    assert isinstance(ControllerRoutes(), MqttControllerTransport)


def test_it_needs_no_connection_to_construct() -> None:
    """The whole point. The transport builds the parser before the connection
    exists, so anything the parser owns must be constructible without one."""
    routes = ControllerRoutes()

    assert routes.routes == ()


def test_subscribe_records_a_route() -> None:
    routes = ControllerRoutes()
    callback = MagicMock()

    routes.subscribe("ebus/5/panel/#", callback, qos=1)

    assert routes.routes == ("ebus/5/panel/#",)


def test_unsubscribe_forgets_the_route() -> None:
    """The wire subscription is broader and stays put; messages for a device the
    SDK dropped simply stop matching."""
    routes = ControllerRoutes()
    callback = MagicMock()
    routes.subscribe("ebus/5/child/#", callback)

    routes.unsubscribe("ebus/5/child/#")

    assert routes.routes == ()
    routes.dispatch("ebus/5/child/meter/active-power", "1.0")
    callback.assert_not_called()


def test_unsubscribing_something_unknown_is_harmless() -> None:
    ControllerRoutes().unsubscribe("ebus/5/never-subscribed/#")  # must not raise


def test_publish_refuses_rather_than_silently_dropping() -> None:
    """Commands do not travel this way — the adapter returns a topic and the
    transport layer sends it. A silent no-op here would leave the panel in the
    state the user was trying to change, with the UI reporting they changed it.
    """
    with pytest.raises(NotImplementedError, match="receive-only"):
        ControllerRoutes().publish("ebus/5/panel/core/relay/set", "CLOSED")


def test_dispatch_delivers_bytes_to_the_matching_callback() -> None:
    """The transport hands us `str`; the SDK hands its callbacks `bytes`."""
    routes = ControllerRoutes()
    callback = MagicMock()
    routes.subscribe("ebus/5/panel/+/+", callback)

    routes.dispatch("ebus/5/panel/meter/active-power", "-121.0")

    callback.assert_called_once_with("ebus/5/panel/meter/active-power", b"-121.0")


def test_a_topic_matching_no_route_reaches_nobody_yet() -> None:
    """Expected, not exceptional: the wire subscription is broader than the
    SDK's interest by construction. It is held rather than delivered — see the
    ordering tests below for why it is not simply thrown away."""
    routes = ControllerRoutes()
    callback = MagicMock()
    routes.subscribe("ebus/5/panel/#", callback)

    routes.dispatch("ebus/5/other-device/meter/active-power", "1.0")

    callback.assert_not_called()
    assert routes.held == 1


def test_the_most_recently_recorded_matching_route_wins() -> None:
    """Defensive rather than currently required: tree-rooted discovery records
    four device-scoped patterns per device, which cannot overlap. But the SDK's
    wildcard mode subscribes `<domain>/5/+/$state`, overlapping every per-device
    `$state`. Under insertion order that would hand a device's state to the
    wildcard handler — silent misattribution, not an error."""
    routes = ControllerRoutes()
    broad = MagicMock(name="root")
    narrow = MagicMock(name="child")
    routes.subscribe("ebus/5/#", broad)
    routes.subscribe("ebus/5/child-a/#", narrow)

    routes.dispatch("ebus/5/child-a/meter/active-power", "-3500.0")

    narrow.assert_called_once()
    broad.assert_not_called()


def test_a_topic_only_the_broad_route_covers_still_arrives() -> None:
    """The corollary: preferring the specific must not strand the general."""
    routes = ControllerRoutes()
    broad = MagicMock(name="root")
    narrow = MagicMock(name="child")
    routes.subscribe("ebus/5/#", broad)
    routes.subscribe("ebus/5/child-a/#", narrow)

    routes.dispatch("ebus/5/panel/$state", "ready")

    broad.assert_called_once()
    narrow.assert_not_called()


def test_rerecording_a_pattern_replaces_it_and_moves_it_to_most_recent() -> None:
    """Re-registering must not leave the stale callback ahead in match order.

    Caught by this test in review: assigning an existing dict key updates the
    value but keeps the key's original position.
    """
    routes = ControllerRoutes()
    first = MagicMock(name="first")
    second = MagicMock(name="second")
    routes.subscribe("ebus/5/child-a/#", first)
    routes.subscribe("ebus/5/#", MagicMock(name="root"))
    routes.subscribe("ebus/5/child-a/#", second)

    routes.dispatch("ebus/5/child-a/$state", "ready")

    second.assert_called_once()
    first.assert_not_called()


# ---------------------------------------------------------------------------
# Arrival order
#
# One wire subscription delivers the whole tree at once, but the SDK registers
# its routes as it walks that tree. Anything arriving ahead of its route has to
# survive the gap, because the broker chooses the replay order and is under no
# obligation to hand back a parent before its children.
# ---------------------------------------------------------------------------


def test_a_message_that_arrives_before_its_route_is_delivered_when_the_route_appears() -> None:
    routes = ControllerRoutes()
    callback = MagicMock()

    routes.dispatch("ebus/5/child-a/meter/active-power", "-3500.0")
    callback.assert_not_called()

    routes.subscribe("ebus/5/child-a/+/+", callback)

    callback.assert_called_once_with("ebus/5/child-a/meter/active-power", b"-3500.0")
    assert routes.held == 0


def test_a_held_topic_keeps_only_its_latest_value() -> None:
    """The same last-value-wins rule the broker applies to the retained message
    this stands in for. Delivering the stale reading too would be worse than
    dropping it — the SDK would end on whichever arrived last."""
    routes = ControllerRoutes()
    callback = MagicMock()

    routes.dispatch("ebus/5/child-a/meter/active-power", "-3500.0")
    routes.dispatch("ebus/5/child-a/meter/active-power", "-3400.0")
    routes.subscribe("ebus/5/child-a/+/+", callback)

    callback.assert_called_once_with("ebus/5/child-a/meter/active-power", b"-3400.0")


def test_releasing_a_message_can_register_the_routes_that_release_the_rest() -> None:
    """How a whole tree unfolds from one root subscription.

    Releasing the root's description is what makes the SDK subscribe to its
    children, whose own messages are already held — so release has to be
    re-entrant, or the tree stops one level down.
    """
    routes = ControllerRoutes()
    child = MagicMock(name="child")

    def on_root(_topic: str, _payload: bytes) -> None:
        routes.subscribe("ebus/5/child-a/+/+", child)

    routes.dispatch("ebus/5/child-a/meter/active-power", "-3500.0")
    routes.dispatch("ebus/5/panel/$description", "{}")

    routes.subscribe("ebus/5/panel/$description", on_root)

    child.assert_called_once_with("ebus/5/child-a/meter/active-power", b"-3500.0")
    assert routes.held == 0


def test_a_released_message_is_not_delivered_again() -> None:
    routes = ControllerRoutes()
    callback = MagicMock()
    routes.dispatch("ebus/5/child-a/meter/active-power", "-3500.0")

    routes.subscribe("ebus/5/child-a/+/+", callback)
    routes.subscribe("ebus/5/child-a/+/+", callback)

    callback.assert_called_once()


def test_held_messages_stop_accumulating_at_the_ceiling() -> None:
    """Unclaimed topics would otherwise be a slow leak in a process that runs
    for months. Values already held still update — it is new topics that stop."""
    routes = ControllerRoutes()

    for index in range(MAX_HELD_MESSAGES + 10):
        routes.dispatch(f"ebus/5/device-{index}/meter/active-power", "1.0")
    routes.dispatch("ebus/5/device-0/meter/active-power", "2.0")

    assert routes.held == MAX_HELD_MESSAGES

    callback = MagicMock()
    routes.subscribe("ebus/5/device-0/+/+", callback)
    callback.assert_called_once_with("ebus/5/device-0/meter/active-power", b"2.0")
