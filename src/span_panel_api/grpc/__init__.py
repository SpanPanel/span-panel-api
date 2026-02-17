"""Gen3 gRPC transport subpackage for span-panel-api.

Requires the optional ``grpcio`` dependency::

    pip install span-panel-api[grpc]
"""

from .client import SpanGrpcClient
from .models import CircuitInfo, CircuitMetrics, PanelData

__all__ = [
    "CircuitInfo",
    "CircuitMetrics",
    "PanelData",
    "SpanGrpcClient",
]
