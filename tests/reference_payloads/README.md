# Reference payloads

Captures of what a panel actually serves, read by this repository's test suite through `reference_payloads.bootstrap` and `reference_payloads.schema_one`.

**The bytes are not here.** This directory holds the loaders; each capture is package data of the adapter that parses it, and the loaders read it with `importlib.resources`:

| Capture                  | Ships in                  | At                                                         |
| ------------------------ | ------------------------- | ---------------------------------------------------------- |
| `homie_schema.json`      | `span-panel-api-schema-0` | `span_panel_api_schema_0/reference/homie_schema.json`      |
| `parent_child_tree.json` | `span-panel-api-schema-1` | `span_panel_api_schema_1/reference/parent_child_tree.json` |

**Test-support data, shipped deliberately, and never read at runtime.** No path in either distribution opens these files; a real consumer gets the schema document from the panel and the tree off a broker. They ship so that a downstream test suite pinned to
a version of an adapter reads the same bytes that version was built and tested against, out of its own site-packages. The alternative is what the integration was doing: vendoring copies, and then maintaining a guard to keep the copies honest — more
machinery than 59 KB in two wheels. That reverses the 3.1.0 decision, whose reasoning (no runtime path reads them) was true and turned out not to be the deciding cost. `tests/test_packaging.py` and CI both assert each adapter wheel carries its capture.

## `homie_schema.json`

The `GET /api/v2/homie/schema` response, captured from a live SPAN Panel running firmware `spanos2/r202603/05`. Unauthenticated endpoint. Serial numbers are masked (last 4 chars replaced with `XXXX`).

Schema hash `sha256:d347556a07d98f40` — compare against `typesSchemaHash` in a live response to detect a schema change across firmware versions. `span_panel_api_schema_0.const.SCHEMA_ANCHOR` is pinned to this value, and `tests/test_schema_provenance.py`
fails when the two diverge.

### Node types present

| Node Type                                        | Properties | Notes                                              |
| ------------------------------------------------ | ---------- | -------------------------------------------------- |
| `energy.ebus.device.distribution-enclosure.core` | 17         | Panel-wide state, network, hardware                |
| `energy.ebus.device.lugs`                        | 7          | Upstream (main meter) and downstream (feedthrough) |
| `energy.ebus.device.circuit`                     | 16         | Per-circuit — one node per commissioned circuit    |
| `energy.ebus.device.bess`                        | 12         | Battery — optional, only if commissioned           |
| `energy.ebus.device.pv`                          | 7          | Solar — optional, only if commissioned             |
| `energy.ebus.device.evse`                        | 9          | EV charger — optional, only if commissioned        |
| `energy.ebus.device.pcs`                         | 15         | Power Control System — optional                    |
| `energy.ebus.device.power-flows`                 | 4          | Aggregated power flows (W)                         |

## `parent_child_tree.json`

Retained topics captured off the eBus emitter's parent/child tree for a 40-space panel: 14 devices — the panel, both lugs, a BESS with its MID, a PV, an EVSE, and the circuits.

Shape is `{device_id: {topic: payload}}`, every value a string, exactly as the broker retains them. `$description` is therefore a **JSON string**, not a nested object; `device_from_topics` replays it the way the transport does. `bess-mid` is typed
`energy.ebus.device.mid`, not `.bess` — a reader filtering the tree by type marker has to expect the MID to survive a BESS filter.

`devices_from_tree` and `device_from_topics` live in `schema_one.py` beside the capture rather than in whichever test first needed them: a tree is not directly usable, every reader of it has to replay the retained topics through `DiscoveredDevice` first,
and splitting the two would put the same twelve lines in each of the modules that read it.

### Provenance

**No document states which release made this file, and that is the design.** `ebus-panel-sim` is pinned in `pyproject.toml`'s `dev` group, and `test_the_shipped_reference_tree_is_what_the_pinned_emitter_produces` regenerates the capture in-process on every
run and compares it to the committed bytes. A written record can go stale in silence — that is exactly how this tree went three emitter releases out of date while thirty test files asserted a producer defect as fact. A regeneration cannot. Following a
release is two steps: bump the pin, run `uv run python scripts/capture_parent_child_reference.py`.

|                |                                                      |
| -------------- | ---------------------------------------------------- |
| Producer       | `ebus-panel-sim`, pinned in `pyproject.toml`         |
| Repository     | electrification-bus/distribution-enclosure-simulator |
| Capture script | `scripts/capture_parent_child_reference.py`          |
| Manifest       | `scripts/reference_panel.yaml`                       |

**The producer is the specification in runnable form.** `ebus-panel-sim` is published by electrification-bus — the organisation that writes the eBus specification — and is conformed against live panel output, so testing against it is correct. Its wheel
also carries the capability catalogs it publishes against, and `test_vendored_catalogs_are_byte_identical_to_the_emitters` compares those to the ones vendored under `packages/schema-1/spec/catalogs/` — so a disagreement between this parser and this capture
is a disagreement about one vocabulary rather than about two. See #161 and #162 for what depending on a frozen, unrecorded copy of it cost.

**The manifest is this repository's, not the emitter's example.** `scripts/reference_panel.yaml` is the capture's input, committed beside the script and existing in exactly one place: a capture whose input is not in the tree is the same class of problem as
one whose producer is not written down. It mirrors `examples/forty_tab_minimal.yaml` key for key so the two can be diffed, and marks its two deliberate divergences at the head of the file:

1. **Shed priorities.** The example commissions two circuits `NICE_TO_HAVE`, a REST-generation value with no v1.0 representation that the emitter degrades to `UNKNOWN` (electrification-bus/distribution-enclosure-simulator#51, open). Across the two
   production enclosures we hold captures from — 27 circuits — no panel has ever published `UNKNOWN`, so this manifest uses values a real panel publishes. **`UNKNOWN` is still a legal enum member and this parser must handle it**; that obligation comes from
   `load-shed` 0.3's declared `$format` and is tested from the catalog in `tests/test_schema_one_circuits.py`, not from this capture. Contract obligations come from the catalog; representativeness comes from the capture.
2. **Identity properties.** The BESS's part/serial/firmware, the MID's model, firmware and hardware version, and the PV's firmware are all published by real panels and unset in the example. A capture omitting them understates what a consumer has to parse —
   which is what left four library tests injecting those values by hand, reading as coverage while asking nothing about what a panel sends. Every value is synthetic (`example-40t-001`, `EXAMPLE-BESS-40T-001`).

The cost of that choice is real: the capture is no longer reproducible by running an example anyone can find in the emitter. The committed manifest is what buys it back.

**Shape-stable, not byte-stable.** Each `$description` carries a `version` minted from the wall clock, so all fourteen move on every recapture. Nothing reads it; a diff confined to those lines means the producer did not move. It is also the one field the
regeneration test normalises away — everything else is compared to the byte.
