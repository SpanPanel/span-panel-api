# Reference payloads

Shipped as package data and read through `span_panel_api_schema_1.reference_payloads`, never by path — a consumer that installs this distribution gets these bytes, and a consumer that pins a version gets the bytes that version's parser was written against.

## `parent_child_tree.json`

Retained topics captured off a `panel_sim` parent/child tree for a 40-space panel: 13 devices — the panel, both lugs, a BESS with its MID, a PV, an EVSE, and the circuits.

Shape is `{device_id: {topic: payload}}`, every value a string, exactly as the broker retains them. `$description` is therefore a **JSON string**, not a nested object; `device_from_topics` replays it the way the transport does. `bess-mid` is typed
`energy.ebus.device.mid`, not `.bess` — a consumer filtering the tree by type marker has to expect the MID to survive a BESS filter.
