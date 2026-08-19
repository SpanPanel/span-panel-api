# v2 API Test Fixtures

Captured from a live SPAN Panel running firmware `spanos2/r202603/05`. Serial numbers are masked (last 4 chars replaced with `XXXX`).

## Files

| File          | Source               | Notes                                    |
| ------------- | -------------------- | ---------------------------------------- |
| `status.json` | `GET /api/v2/status` | v2 status probe response. Serial masked. |

## Moved

`homie_schema.json` is no longer a test fixture. It ships as package data at `src/span_panel_api/reference_payloads/homie_schema.json` and is read through `span_panel_api.reference_payloads.homie_schema()` — by this suite and by consumers alike, so there
is no copy anywhere that can go stale. Its provenance, schema hash and node-type table live in the README next to it.
