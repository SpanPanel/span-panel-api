"""Constants for Gen3 SPAN panel gRPC transport."""

# gRPC connection
DEFAULT_GRPC_PORT: int = 50065
GRPC_SERVICE_PATH: str = "/io.span.panel.protocols.traithandler.TraitHandlerService"

# Trait IDs
TRAIT_BREAKER_GROUPS: int = 15
TRAIT_CIRCUIT_NAMES: int = 16
TRAIT_BREAKER_CONFIG: int = 17
TRAIT_POWER_METRICS: int = 26
TRAIT_RELAY_STATE: int = 27
TRAIT_BREAKER_PARAMS: int = 31

# Vendor/Product IDs
VENDOR_SPAN: int = 1
PRODUCT_GEN3_PANEL: int = 4
PRODUCT_GEN3_GATEWAY: int = 5

# Metric IID offset: circuit N -> metric IID = N + METRIC_IID_OFFSET
METRIC_IID_OFFSET: int = 27

# Main feed IID (always 1 for trait 26)
MAIN_FEED_IID: int = 1

# Voltage threshold for breaker state detection (millivolts).
# Below this value the breaker is considered OFF.
BREAKER_OFF_VOLTAGE_MV: int = 5000  # 5 V
