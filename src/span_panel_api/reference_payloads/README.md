# Reference payloads

Shipped as package data and read through `span_panel_api.reference_payloads`, never by path — a consumer that installs this distribution gets these bytes, and a consumer that pins a version gets the bytes that version was written against.

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
