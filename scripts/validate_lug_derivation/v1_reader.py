"""Read N v1 snapshots from a live panel and print JSON to stdout.

Run in an isolated environment with span-panel-api==1.1.15:

    uv run --no-project --with 'span-panel-api==1.1.15' \
        python v1_reader.py --host 192.168.X.Y --token T --samples 5 --interval 3

Emits a single JSON object: {"api": "v1", "samples": [{...}, ...]}.
Each sample records `t` (unix seconds), main power/energy, feedthrough
power/energy, and per-circuit instant_power_w / consumed_energy_wh /
produced_energy_wh.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from span_panel_api import SpanPanelClient


def _circuit_to_dict(circuit_id: str, circuit: object) -> dict[str, object]:
    return {
        "circuit_id": circuit_id,
        "name": getattr(circuit, "name", "") or "",
        "instant_power_w": float(getattr(circuit, "instant_power_w", 0.0)),
        "consumed_energy_wh": float(getattr(circuit, "consumed_energy_wh", None) or 0.0),
        "produced_energy_wh": float(getattr(circuit, "produced_energy_wh", None) or 0.0),
        "tabs": list(getattr(circuit, "tabs", None) or []),
        "relay_state": getattr(circuit, "relay_state").value,
    }


async def read_once(client: SpanPanelClient) -> dict[str, object]:
    panel = await client.get_panel_state()
    circuits_out = await client.get_circuits()

    main_energy = panel.main_meter_energy
    feed_energy = panel.feedthrough_energy

    circuits = [
        _circuit_to_dict(cid, c)
        for cid, c in circuits_out.circuits.additional_properties.items()
    ]

    return {
        "t": time.time(),
        "main_power_w": float(panel.instant_grid_power_w),
        "feedthrough_power_w": float(panel.feedthrough_power_w),
        "main_consumed_wh": float(main_energy.consumed_energy_wh),
        "main_produced_wh": float(main_energy.produced_energy_wh),
        "feedthrough_consumed_wh": float(feed_energy.consumed_energy_wh),
        "feedthrough_produced_wh": float(feed_energy.produced_energy_wh),
        "circuits": circuits,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()

    samples: list[dict[str, object]] = []
    async with SpanPanelClient(
        host=args.host, port=args.port, use_ssl=False, timeout=15.0
    ) as client:
        client.set_access_token(args.token)
        for i in range(args.samples):
            if i > 0:
                await asyncio.sleep(args.interval)
            samples.append(await read_once(client))

    json.dump({"api": "v1", "samples": samples}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
