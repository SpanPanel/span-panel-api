"""Read N v2 (MQTT) snapshots from a live panel and print JSON to stdout.

Run from the span-panel-api workspace so the current editable install is used:

    uv run python v2_reader.py --host 192.168.X.Y --passphrase P \
        --samples 5 --interval 3

Emits a single JSON object: {"api": "v2", "samples": [{...}, ...]}.
Registers once, connects once, takes N snapshots at the requested interval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from span_panel_api import (
    MqttClientConfig,
    SpanCircuitSnapshot,
    SpanMqttClient,
    SpanPanelSnapshot,
    register_v2,
)

# Same-repo diagnostic access to library internals: the purpose of this
# script is to observe the raw panel-published downstream-lugs properties
# alongside the library's Kirchhoff-derived feedthrough values, so we can
# detect empirically when the upstream firmware defect is fixed (the SPAN
# API is in beta and does not carry a version signal).
from span_panel_api.mqtt.const import LUGS_DOWNSTREAM  # noqa: PLC2701


def _parse_float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _read_downstream_lugs_raw(client: SpanMqttClient) -> dict[str, float | None]:
    """Read the pre-derivation downstream-lugs MQTT properties.

    Reaches into the library's accumulator intentionally — these are the
    raw values the library *stopped reading into the snapshot* in 2.6.3.
    We surface them here so ``compare.py`` can flag when the firmware
    offset collapses (indicating SPAN has shipped the upstream fix).
    """
    homie = client._homie  # noqa: SLF001
    if homie is None:
        return {"active_power_w": None, "imported_energy_wh": None, "exported_energy_wh": None}
    node = homie._find_lugs_node(LUGS_DOWNSTREAM)  # noqa: SLF001
    acc = homie._acc  # noqa: SLF001
    if node is None:
        return {"active_power_w": None, "imported_energy_wh": None, "exported_energy_wh": None}
    return {
        "active_power_w": _parse_float_or_none(acc.get_prop(node, "active-power")),
        "imported_energy_wh": _parse_float_or_none(acc.get_prop(node, "imported-energy")),
        "exported_energy_wh": _parse_float_or_none(acc.get_prop(node, "exported-energy")),
    }


def _circuit_to_dict(circuit_id: str, c: SpanCircuitSnapshot) -> dict[str, object]:
    return {
        "circuit_id": circuit_id,
        "name": c.name,
        "device_type": c.device_type,
        "instant_power_w": c.instant_power_w,
        "consumed_energy_wh": c.consumed_energy_wh,
        "produced_energy_wh": c.produced_energy_wh,
        "tabs": list(c.tabs),
        "relay_state": c.relay_state,
    }


def _snapshot_to_dict(
    snap: SpanPanelSnapshot,
    downstream_raw: dict[str, float | None],
) -> dict[str, object]:
    return {
        "t": time.time(),
        "main_power_w": snap.instant_grid_power_w,
        "feedthrough_power_w": snap.feedthrough_power_w,
        "main_consumed_wh": snap.main_meter_energy_consumed_wh,
        "main_produced_wh": snap.main_meter_energy_produced_wh,
        "feedthrough_consumed_wh": snap.feedthrough_energy_consumed_wh,
        "feedthrough_produced_wh": snap.feedthrough_energy_produced_wh,
        "downstream_lugs_raw": downstream_raw,
        "power_flow_pv": snap.power_flow_pv,
        "power_flow_battery": snap.power_flow_battery,
        "power_flow_grid": snap.power_flow_grid,
        "power_flow_site": snap.power_flow_site,
        "upstream_l1_current_a": snap.upstream_l1_current_a,
        "upstream_l2_current_a": snap.upstream_l2_current_a,
        "downstream_l1_current_a": snap.downstream_l1_current_a,
        "downstream_l2_current_a": snap.downstream_l2_current_a,
        "pv": {
            "feed_circuit_id": snap.pv.feed_circuit_id,
            "vendor_name": snap.pv.vendor_name,
            "product_name": snap.pv.product_name,
            "nameplate_capacity_w": snap.pv.nameplate_capacity_w,
            "relative_position": snap.pv.relative_position,
        },
        "battery": {
            "connected": snap.battery.connected,
            "soe_kwh": snap.battery.soe_kwh,
            "soe_percentage": snap.battery.soe_percentage,
        },
        "evse_node_ids": list(snap.evse.keys()),
        "circuits": [_circuit_to_dict(cid, c) for cid, c in snap.circuits.items()],
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--passphrase", required=True)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()

    auth = await register_v2(
        host=args.host,
        name="validate-lug-derivation",
        passphrase=args.passphrase,
        port=args.port,
    )

    broker = MqttClientConfig(
        broker_host=auth.ebus_broker_host,
        username=auth.ebus_broker_username,
        password=auth.ebus_broker_password,
        mqtts_port=auth.ebus_broker_mqtts_port,
        ws_port=auth.ebus_broker_ws_port,
        wss_port=auth.ebus_broker_wss_port,
    )

    client = SpanMqttClient(
        host=args.host,
        serial_number=auth.serial_number,
        broker_config=broker,
        panel_http_port=args.port,
    )

    samples: list[dict[str, object]] = []
    try:
        await client.connect()
        for i in range(args.samples):
            if i > 0:
                await asyncio.sleep(args.interval)
            snap = await client.get_snapshot()
            raw = _read_downstream_lugs_raw(client)
            samples.append(_snapshot_to_dict(snap, raw))
    finally:
        await client.close()

    json.dump({"api": "v2", "samples": samples}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
