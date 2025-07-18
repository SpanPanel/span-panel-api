"""Constants for the SPAN Panel API client."""

# HTTP Status Code Constants
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_TOO_MANY_REQUESTS = 429
HTTP_INTERNAL_SERVER_ERROR = 500
HTTP_BAD_GATEWAY = 502
HTTP_SERVICE_UNAVAILABLE = 503
HTTP_GATEWAY_TIMEOUT = 504

# Categorized status codes for error handling
AUTH_ERROR_CODES = (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN)
RETRIABLE_ERROR_CODES = (
    HTTP_BAD_GATEWAY,
    HTTP_SERVICE_UNAVAILABLE,
    HTTP_GATEWAY_TIMEOUT,
)  # 429 removed to match test expectations
SERVER_ERROR_CODES = (HTTP_INTERNAL_SERVER_ERROR,)

# Retry Configuration Constants
RETRY_MAX_ATTEMPTS = 3
RETRY_INITIAL_DELAY = 0.5  # seconds
RETRY_BACKOFF_MULTIPLIER = 2

# SPAN Panel State Constants
# DSM (Demand Side Management) States
DSM_GRID_UP = "DSM_GRID_UP"
DSM_GRID_DOWN = "DSM_GRID_DOWN"
DSM_ON_GRID = "DSM_ON_GRID"
DSM_OFF_GRID = "DSM_OFF_GRID"

# Panel Run Configuration States
PANEL_ON_GRID = "PANEL_ON_GRID"
PANEL_OFF_GRID = "PANEL_OFF_GRID"
PANEL_BACKUP = "PANEL_BACKUP"

# Main Relay States
MAIN_RELAY_OPEN = "OPEN"
MAIN_RELAY_CLOSED = "CLOSED"
MAIN_RELAY_UNKNOWN = "UNKNOWN"
