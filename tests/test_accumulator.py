"""Tests for HomiePropertyAccumulator with lifecycle management.

Covers:
- All lifecycle transitions (description-first, state-first, disconnection, invalid JSON, wrong serial)
- ready_since set on READY transition
- Property storage and defaults
- Timestamp tracking
- Target storage separate from reported values
- /set topics ignored
- Dirty tracking: property change marks dirty, same value doesn't, target change marks dirty,
  description marks all dirty, mark_clean clears
- Node queries: find_node_by_type, nodes_by_type, all_node_types
- Callbacks: fire on change only, not on same value, unregister works, exception doesn't propagate
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from span_panel_api.mqtt.accumulator import HomieLifecycle, HomiePropertyAccumulator
from span_panel_api.mqtt.const import TOPIC_PREFIX

SERIAL = "nj-2316-XXXX"
PREFIX = f"{TOPIC_PREFIX}/{SERIAL}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _desc(nodes: dict) -> str:
    return json.dumps({"nodes": nodes})


SIMPLE_DESC = _desc(
    {
        "core": {"type": "energy.ebus.device.distribution-enclosure.core"},
        "circuit-1": {"type": "energy.ebus.device.circuit"},
        "circuit-2": {"type": "energy.ebus.device.circuit"},
        "bess-0": {"type": "energy.ebus.device.bess"},
    }
)


def _make_ready(acc: HomiePropertyAccumulator, description: str = SIMPLE_DESC) -> None:
    """Drive accumulator to READY (description first, then state)."""
    acc.handle_message(f"{PREFIX}/$description", description)
    acc.handle_message(f"{PREFIX}/$state", "ready")


# ---------------------------------------------------------------------------
# Lifecycle: construction
# ---------------------------------------------------------------------------


class TestLifecycleConstruction:
    def test_initial_state_is_disconnected(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert acc.lifecycle == HomieLifecycle.DISCONNECTED

    def test_serial_number_property(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert acc.serial_number == SERIAL

    def test_is_ready_false_initially(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert not acc.is_ready()

    def test_ready_since_zero_initially(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert acc.ready_since == 0.0


# ---------------------------------------------------------------------------
# Lifecycle: description-first ordering
# ---------------------------------------------------------------------------


class TestLifecycleDescriptionFirst:
    def test_description_moves_to_description_received(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        assert acc.lifecycle == HomieLifecycle.DESCRIPTION_RECEIVED

    def test_state_ready_after_description_moves_to_ready(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        acc.handle_message(f"{PREFIX}/$state", "ready")
        assert acc.lifecycle == HomieLifecycle.READY
        assert acc.is_ready()

    def test_ready_since_set_on_ready(self):
        acc = HomiePropertyAccumulator(SERIAL)
        before = time.monotonic()
        _make_ready(acc)
        after = time.monotonic()
        assert before <= acc.ready_since <= after


# ---------------------------------------------------------------------------
# Lifecycle: state-first ordering
# ---------------------------------------------------------------------------


class TestLifecycleStateFirst:
    def test_state_ready_before_description_stays_connected(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$state", "ready")
        assert acc.lifecycle == HomieLifecycle.CONNECTED
        assert not acc.is_ready()

    def test_description_after_state_ready_moves_to_ready(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$state", "ready")
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        assert acc.lifecycle == HomieLifecycle.READY
        assert acc.is_ready()

    def test_ready_since_set_when_both_arrive(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$state", "ready")
        before = time.monotonic()
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        after = time.monotonic()
        assert before <= acc.ready_since <= after


# ---------------------------------------------------------------------------
# Lifecycle: disconnection
# ---------------------------------------------------------------------------


class TestLifecycleDisconnection:
    def test_disconnected_state_resets_to_disconnected(self):
        acc = HomiePropertyAccumulator(SERIAL)
        _make_ready(acc)
        acc.handle_message(f"{PREFIX}/$state", "disconnected")
        assert acc.lifecycle == HomieLifecycle.DISCONNECTED
        assert not acc.is_ready()

    def test_lost_state_resets_to_disconnected(self):
        acc = HomiePropertyAccumulator(SERIAL)
        _make_ready(acc)
        acc.handle_message(f"{PREFIX}/$state", "lost")
        assert acc.lifecycle == HomieLifecycle.DISCONNECTED

    def test_ready_since_preserved_after_disconnect(self):
        """ready_since records the last READY transition; not cleared on disconnect."""
        acc = HomiePropertyAccumulator(SERIAL)
        _make_ready(acc)
        rs = acc.ready_since
        acc.handle_message(f"{PREFIX}/$state", "disconnected")
        assert acc.ready_since == rs

    def test_init_state_moves_to_connected(self):
        """$state=init is a valid non-ready state; should be CONNECTED."""
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$state", "init")
        assert acc.lifecycle == HomieLifecycle.CONNECTED


# ---------------------------------------------------------------------------
# Lifecycle: invalid JSON description
# ---------------------------------------------------------------------------


class TestLifecycleInvalidDescription:
    def test_invalid_json_stays_in_current_state(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$state", "ready")
        acc.handle_message(f"{PREFIX}/$description", "{bad json")
        assert acc.lifecycle == HomieLifecycle.CONNECTED
        assert not acc.is_ready()


# ---------------------------------------------------------------------------
# Lifecycle: wrong serial
# ---------------------------------------------------------------------------


class TestWrongSerial:
    def test_messages_for_wrong_serial_ignored(self):
        acc = HomiePropertyAccumulator(SERIAL)
        other_prefix = f"{TOPIC_PREFIX}/other-serial"
        acc.handle_message(f"{other_prefix}/$state", "ready")
        acc.handle_message(f"{other_prefix}/$description", SIMPLE_DESC)
        assert acc.lifecycle == HomieLifecycle.DISCONNECTED


# ---------------------------------------------------------------------------
# Property storage and defaults
# ---------------------------------------------------------------------------


class TestPropertyStorage:
    def test_get_prop_default(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert acc.get_prop("node", "prop") == ""

    def test_get_prop_custom_default(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert acc.get_prop("node", "prop", default="X") == "X"

    def test_get_prop_after_store(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/name", "My Panel")
        assert acc.get_prop("core", "name") == "My Panel"

    def test_property_overwrite(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/name", "Old")
        acc.handle_message(f"{PREFIX}/core/name", "New")
        assert acc.get_prop("core", "name") == "New"


# ---------------------------------------------------------------------------
# Timestamp tracking
# ---------------------------------------------------------------------------


class TestTimestampTracking:
    def test_timestamp_zero_for_unknown(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert acc.get_timestamp("node", "prop") == 0

    def test_timestamp_set_on_property(self):
        acc = HomiePropertyAccumulator(SERIAL)
        before = int(time.time())
        acc.handle_message(f"{PREFIX}/core/power", "100")
        after = int(time.time())
        ts = acc.get_timestamp("core", "power")
        assert before <= ts <= after

    def test_timestamp_updates_on_overwrite(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/power", "100")
        ts1 = acc.get_timestamp("core", "power")
        # same value re-sent — timestamp still updates
        acc.handle_message(f"{PREFIX}/core/power", "100")
        ts2 = acc.get_timestamp("core", "power")
        assert ts2 >= ts1


# ---------------------------------------------------------------------------
# Target storage
# ---------------------------------------------------------------------------


class TestTargetStorage:
    def test_target_none_by_default(self):
        acc = HomiePropertyAccumulator(SERIAL)
        assert acc.get_target("node", "prop") is None
        assert not acc.has_target("node", "prop")

    def test_target_stored_from_dollar_target(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/relay/$target", "OPEN")
        assert acc.get_target("core", "relay") == "OPEN"
        assert acc.has_target("core", "relay")

    def test_target_independent_of_reported_value(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/relay", "CLOSED")
        acc.handle_message(f"{PREFIX}/core/relay/$target", "OPEN")
        assert acc.get_prop("core", "relay") == "CLOSED"
        assert acc.get_target("core", "relay") == "OPEN"


# ---------------------------------------------------------------------------
# /set topics ignored
# ---------------------------------------------------------------------------


class TestSetTopicsIgnored:
    def test_set_topic_not_stored(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/relay/set", "OPEN")
        assert acc.get_prop("core", "relay") == ""

    def test_set_topic_not_dirty(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/relay/set", "OPEN")
        assert len(acc.dirty_node_ids()) == 0


# ---------------------------------------------------------------------------
# Dirty tracking
# ---------------------------------------------------------------------------


class TestDirtyTracking:
    def test_property_change_marks_dirty(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/power", "100")
        assert "core" in acc.dirty_node_ids()

    def test_same_value_not_dirty(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/power", "100")
        acc.mark_clean()
        acc.handle_message(f"{PREFIX}/core/power", "100")
        assert "core" not in acc.dirty_node_ids()

    def test_target_change_marks_dirty(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/relay/$target", "OPEN")
        assert "core" in acc.dirty_node_ids()

    def test_same_target_not_dirty(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/relay/$target", "OPEN")
        acc.mark_clean()
        acc.handle_message(f"{PREFIX}/core/relay/$target", "OPEN")
        assert "core" not in acc.dirty_node_ids()

    def test_description_marks_all_dirty(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        dirty = acc.dirty_node_ids()
        assert "core" in dirty
        assert "circuit-1" in dirty
        assert "circuit-2" in dirty
        assert "bess-0" in dirty

    def test_mark_clean_clears(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/power", "100")
        acc.mark_clean()
        assert len(acc.dirty_node_ids()) == 0

    def test_dirty_returns_frozenset(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/core/power", "100")
        result = acc.dirty_node_ids()
        assert isinstance(result, frozenset)


# ---------------------------------------------------------------------------
# Node queries
# ---------------------------------------------------------------------------


class TestNodeQueries:
    def test_find_node_by_type(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        assert acc.find_node_by_type("energy.ebus.device.distribution-enclosure.core") == "core"

    def test_find_node_by_type_missing(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        assert acc.find_node_by_type("nonexistent") is None

    def test_nodes_by_type(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        circuits = acc.nodes_by_type("energy.ebus.device.circuit")
        assert sorted(circuits) == ["circuit-1", "circuit-2"]

    def test_nodes_by_type_empty(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        assert acc.nodes_by_type("nonexistent") == []

    def test_all_node_types(self):
        acc = HomiePropertyAccumulator(SERIAL)
        acc.handle_message(f"{PREFIX}/$description", SIMPLE_DESC)
        types = acc.all_node_types()
        assert types["core"] == "energy.ebus.device.distribution-enclosure.core"
        assert types["bess-0"] == "energy.ebus.device.bess"
        assert len(types) == 4


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_callback_fires_on_change(self):
        acc = HomiePropertyAccumulator(SERIAL)
        calls = []
        acc.register_property_callback(lambda n, p, v, old: calls.append((n, p, v, old)))
        acc.handle_message(f"{PREFIX}/core/power", "100")
        assert len(calls) == 1
        assert calls[0] == ("core", "power", "100", None)

    def test_callback_not_fired_on_same_value(self):
        acc = HomiePropertyAccumulator(SERIAL)
        calls = []
        acc.register_property_callback(lambda n, p, v, old: calls.append((n, p, v, old)))
        acc.handle_message(f"{PREFIX}/core/power", "100")
        acc.handle_message(f"{PREFIX}/core/power", "100")
        assert len(calls) == 1

    def test_callback_fires_on_value_change(self):
        acc = HomiePropertyAccumulator(SERIAL)
        calls = []
        acc.register_property_callback(lambda n, p, v, old: calls.append((n, p, v, old)))
        acc.handle_message(f"{PREFIX}/core/power", "100")
        acc.handle_message(f"{PREFIX}/core/power", "200")
        assert len(calls) == 2
        assert calls[1] == ("core", "power", "200", "100")

    def test_unregister_callback(self):
        acc = HomiePropertyAccumulator(SERIAL)
        calls = []
        unregister = acc.register_property_callback(lambda n, p, v, old: calls.append(1))
        acc.handle_message(f"{PREFIX}/core/power", "100")
        unregister()
        acc.handle_message(f"{PREFIX}/core/power", "200")
        assert len(calls) == 1

    def test_callback_exception_does_not_propagate(self):
        acc = HomiePropertyAccumulator(SERIAL)

        def bad_callback(n: str, p: str, v: str, old: str | None) -> None:
            raise RuntimeError("boom")

        acc.register_property_callback(bad_callback)
        # Should not raise
        acc.handle_message(f"{PREFIX}/core/power", "100")
        assert acc.get_prop("core", "power") == "100"

    def test_callback_exception_does_not_block_other_callbacks(self):
        acc = HomiePropertyAccumulator(SERIAL)
        calls = []

        def bad_callback(n: str, p: str, v: str, old: str | None) -> None:
            raise RuntimeError("boom")

        acc.register_property_callback(bad_callback)
        acc.register_property_callback(lambda n, p, v, old: calls.append(1))
        acc.handle_message(f"{PREFIX}/core/power", "100")
        assert len(calls) == 1
