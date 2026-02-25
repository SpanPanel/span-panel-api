"""Protocol conformance tests.

Verifies that concrete transport classes satisfy the structural protocols
defined in span_panel_api.protocol. Uses runtime_checkable isinstance()
checks against minimal instances to validate method/property presence.

Note: SpanPanelClientProtocol has property members, so issubclass() cannot
be used (Python limitation). We construct minimal instances and use
isinstance() instead.
"""

from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig
from span_panel_api.protocol import (
    CircuitControlProtocol,
    SpanPanelClientProtocol,
    StreamingCapableProtocol,
)


def _make_mqtt_client() -> SpanMqttClient:
    """Build a minimal SpanMqttClient for protocol checks (no I/O)."""
    config = MqttClientConfig(
        broker_host="127.0.0.1",
        username="test",
        password="test",
    )
    return SpanMqttClient("127.0.0.1", "test-serial", config)


class TestMqttProtocolConformance:
    def test_satisfies_panel_client_protocol(self) -> None:
        client = _make_mqtt_client()
        if not isinstance(client, SpanPanelClientProtocol):
            raise TypeError("SpanMqttClient does not satisfy SpanPanelClientProtocol")

    def test_satisfies_circuit_control_protocol(self) -> None:
        if not issubclass(SpanMqttClient, CircuitControlProtocol):
            raise TypeError("SpanMqttClient does not satisfy CircuitControlProtocol")

    def test_satisfies_streaming_protocol(self) -> None:
        if not issubclass(SpanMqttClient, StreamingCapableProtocol):
            raise TypeError("SpanMqttClient does not satisfy StreamingCapableProtocol")
