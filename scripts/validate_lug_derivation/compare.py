"""Drive v1 and v2 readers in parallel; derive feedthrough from main minus
Σcircuits; print a side-by-side comparison for each sample.

Usage:

    python compare.py \
        --host 192.168.65.70 \
        --v1-token "$V1_TOKEN" \
        --v2-passphrase "$V2_PASSPHRASE" \
        --samples 5 --interval 3

Physics — Kirchhoff at the main bus (grid-perspective on main/feedthrough,
load-perspective on branch circuits where positive = consumption):

    P_main = P_feedthrough + Σ(branches, load-perspective)
    =>  P_feedthrough_derived = P_main - Σ(branches)

PV handling. A solar inverter connected to a branch appears as:
  * v1 REST: two raw physical tab circuits in grid-perspective (positive =
    power flowing INTO the bus from the inverter). No virtual PV entry.
  * v2 MQTT: one synthesized "PV" virtual circuit in load-perspective
    (negative = producing), AND the underlying physical tabs are suppressed.

The v1-only circuits (by UUID set-difference with v2) therefore identify the
physical PV tabs. To get comparable load-perspective totals, we negate them:

    Σ_v1_load = Σ_v1_raw - 2 * Σ(v1-only circuits)

Energy uses the Kirchhoff identity on NET counters:

    net_feedthrough = (main_consumed − main_produced)
                      − Σ(c.consumed − c.produced)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
SPAN_API_ROOT = HERE.parent.parent


def _fmt(x: float | None, width: int = 12) -> str:
    if x is None:
        return f"{'—':>{width}}"
    return f"{x:>{width}.2f}"


async def _run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"reader exited {proc.returncode}: {stderr_b.decode(errors='replace')}"
        )
    return json.loads(stdout_b.decode())


def _partition_v1(v1: dict[str, Any], shared_ids: set[str]) -> dict[str, float]:
    """Partition v1 circuits into 'load' (shared with v2) and 'pv_tabs' (v1-only,
    grid-perspective) and return comparable sums."""
    load_p = load_c = load_pe = 0.0
    pv_p = pv_c = pv_pe = 0.0
    for c in v1["circuits"]:
        p = float(c["instant_power_w"])
        cons = float(c["consumed_energy_wh"])
        prod = float(c["produced_energy_wh"])
        if c["circuit_id"] in shared_ids:
            load_p += p
            load_c += cons
            load_pe += prod
        else:
            pv_p += p
            pv_c += cons
            pv_pe += prod
    return {
        "load_power_w": load_p,
        "load_consumed_wh": load_c,
        "load_produced_wh": load_pe,
        "pv_tabs_power_w_grid": pv_p,
        "pv_tabs_consumed_wh_grid": pv_c,
        "pv_tabs_produced_wh_grid": pv_pe,
        # Load-perspective total for Kirchhoff: flip pv_tabs sign for power.
        # For energy we can't symmetrically swap consumed/produced without
        # knowing which counter corresponds to which direction in raw REST.
        # Power-space correction is what we need for Kirchhoff balance.
        "sigma_load_persp_power_w": load_p - pv_p,
        "sigma_all_raw_power_w": load_p + pv_p,
    }


def _sum_v2_circuits(v2: dict[str, Any]) -> dict[str, float]:
    p = sum(float(c["instant_power_w"]) for c in v2["circuits"])
    cons = sum(float(c["consumed_energy_wh"]) for c in v2["circuits"])
    prod = sum(float(c["produced_energy_wh"]) for c in v2["circuits"])
    return {
        "sigma_power_w": p,
        "sigma_consumed_wh": cons,
        "sigma_produced_wh": prod,
    }


def _print_sample(idx: int, v1: dict[str, Any], v2: dict[str, Any]) -> None:
    shared_ids = {c["circuit_id"] for c in v1["circuits"]} & {
        c["circuit_id"] for c in v2["circuits"]
    }
    p1 = _partition_v1(v1, shared_ids)
    s2 = _sum_v2_circuits(v2)

    main_v1 = float(v1["main_power_w"])
    main_v2 = float(v2["main_power_w"])
    feed_v1 = float(v1["feedthrough_power_w"])
    feed_v2 = float(v2["feedthrough_power_w"])

    # Derived feedthrough power (Kirchhoff, load-perspective Σ)
    derived_v1 = main_v1 - p1["sigma_load_persp_power_w"]
    derived_v2 = main_v2 - s2["sigma_power_w"]

    # Energy nets
    net_main_v1 = float(v1["main_consumed_wh"]) - float(v1["main_produced_wh"])
    net_main_v2 = float(v2["main_consumed_wh"]) - float(v2["main_produced_wh"])
    net_feed_rpt_v1 = float(v1["feedthrough_consumed_wh"]) - float(v1["feedthrough_produced_wh"])
    net_feed_rpt_v2 = float(v2["feedthrough_consumed_wh"]) - float(v2["feedthrough_produced_wh"])
    net_circ_v1 = p1["load_consumed_wh"] + p1["pv_tabs_consumed_wh_grid"] - (
        p1["load_produced_wh"] + p1["pv_tabs_produced_wh_grid"]
    )
    net_circ_v2 = s2["sigma_consumed_wh"] - s2["sigma_produced_wh"]
    net_feed_der_v1 = net_main_v1 - net_circ_v1
    net_feed_der_v2 = net_main_v2 - net_circ_v2

    dt = float(v2["t"]) - float(v1["t"])
    print(f"\n=== sample {idx}  (v2 vs v1 capture offset: {dt:+.2f}s) ===")
    print(f"  shared circuits: {len(shared_ids)}   "
          f"v1-only (PV tabs): {len(v1['circuits']) - len(shared_ids)}   "
          f"v2-only (PV virtual): {len(v2['circuits']) - len(shared_ids)}")

    pv = v2.get("pv") or {}
    if pv.get("feed_circuit_id"):
        print(f"  v2 pv: feed={pv['feed_circuit_id'][:8]}  "
              f"vendor={pv.get('vendor_name')}  "
              f"capacity={pv.get('nameplate_capacity_w')} W  "
              f"position={pv.get('relative_position')}")

    print("\n  power (W):")
    print(f"{'    field':<44}{'v1':>12}{'v2':>12}{'Δ(v2-v1)':>12}")
    rows_p: list[tuple[str, float, float]] = [
        ("main_power_w", main_v1, main_v2),
        ("feedthrough_power_w (reported)", feed_v1, feed_v2),
        ("Σ circuits (raw, v1 grid+load mixed)",
         p1["sigma_all_raw_power_w"], s2["sigma_power_w"]),
        ("Σ circuits (load-perspective)",
         p1["sigma_load_persp_power_w"], s2["sigma_power_w"]),
        ("Σ v1-only / v2-only (PV)",
         p1["pv_tabs_power_w_grid"],
         sum(float(c["instant_power_w"]) for c in v2["circuits"]
             if c["circuit_id"] not in shared_ids)),
        ("feedthrough_power_w (derived)", derived_v1, derived_v2),
    ]
    for label, a, b in rows_p:
        print(f"{'    ' + label:<44}{_fmt(a)}{_fmt(b)}{_fmt(b - a)}")

    # v2-only: power flows indicators
    pfp = v2.get("power_flow_pv")
    pfb = v2.get("power_flow_battery")
    pfg = v2.get("power_flow_grid")
    pfs = v2.get("power_flow_site")
    print("\n  v2 power_flows (W):")
    print(f"    pv={_fmt(pfp, 9)}  battery={_fmt(pfb, 9)}  "
          f"grid={_fmt(pfg, 9)}  site={_fmt(pfs, 9)}")

    print("\n  energy net (Wh = consumed - produced):")
    print(f"{'    field':<44}{'v1':>12}{'v2':>12}{'Δ(v2-v1)':>12}")
    rows_e: list[tuple[str, float, float]] = [
        ("net_main", net_main_v1, net_main_v2),
        ("net_feedthrough (reported)", net_feed_rpt_v1, net_feed_rpt_v2),
        ("net_Σcircuits", net_circ_v1, net_circ_v2),
        ("net_feedthrough (derived)", net_feed_der_v1, net_feed_der_v2),
    ]
    for label, a, b in rows_e:
        print(f"{'    ' + label:<44}{_fmt(a)}{_fmt(b)}{_fmt(b - a)}")

    # Cross-api consistency: derived should match across v1 and v2
    # Reported may disagree with derived (the "defect").
    flags: list[str] = []
    if abs(derived_v1 - derived_v2) > 100.0:
        flags.append(
            f"derived feedthrough power diverges across APIs: "
            f"v1={derived_v1:+.1f} W vs v2={derived_v2:+.1f} W"
        )
    dp1 = derived_v1 - feed_v1
    dp2 = derived_v2 - feed_v2
    if abs(dp1) > 100.0:
        flags.append(f"v1 reported feedthrough off Kirchhoff by {dp1:+.1f} W")
    if abs(dp2) > 100.0:
        flags.append(f"v2 reported feedthrough off Kirchhoff by {dp2:+.1f} W   <<< MQTT defect")
    if float(v1["feedthrough_consumed_wh"]) < 0:
        flags.append(
            f"v1 feedthrough_consumed_wh is NEGATIVE ({v1['feedthrough_consumed_wh']:.0f}) — "
            f"counter cannot decrease"
        )
    de1 = net_feed_der_v1 - net_feed_rpt_v1
    de2 = net_feed_der_v2 - net_feed_rpt_v2
    if abs(de1) > 1000.0:
        flags.append(f"v1 reported net energy off Kirchhoff by {de1:+,.0f} Wh")
    if abs(de2) > 1000.0:
        flags.append(f"v2 reported net energy off Kirchhoff by {de2:+,.0f} Wh")

    for f in flags:
        print(f"  ! {f}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--v1-token", required=True)
    parser.add_argument("--v2-passphrase", required=True)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--dump-json", type=Path)
    args = parser.parse_args()

    v1_cmd = [
        "uv", "run", "--no-project", "--with", "span-panel-api==1.1.15",
        "python", str(HERE / "v1_reader.py"),
        "--host", args.host,
        "--token", args.v1_token,
        "--port", str(args.port),
        "--samples", str(args.samples),
        "--interval", str(args.interval),
    ]
    v2_cmd = [
        "uv", "run",
        "python", str(HERE / "v2_reader.py"),
        "--host", args.host,
        "--passphrase", args.v2_passphrase,
        "--port", str(args.port),
        "--samples", str(args.samples),
        "--interval", str(args.interval),
    ]

    v1_task = asyncio.create_task(_run(v1_cmd, cwd=HERE))
    v2_task = asyncio.create_task(_run(v2_cmd, cwd=SPAN_API_ROOT))
    v1_result, v2_result = await asyncio.gather(v1_task, v2_task)

    v1_samples = v1_result["samples"]
    v2_samples = v2_result["samples"]
    n = min(len(v1_samples), len(v2_samples))
    if n == 0:
        print("no samples captured", file=sys.stderr)
        return 1

    for i in range(n):
        _print_sample(i, v1_samples[i], v2_samples[i])

    if args.dump_json is not None:
        args.dump_json.write_text(
            json.dumps({"v1": v1_result, "v2": v2_result}, indent=2)
        )
        print(f"\nraw JSON written to {args.dump_json}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
