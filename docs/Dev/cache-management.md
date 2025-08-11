## Cache Management in SpanPanelClient

This document describes the client's caching system, focusing on how regular and unmapped circuits are handled to provide a deterministic and complete view of the panel state.

### Core Concepts: Regular vs. Unmapped Circuits

Understanding the two types of circuits is key to understanding the cache logic.

**Definitions:**

- **Regular circuits:** Entries returned by the panel API under `circuits.circuits.additional_properties`. They represent configured/bound circuits and may include tab mappings via `tabs`.
- **Unmapped circuits:** Virtual entries synthesized by the client for any panel tabs that are not mapped to a regular circuit. IDs are generated as `unmapped_tab_#`. They mirror branch metrics for the associated tab and ensure complete coverage of panel
  tabs in circuit views.

**Source of data:**

- **Regular circuits:** Server-sourced (live) or YAML-sourced (simulation), then parsed into `CircuitsOut`.
- **Unmapped circuits:** Client-synthesized from `panel_state.branches` via `_create_unmapped_tab_circuit(...)`.

**Why unmapped circuits exist:**

- To guarantee that per-tab power/energy is represented even when no configured circuit owns a tab.
- To keep totals consistent (e.g., sum of circuit power aligns with adjusted panel power where required).

### Caching Goals

- Reduce redundant API calls while keeping responses fresh.
- Keep behavior deterministic across live and simulation modes.
- Ensure circuit results include virtual entries for unmapped panel tabs when appropriate.

### How the Cache Works (`TimeWindowCache`)

The client uses a simple time-window cache (`TimeWindowCache`) with these properties:

- Window duration configured via `cache_window` (seconds). Default: 1.0.
- `cache_window = 0` disables caching completely.
- On successful fetch, the result is stored and is considered valid until the window expires.
- After expiration, the next request fetches fresh data and starts a new window.
- Not thread-safe by design; intended for single-threaded async usage.

**Cache API (internal):**

- `get_cached_data(key) -> Any | None`
- `set_cached_data(key, data) -> None`
- `clear() -> None` (clears all keys)

### Cache Keys by Endpoint/Mode

- **Live status:** `"status"`
- **Live panel state:** `"panel_state"`
- **Live circuits:** `"circuits"`
- **Live storage SOE:** `"storage_soe"`
- **Simulation:** A single snapshot of the full YAML-backed dataset is cached under `"full_sim_data"` containing both `panel` and `circuits` payloads; additional per-feature keys:
  - **Simulation status:** `"status_sim"`
  - **Simulation storage SOE:** `"storage_soe_sim"`

### Endpoint Behavior and Cache Interaction

- **`get_status()`**
  - **Live:** Caches under `status`. Returns cached when window is active.
  - **Simulation:** Caches under `status_sim`.

- **`get_panel_state()`**
  - **Live:** Caches under `panel_state`. Returns cached when window is active.
  - **Simulation:** Caches full dataset under `full_sim_data` and returns `panel` portion.

- **`get_circuits()`**
  - **Live:** Caches under `circuits`.
    - **On cache hit:** Deterministically ensures unmapped circuits are present by recomputing against cached `panel_state` (if available) and injecting any missing `unmapped_tab_#` entries into the cached result before returning it. The cache is **not**
      invalidated solely due to the absence of unmapped circuits.
    - **On cache miss:** Fetches circuits, fetches `panel_state`, computes unmapped circuits, caches the augmented result, and returns it.
  - **Simulation:** Uses `full_sim_data` to keep `panel` and `circuits` consistent, then applies the same unmapped-circuit computation as live mode.

- **`get_storage_soe()`**
  - **Live:** Caches under `storage_soe`.
  - **Simulation:** Caches under `storage_soe_sim`.

**Edge cases and determinism:**

Cache validity does not depend on the presence of unmapped circuits. If a cached `panel_state` exists, unmapped circuits are deterministically recomputed on cache hits. If not, the cached circuits are returned as-is. This removes false cache invalidations
while keeping the invariant that the returned circuits view is complete and tab-aligned.

### Invalidation Triggers

- **Time window expiration:** Any key becomes invalid after `cache_window` seconds.
- **Simulation overrides:** Calling `set_circuit_overrides(...)` or `clear_circuit_overrides()` clears the entire cache because behavior changes.
- **Authentication changes:** Updating the access token does not clear the cache; this is an intentional trade-off since core panel data is not user-specific.

### Retry Interaction

- Retries can extend the time before a successful response; if a retry completes after a window expires, the next success starts a fresh window. This is acceptable and expected.

### Tuning Guidance

- `cache_window = 0`: Disables caching for fully live behavior.
- `cache_window ~ 0.5 - 2.0`: Typical for reducing redundant calls while keeping UI responsive.
- Longer windows increase staleness risk but reduce network traffic.

### Testing Notes

- For live-circuits tests, prefer providing a `panel_state` with `branches` and circuits with realistic `additional_properties`. On cache hits, unmapped circuits will be recomputed using the cached `panel_state`.
- If asserting cache hits by call counts, ensure the first response is cacheable and that the second call occurs within the active window.
