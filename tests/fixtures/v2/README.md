# v2 API Test Fixtures

Captured from a live SPAN Panel running firmware `spanos2/r202603/05`. Serial numbers are masked (last 4 chars replaced with `XXXX`).

## Files

| File          | Source               | Notes                                    |
| ------------- | -------------------- | ---------------------------------------- |
| `status.json` | `GET /api/v2/status` | v2 status probe response. Serial masked. |

## Moved

`homie_schema.json` is package data of `span-panel-api-schema-0`, at `span_panel_api_schema_0/reference/homie_schema.json`, and is read through `reference_payloads.bootstrap.homie_schema()`. Nothing at runtime reads it; it ships so a downstream test suite
pinned to a version of that adapter reads the same bytes it was tested against. Its provenance, schema hash and node-type table live in [`tests/reference_payloads/README.md`](../../reference_payloads/README.md), beside the loader.
