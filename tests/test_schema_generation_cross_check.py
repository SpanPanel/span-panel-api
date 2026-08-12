"""The two schema-generation signals must agree, and disagreement must be loud.

The migration guide's "Schema-generation detection" carries one rule on two
transports: MQTT ``info/data-model-version`` absent = flat, present = parent/child;
REST ``dataModelVersion`` absent = flat, exactly mirroring the MQTT signal.

Dispatch reads REST, because the adapter chooses which topics to subscribe to and so
must exist before the first SUBSCRIBE. That left the MQTT value unread, and a
producer that published one and not the other went undetected: the client dispatched
on the REST answer, parsed the tree with the wrong parser, and reported a clean
connection. Every number in Home Assistant was wrong and nothing said so.

That is not hypothetical -- it is exactly what a parent/child simulator did when it
published `info/data-model-version` over MQTT while its REST schema omitted
`dataModelVersion`. These tests are what makes that state impossible to reach
quietly.
"""

from __future__ import annotations

import pytest

from span_panel_api.exceptions import SpanPanelSchemaVersionError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import SERIAL


def _client(*, reported: str | None, observed: str | None) -> SpanMqttClient:
    """A client with the two signals set, without connecting to anything.

    The cross-check reads only these two values, so driving a whole connect flow to
    reach it would test the mocking rather than the rule.
    """
    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        data_model_version=reported,
    )
    client._observed_data_model_version = observed
    return client


@pytest.mark.parametrize(
    ("reported", "observed", "why"),
    [
        (None, None, "flat panel: neither transport carries the property"),
        ("1.0", "1.0", "parent/child panel: both carry the same value"),
        ("1.0", "1.0.3", "same major, so the same parser reads both"),
        ("1.2", "1.0", "same major across a minor bump"),
    ],
)
def test_agreeing_signals_pass(reported: str | None, observed: str | None, why: str) -> None:
    """Agreement is by selected adapter, not by string equality.

    A patch or minor difference between the two reads is not a disagreement worth
    refusing a connection over: both values select the same parser, so no value in
    the tree can be misread. Comparing the strings would turn a routine firmware
    release into an outage.
    """
    _client(reported=reported, observed=observed)._assert_transports_agree_on_schema_generation()


@pytest.mark.parametrize(
    ("reported", "observed"),
    [
        (None, "1.0"),  # the simulator's actual failure: MQTT v1.0, REST silent
        ("1.0", None),  # the mirror image: REST claims v1.0, the tree is flat
        ("1.0", "2.0"),  # both present, different majors
    ],
)
def test_disagreeing_signals_raise(reported: str | None, observed: str | None) -> None:
    """Refusing follows the rule dispatch already applies to an unparseable version.

    An unknown schema generation means every value in the tree may be misread, so the
    blast radius is the whole panel rather than one property. A warning would leave a
    consumer running on wrong numbers; the error names both values so the offending
    transport is obvious without a packet capture.
    """
    client = _client(reported=reported, observed=observed)

    with pytest.raises(SpanPanelSchemaVersionError) as exc:
        client._assert_transports_agree_on_schema_generation()

    message = str(exc.value)
    assert repr(reported) in message
    assert repr(observed) in message


def test_an_unparseable_mqtt_value_is_reported_as_such() -> None:
    """A present-but-unreadable MQTT value is its own failure, not a silent pass.

    Dispatch already refused any unparseable *REST* value before the connection got
    this far, so an unparseable value here can only have come from MQTT -- and saying
    so is what stops the reader hunting through the REST response for it.
    """
    client = _client(reported=None, observed="not-a-version")

    with pytest.raises(SpanPanelSchemaVersionError, match="no adapter major"):
        client._assert_transports_agree_on_schema_generation()


def test_the_root_devices_property_is_the_one_observed() -> None:
    """A child's copy must not be mistaken for the panel's.

    Under parent/child every device has its own `info` node, so the topic is matched
    on the root serial rather than on the property name alone. Without that, a BESS
    or MID publishing the property would overwrite the panel's answer -- and it would
    do so non-deterministically, depending on retained-message ordering.
    """
    client = _client(reported="1.0", observed=None)

    client._on_message(f"ebus/5/{SERIAL}-bess/info/data-model-version", "9.9")
    assert client._observed_data_model_version is None, "a child's copy must be ignored"

    client._on_message(f"ebus/5/{SERIAL}/info/data-model-version", "1.0")
    assert client._observed_data_model_version == "1.0"
