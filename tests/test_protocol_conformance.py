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
    EvseControlProtocol,
    PanelControlProtocol,
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

    def test_satisfies_panel_control_protocol(self) -> None:
        if not issubclass(SpanMqttClient, PanelControlProtocol):
            raise TypeError("SpanMqttClient does not satisfy PanelControlProtocol")

    def test_satisfies_evse_control_protocol(self) -> None:
        if not issubclass(SpanMqttClient, EvseControlProtocol):
            raise TypeError("SpanMqttClient does not satisfy EvseControlProtocol")

    def test_satisfies_streaming_protocol(self) -> None:
        if not issubclass(SpanMqttClient, StreamingCapableProtocol):
            raise TypeError("SpanMqttClient does not satisfy StreamingCapableProtocol")


def test_schema_adapter_declares_its_methods() -> None:
    """The protocol must name every method SpanMqttClient calls on its parser."""
    from span_panel_api.protocol import SchemaAdapter

    for name in (
        "topics_to_subscribe",
        "handle_message",
        "is_ready",
        "build_snapshot",
        "build_field_metadata",
        "circuit_nodes_missing_names",
        "find_node_by_type",
        "set_circuit_relay_target",
        "set_circuit_priority_target",
        "set_dominant_power_source_target",
        "dominant_power_source_payload",
        "set_evse_charge_limit_target",
        "evse_charge_limit_payload",
        "register_property_callback",
    ):
        assert hasattr(SchemaAdapter, name), f"SchemaAdapter is missing method {name}"


def test_schema_adapter_declares_its_class_attributes() -> None:
    """`schema_major` and `SUPPORTS_DATA_MODEL_VERSIONS` are annotation-only members.

    A bare annotation on a Protocol creates no class attribute, so `hasattr` is
    False for them even when correctly declared — they must be checked through
    `__annotations__` instead.
    """
    from span_panel_api.protocol import SchemaAdapter

    for name in ("schema_major", "SUPPORTS_DATA_MODEL_VERSIONS"):
        assert name in SchemaAdapter.__annotations__, f"SchemaAdapter is missing attribute {name}"


def test_schema_adapter_construction_signature_matches_its_implementation() -> None:
    """Construction is part of the contract, so it must be checked like the rest.

    `hasattr(SchemaAdapter, "__init__")` is vacuous — every object has one. The
    assertion with teeth is that the protocol's declared signature and the
    installed adapter's actual signature agree, which is what the transport
    depends on when it calls a class resolved from the entry-point registry.
    """
    import inspect

    from span_panel_api_schema_0 import SchemaZeroAdapter
    from span_panel_api.protocol import SchemaAdapter

    declared = list(inspect.signature(SchemaAdapter.__init__).parameters)
    implemented = list(inspect.signature(SchemaZeroAdapter.__init__).parameters)

    assert declared == ["self", "serial_number", "schema"]
    assert implemented == declared, f"SchemaZeroAdapter.__init__{implemented} does not match the protocol {declared}"


def test_adapter_missing_error_reports_what_is_installed() -> None:
    from span_panel_api.exceptions import SpanPanelAdapterMissingError

    err = SpanPanelAdapterMissingError(needed="schema_1", reason="data-model-version='1.0'", available=["schema_0"])
    assert err.needed == "schema_1"
    assert err.available == ["schema_0"]
    assert "schema_1" in str(err)
    assert "schema_0" in str(err)
