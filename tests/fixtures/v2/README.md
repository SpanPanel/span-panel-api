# v2 API Test Fixtures

Captured from a live SPAN Panel running firmware `spanos2/r202603/05`. Serial numbers are masked (last 4 chars replaced with `XXXX`).

## Files

| File                | Source                     | Notes                                                                                   |
| ------------------- | -------------------------- | --------------------------------------------------------------------------------------- |
| `homie_schema.json` | `GET /api/v2/homie/schema` | Complete Homie property schema. Unauthenticated. Schema hash: `sha256:d347556a07d98f40` |
| `status.json`       | `GET /api/v2/status`       | v2 status probe response. Serial masked.                                                |

## Schema Hash

`sha256:d347556a07d98f40` — use this to detect schema changes across firmware versions (compare against `typesSchemaHash` in live responses).

## Node Types Present

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
