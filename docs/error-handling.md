# Error Handling and Retry

## Exception Hierarchy

All exceptions inherit from `SpanPanelError`.

```text
SpanPanelError
├── SpanPanelAuthError          — authentication failures (401, 403)
├── SpanPanelConnectionError    — network errors or unreachable panel
├── SpanPanelTimeoutError       — request timeout
├── SpanPanelValidationError    — invalid input or schema mismatch
├── SpanPanelAPIError           — general API error (catch-all for HTTP errors)
├── SpanPanelRetriableError     — transient server errors (502, 503, 504)
├── SpanPanelServerError        — non-retriable server error (500)
├── SpanPanelGrpcError          — base for Gen3 gRPC errors
│   └── SpanPanelGrpcConnectionError  — Gen3 connection failure
└── SimulationConfigurationError — invalid simulation config (simulation mode only)
```

### Import

```python
from span_panel_api import (
    SpanPanelError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
    SpanPanelAPIError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelGrpcError,
    SpanPanelGrpcConnectionError,
    SimulationConfigurationError,
)
```

## HTTP Error → Exception Mapping (Gen2)

| HTTP Status        | Exception                      | Retriable            | Action                         |
| ------------------ | ------------------------------ | -------------------- | ------------------------------ |
| 401, 403           | `SpanPanelAuthError`           | Once (after re-auth) | Re-authenticate then retry     |
| 500                | `SpanPanelServerError`         | No                   | Check server; report issue     |
| 502, 503, 504      | `SpanPanelRetriableError`      | Yes                  | Retry with exponential backoff |
| 404, 400, etc.     | `SpanPanelAPIError`            | Case-by-case         | Check request parameters       |
| Timeout            | `SpanPanelTimeoutError`        | Yes                  | Retry with backoff             |
| Validation failure | `SpanPanelValidationError`     | No                   | Fix input data                 |
| Simulation config  | `SimulationConfigurationError` | No                   | Fix simulation config file     |

The underlying HTTP client is configured with `raise_on_unexpected_status=True`, so unexpected status codes are never silently ignored.

## Handling Errors in Practice

```python
from span_panel_api import (
    SpanPanelAuthError,
    SpanPanelRetriableError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
    SpanPanelAPIError,
)

async def fetch_circuits(client):
    try:
        return await client.get_circuits()
    except SpanPanelAuthError:
        # Token expired or not yet authenticated — re-auth and retry once
        await client.authenticate("my-app", "My Application")
        return await client.get_circuits()
    except SpanPanelRetriableError as exc:
        # Temporary server overload — let retry logic or coordinator handle this
        logger.warning("Transient server error, will retry: %s", exc)
        raise
    except SpanPanelTimeoutError as exc:
        # Network too slow — retry after backoff
        logger.warning("Request timed out: %s", exc)
        raise
    except SpanPanelValidationError as exc:
        # Unexpected response structure — not retriable
        logger.error("Validation error: %s", exc)
        raise
    except SpanPanelAPIError as exc:
        # Any other API error
        logger.error("API error: %s", exc)
        raise
```

## Retry Configuration (Gen2)

Configure retries on the client to handle transient network issues automatically:

```python
from span_panel_api import SpanPanelClient

client = SpanPanelClient(
    "192.168.1.100",
    timeout=10.0,
    retries=3,              # 3 retries → up to 4 total attempts
    retry_timeout=0.5,      # initial delay before first retry
    retry_backoff_multiplier=2.0,  # delays: 0.5s, 1.0s, 2.0s
)
```

Only `SpanPanelRetriableError` and `SpanPanelTimeoutError` trigger automatic retries. `SpanPanelAuthError` and `SpanPanelValidationError` are not retried automatically.

### Retry Attempt Count

| `retries`   | Total attempts |
| ----------- | -------------- |
| 0 (default) | 1              |
| 1           | 2              |
| 2           | 3              |
| 3           | 4              |

Settings can be changed at runtime:

```python
client.retries = 2
client.retry_timeout = 1.0
client.retry_backoff_multiplier = 1.5
```

## Gen3 gRPC Errors

Gen3 errors use a separate, simpler hierarchy since gRPC does not use HTTP status codes:

```python
from span_panel_api import SpanPanelGrpcError, SpanPanelGrpcConnectionError

try:
    await client.connect()
    snapshot = await client.get_snapshot()
except SpanPanelGrpcConnectionError as exc:
    # Panel unreachable or gRPC channel failed
    logger.error("Gen3 connection failed: %s", exc)
except SpanPanelGrpcError as exc:
    # Other gRPC-level errors
    logger.error("Gen3 gRPC error: %s", exc)
```

Gen3 does not have built-in retry logic — reconnect handling should be implemented at the integration layer (e.g., the Home Assistant coordinator).
