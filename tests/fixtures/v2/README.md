# v2 API Test Fixtures

Captured from a live SPAN Panel running firmware `spanos2/r202603/05`. Serial numbers are masked (last 4 chars replaced with `XXXX`).

## Files

| File          | Source               | Notes                                    |
| ------------- | -------------------- | ---------------------------------------- |
| `status.json` | `GET /api/v2/status` | v2 status probe response. Serial masked. |

## Moved

`homie_schema.json` lives at [`tests/reference_payloads/homie_schema.json`](../../reference_payloads/README.md) and is read through `reference_payloads.bootstrap.homie_schema()`. It was package data under `src/span_panel_api/` between 3.0.0 and 3.1.0;
nothing at runtime read it there, so it is an ordinary fixture again. Its provenance, schema hash and node-type table live in the README next to it.
