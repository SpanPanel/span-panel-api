#!/usr/bin/env python3
"""Verify that a live MQTT session recovers from a broker outage.

The bridge's reconnect and rebuild machinery has unit coverage against a mocked
paho client, which proves the control flow. What it cannot prove is the part
only a broker can answer: that after a real socket drop the client
re-subscribes, the broker replays its retained tree, and the parser repopulates
to the same panel it described before.

A severable TCP passthrough sits between the client and the broker, so the
outage is a real network failure — the broker keeps running and keeps its
retained state, exactly as when an integration loses its route to the panel.
Cutting closes the listener as well as the live sockets, so reconnect attempts
during the outage are refused rather than left hanging.

Two outages are exercised, because the client recovers from them differently:

  brief      Restored at once, so the reconnect loop succeeds before the
             rebuild threshold. The parser instance survives, and has to
             absorb a second delivery of the retained tree it already holds.

  sustained  Held past MQTT_FULL_REBUILD_AFTER_FAILURES, so the bridge rebuilds
             its paho client and the transport swaps in a *fresh* parser.
             Recovery then comes entirely from retained state.

Usage — flat schema against the SPAN simulator (TLS, real CA re-fetch):

    uv run python scripts/verify_reconnect.py \
        --serial sim-40t-001 \
        --panel-host 127.0.0.1 --panel-http-port 8081 \
        --broker-host 127.0.0.1 --broker-port 18883 \
        --broker-username span --broker-password <password>

Usage — parent/child schema against a plain broker seeded from the captured
tree. schema_1 registers no entry point yet, so its factory is named outright:

    uv run python scripts/verify_reconnect.py \
        --serial example-40t-001 \
        --broker-host 127.0.0.1 --broker-port 1883 --no-tls \
        --data-model-version 1.0 \
        --adapter span_panel_api_schema_1:SchemaOneAdapter \
        --seed tests/reference_payloads/parent_child_tree.json

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import importlib
import json
from pathlib import Path
import socket
import sys
import time
from typing import TYPE_CHECKING

from span_panel_api.exceptions import SpanPanelStaleDataError
from span_panel_api.models import SpanPanelSnapshot, V2HomieSchema
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.const import MQTT_FULL_REBUILD_AFTER_FAILURES
from span_panel_api.mqtt.models import MqttClientConfig

if TYPE_CHECKING:
    from span_panel_api.protocol import SchemaAdapter

# How long to allow for each stage. The rebuild threshold is three failures
# with 1s/2s/4s backoff, so a sustained outage needs headroom past ~7s.
DISCONNECT_TIMEOUT_S = 15.0
REBUILD_TIMEOUT_S = 45.0
RECOVERY_TIMEOUT_S = 60.0
# Retained messages arrive in a burst on re-subscribe. Ongoing traffic is only
# distinguishable from that burst once it has drained.
BURST_DRAIN_S = 3.0
LIVENESS_WINDOW_S = 5.0


# ---------------------------------------------------------------------------
# The severable link
# ---------------------------------------------------------------------------


class SeverableLink:
    """A TCP passthrough to the broker that can be cut and restored."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self._target_host = target_host
        self._target_port = target_port
        self.port = _free_port()
        self._server: asyncio.Server | None = None
        self._live: set[asyncio.StreamWriter] = set()

    async def open(self) -> None:
        """Start accepting connections on the reserved port."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port, reuse_address=True)

    async def cut(self) -> None:
        """Refuse new connections and drop every live one.

        Sockets are closed before the listener is awaited: ``wait_closed`` also
        waits for the handlers still pumping those sockets, so closing in the
        other order deadlocks.
        """
        for writer in list(self._live):
            with contextlib.suppress(OSError):
                writer.close()
        self._live.clear()
        server, self._server = self._server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), timeout=5.0)

    async def close(self) -> None:
        await self.cut()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(self._target_host, self._target_port)
        except OSError:
            writer.close()
            return
        self._live.update({writer, upstream_writer})
        try:
            await asyncio.gather(
                self._pump(reader, upstream_writer),
                self._pump(upstream_reader, writer),
            )
        finally:
            self._live.difference_update({writer, upstream_writer})

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while chunk := await reader.read(65536):
                writer.write(chunk)
                await writer.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(OSError):
                writer.close()


def _free_port() -> int:
    """Reserve a port number the link can rebind to after each cut."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class Report:
    """Accumulated check results."""

    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")
        return ok

    @property
    def failed(self) -> list[str]:
        return [name for name, ok, _ in self.checks if not ok]


# ---------------------------------------------------------------------------
# Snapshot comparison
# ---------------------------------------------------------------------------


def _fingerprint(snapshot: SpanPanelSnapshot) -> dict[str, object]:
    """Structure that must survive an outage unchanged.

    Deliberately excludes readings: power and energy are expected to move while
    the client is away, and demanding they match would test the panel's
    stability rather than the client's recovery.
    """
    return {
        "serial_number": snapshot.serial_number,
        "panel_size": snapshot.panel_size,
        "circuits": sorted(
            (circuit_id, circuit.name, tuple(circuit.tabs), circuit.device_type)
            for circuit_id, circuit in snapshot.circuits.items()
        ),
        "evse": sorted(snapshot.evse),
        "battery_serial": snapshot.battery.serial_number,
        "pv_product": snapshot.pv.product_name,
    }


def _describe_difference(before: dict[str, object], after: dict[str, object]) -> str:
    changed = [key for key in before if before[key] != after.get(key)]
    if not changed:
        return "identical"
    return "; ".join(f"{key}: {_brief(before[key])} -> {_brief(after.get(key))}" for key in changed)


def _brief(value: object) -> str:
    """A value short enough to read in a result line."""
    if isinstance(value, list):
        return f"{len(value)} entries" if len(value) > 3 else repr(value)
    return repr(value)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """A connected client plus the observations the checks are made from."""

    def __init__(self, client: SpanMqttClient) -> None:
        self.client = client
        self.edges: list[bool] = []
        self.dispatches = 0
        client.register_connection_callback(self.edges.append)
        client.register_snapshot_callback(self._count)

    async def _count(self, _snapshot: SpanPanelSnapshot) -> None:
        self.dispatches += 1


async def _wait_for(predicate: Callable[[], bool], timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


async def _snapshot_is_stale(client: SpanMqttClient) -> bool:
    try:
        await client.get_snapshot()
    except SpanPanelStaleDataError:
        return True
    return False


async def _measure_liveness(session: Session) -> int:
    """Count snapshot dispatches in a window past the retained burst."""
    await asyncio.sleep(BURST_DRAIN_S)
    before = session.dispatches
    await asyncio.sleep(LIVENESS_WINDOW_S)
    return session.dispatches - before


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def run_outage(session: Session, link: SeverableLink, report: Report, *, sustained: bool) -> None:
    """Cut the link, verify the client notices, restore, verify it recovers."""
    label = "sustained" if sustained else "brief"
    print(f"\n{label} outage")

    client = session.client
    before = _fingerprint(await client.get_snapshot())
    adapter_before: SchemaAdapter | None = client.adapter
    metadata_before = client.field_metadata
    edges_before = len(session.edges)

    await link.cut()

    noticed = await _wait_for(lambda: len(session.edges) > edges_before, DISCONNECT_TIMEOUT_S)
    report.check("client observes the outage", noticed and session.edges[edges_before] is False)
    report.check("snapshots report stale data during the outage", await _snapshot_is_stale(client))

    if sustained:
        swapped = await _wait_for(lambda: client.adapter is not adapter_before, REBUILD_TIMEOUT_S)
        report.check(
            f"parser rebuilt after {MQTT_FULL_REBUILD_AFTER_FAILURES} failed reconnects",
            swapped,
        )
        # Checked before restoring: a fresh parser must be empty, and once the
        # link is back the retained burst would fill it within milliseconds.
        fresh = client.adapter
        report.check(
            "rebuilt parser starts empty",
            fresh is not None and not fresh.is_ready(),
        )
    else:
        report.check(
            "parser instance survives a brief outage",
            client.adapter is adapter_before,
        )

    await link.open()

    reconnected = await _wait_for(lambda: len(session.edges) > edges_before + 1, RECOVERY_TIMEOUT_S)
    report.check("client reconnects", reconnected and session.edges[-1] is True)

    adapter = client.adapter
    ready = await _wait_for(lambda: adapter is not None and adapter.is_ready(), RECOVERY_TIMEOUT_S)
    report.check("parser repopulates from retained state", ready)

    if not ready:
        return

    after = _fingerprint(await client.get_snapshot())
    report.check(
        "panel is described identically after recovery",
        after == before,
        _describe_difference(before, after),
    )
    report.check(
        "field metadata survives the outage",
        client.field_metadata == metadata_before,
    )

    dispatched = await _measure_liveness(session)
    report.check(
        "live updates resume once the retained burst has drained",
        dispatched > 0,
        f"{dispatched} snapshots in {LIVENESS_WINDOW_S:.0f}s",
    )


async def check_callback_contract(session: Session, report: Report, *, rebuilt: bool) -> None:
    """Property callbacks are registered on the parser, not the transport.

    A rebuild replaces the parser, so callbacks registered on the old instance
    are gone — documented on ``SpanMqttClient.adapter`` and load-bearing for the
    integration, which must re-register. Worth asserting rather than trusting.
    """
    print("\nproperty callback contract")
    adapter = session.client.adapter
    if adapter is None:
        report.check("adapter present", False)
        return

    seen: list[str] = []
    unregister = adapter.register_property_callback(lambda device, node, prop, value: seen.append(node))
    received = await _wait_for(lambda: bool(seen), LIVENESS_WINDOW_S)
    report.check(
        "callbacks registered on the current parser receive updates",
        received,
        f"{len(seen)} updates",
    )
    unregister()

    if rebuilt:
        report.check(
            "the parser that served the callback is the rebuilt one",
            adapter is session.client.adapter,
        )


# ---------------------------------------------------------------------------
# Seeding a broker from a captured tree
# ---------------------------------------------------------------------------


async def seed_broker(fixture: Path, host: str, port: int, stop: asyncio.Event) -> None:
    """Publish a captured device tree retained, then keep its meters moving.

    Stands in for a panel on a plain broker: the retained topics are what a
    reconnecting client replays, and the ticking meters are what proves live
    traffic resumed rather than merely the burst arriving.
    """
    import paho.mqtt.client as paho  # imported here so the flat path needs no seeder

    tree: dict[str, dict[str, str]] = json.loads(fixture.read_text(encoding="utf-8"))
    client = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2)
    client.connect(host, port, keepalive=60)
    client.loop_start()

    for device_id, topics in tree.items():
        for topic, payload in topics.items():
            client.publish(f"ebus/5/{device_id}/{topic}", payload, qos=0, retain=True)

    meters = [
        (device_id, float(topics["meter/active-power"]))
        for device_id, topics in tree.items()
        if "meter/active-power" in topics
    ]
    print(f"seeded {sum(len(t) for t in tree.values())} retained topics for {len(tree)} devices, ticking {len(meters)} meters")

    tick = 0
    while not stop.is_set():
        tick += 1
        for device_id, base in meters:
            client.publish(f"ebus/5/{device_id}/meter/active-power", f"{base + tick:.1f}", qos=0, retain=True)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)

    client.loop_stop()
    client.disconnect()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _load_factory(spec: str) -> Callable[[str, V2HomieSchema], SchemaAdapter]:
    """Resolve a ``module:attribute`` adapter factory.

    Needed while an adapter is deliberately unregistered: schema_1 ships no
    entry point until it has run against real hardware, so its factory has to
    be named to be exercised.
    """
    module_name, _, attribute = spec.partition(":")
    if not attribute:
        raise SystemExit(f"--adapter expects 'module:attribute', got {spec!r}")
    factory: Callable[[str, V2HomieSchema], SchemaAdapter] = getattr(importlib.import_module(module_name), attribute)
    return factory


def _synthetic_schema(data_model_version: str | None) -> V2HomieSchema:
    """A schema for brokers with no panel behind them.

    Only the discriminator matters here — the parser reads its structure from
    the tree, and field metadata comes from each device's own description.
    """
    return V2HomieSchema(
        firmware_version="unknown",
        types_schema_hash="sha256:synthetic",
        types={},
        data_model_version=data_model_version,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", required=True, help="Panel serial number (the Homie root device id)")
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, required=True)
    parser.add_argument("--broker-username", default="")
    parser.add_argument("--broker-password", default="")
    parser.add_argument("--no-tls", action="store_true", help="Plain TCP to the broker (no CA fetch)")
    parser.add_argument("--panel-host", help="Panel HTTP host; when given, the schema is fetched from it")
    parser.add_argument("--panel-http-port", type=int, default=80)
    parser.add_argument(
        "--data-model-version",
        help="Discriminator to use when no panel is available to fetch a schema from",
    )
    parser.add_argument("--adapter", help="Adapter factory as 'module:attribute'; omit to dispatch by entry point")
    parser.add_argument("--seed", type=Path, help="Captured tree to publish retained before connecting")
    parser.add_argument(
        "--scenario",
        choices=["brief", "sustained", "both"],
        default="both",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    report = Report()
    stop_seeder = asyncio.Event()
    seeder: asyncio.Task[None] | None = None

    if args.seed is not None:
        seeder = asyncio.create_task(seed_broker(args.seed, args.broker_host, args.broker_port, stop_seeder))
        await asyncio.sleep(2.0)  # let the retained tree land before connecting

    link = SeverableLink(args.broker_host, args.broker_port)
    await link.open()
    print(f"link: 127.0.0.1:{link.port} -> {args.broker_host}:{args.broker_port}")

    config = MqttClientConfig(
        broker_host="127.0.0.1",
        username=args.broker_username,
        password=args.broker_password,
        mqtts_port=link.port,
        use_tls=not args.no_tls,
    )
    client = SpanMqttClient(
        host=args.panel_host or args.broker_host,
        serial_number=args.serial,
        broker_config=config,
        snapshot_interval=0.25,
        panel_http_port=args.panel_http_port,
        adapter_factory=_load_factory(args.adapter) if args.adapter else None,
        schema=None if args.panel_host else _synthetic_schema(args.data_model_version),
    )
    session = Session(client)

    try:
        await client.connect()
        # Snapshot dispatch is what the integration actually consumes, and it
        # is gated on streaming — without this the liveness check measures
        # nothing.
        await client.start_streaming()
        snapshot = await client.get_snapshot()
        print(
            f"connected: {client.schema_major} / {snapshot.serial_number} / "
            f"{snapshot.panel_size} spaces / {len(snapshot.circuits)} circuits"
        )

        rebuilt = False
        if args.scenario in ("brief", "both"):
            await run_outage(session, link, report, sustained=False)
        if args.scenario in ("sustained", "both"):
            await run_outage(session, link, report, sustained=True)
            rebuilt = True
        await check_callback_contract(session, report, rebuilt=rebuilt)
    finally:
        await client.close()
        await link.close()
        if seeder is not None:
            stop_seeder.set()
            await seeder

    print()
    if report.failed:
        print(f"FAILED ({len(report.failed)}/{len(report.checks)}): {', '.join(report.failed)}")
        return 1
    print(f"All {len(report.checks)} checks passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
