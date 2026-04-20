# validate_lug_derivation

Diagnostic harness that compares **v1 REST**, **v2 MQTT (via the current library)**, and **Kirchhoff-derived** values for the SPAN panel's downstream-lugs (feedthrough) power and energy. Used to:

1. Validate that the library's Kirchhoff derivation (introduced in `span-panel-api` 2.6.3) stays consistent with v1 REST as an independent ground truth.
2. **Track when the upstream firmware defect on `downstream-lugs` is fixed.** The SPAN API is in beta and carries no version signal, so we detect the fix empirically by watching the raw MQTT properties converge on the Kirchhoff-derived values. Upstream
   issue: [spanio/SPAN-API-Client-Docs#13](https://github.com/spanio/SPAN-API-Client-Docs/issues/13).

## Files

- **`v1_reader.py`** — Captures N v1-REST snapshots using an isolated `span-panel-api==1.1.15` env (the last version that still shipped the v1 client). Output: JSON on stdout.
- **`v2_reader.py`** — Captures N v2-MQTT snapshots from the current workspace install. Also reaches into the library's accumulator to grab the **raw** `downstream-lugs/active-power`, `imported-energy`, `exported-energy` — pre-derivation — for firmware-fix
  tracking.
- **`compare.py`** — Driver. Runs both readers in parallel via `uv run`, zips samples by index, prints a side-by-side table per sample, and flags anomalies.
- **`run_local.sh`** — Gitignored credentialed wrapper. Copy the template, edit the three placeholders, run.

## Setup

1. Obtain credentials for a live panel:

   - v1 token: existing pre-issued JWT (no re-registration needed).
   - v2 passphrase: `hopPassphrase` for `/api/v2/auth/register`.

2. Edit `run_local.sh` (already gitignored, so credentials won't leak):

   ```bash
   HOST="192.168.X.Y"
   V1_TOKEN="..."
   V2_PASSPHRASE="..."
   SAMPLES=5
   INTERVAL=3
   ```

3. Run: `./run_local.sh`.

Requires `uv`. The v1 reader is auto-provisioned via `uv run --no-project --with span-panel-api==1.1.15`; the v2 reader uses the current workspace install.

## Output — what to look for

Per sample, `compare.py` prints:

- **Power table** — `main_power_w`, reported vs derived feedthrough, Σcircuits partitioned (PV vs loads), for both APIs.
- **v2 `power_flows`** — the panel's own `pv/battery/grid/site` aggregates, as indicators.
- **v2 downstream-lugs raw (pre-derivation)** — the three raw MQTT properties the library **stopped** reading into the snapshot in 2.6.3. This is the firmware-fix tracker.
- **Energy net** — `(consumed − produced)` for main, reported feedthrough, Σcircuits, and derived feedthrough.
- **Flags** — anomalies (see next section).

Post-2.6.3, `feedthrough_power_w (reported)` equals `(derived)` by construction on v2 (the library derives internally). That's expected — convergence on that row is itself the confirmation signal that the library-side fix is working. The interesting row
for ongoing tracking is the **raw** block.

## Flags

- **`firmware downstream-lugs active-power still offset by X W vs Kirchhoff — upstream defect present`** — The raw MQTT `active-power` differs from the Kirchhoff-derived feedthrough by more than 100 W. Current state while the upstream firmware defect is
  unpatched.
- **`firmware downstream-lugs active-power within X W of Kirchhoff — upstream defect MAY be fixed (confirm over sustained samples)`** — The delta has dropped below 50 W. Could be sensor noise on a single sample — confirm across a longer run (e.g.
  `SAMPLES=30 INTERVAL=10`) before declaring the upstream fix has shipped.
- **`firmware downstream-lugs imported-energy is NEGATIVE (X Wh) — upstream counter still broken`** — The cumulative `imported-energy` counter went negative, which is physically impossible for a monotonic counter. Historically observed; current live panels
  sometimes emit positive values, so the flag is only armed when `< 0`.
- **`v1 feedthrough_consumed_wh is NEGATIVE (...)`** — The v1 REST feedthrough energy counter is broken on this panel too, independent of the MQTT defect. Included for completeness; v1 is not a viable fallback.
- **`v1 reported net energy off Kirchhoff by X Wh`** / **`v1 reported feedthrough off Kirchhoff by X W`** — Same shape of check on the v1 side. v1 active-power tends to track Kirchhoff within sensor noise; v1 energy diverges heavily due to the broken
  counter.
- **`derived feedthrough power diverges across APIs: v1=A vs v2=B`** — The Kirchhoff-derived values from v1 and v2 disagree by more than 100 W. Usually explained by sample-timing skew when load is shifting quickly; a persistent gap would warrant
  investigation of the sign-partitioning logic.

## When the firmware is fixed

Watch for the `active-power ... MAY be fixed` flag to fire on every sample across a sustained run (e.g. 30+ samples over several minutes). When that holds:

1. Verify `imported-energy` stays non-negative and its delta from the derived `consumed_wh` stabilizes near zero.
2. Consider whether the library should switch back to reading the native `downstream-lugs` values directly, or continue deriving. Deriving is robust regardless of firmware state, so the change is optional — potentially valuable only if the panel's own
   measurement is more precise than the computed one (which is not yet established).
