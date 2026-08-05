# span-panel-api-schema-1

Parent/child schema parser (`data-model-version` 1.x, SPAN firmware r202633+) for [span-panel-api](https://github.com/SpanPanel/span-panel-api).

**Status: incomplete.** This distribution does not yet register a `schema_1` adapter, so installing it does not make a parent/child panel work. A 1.x panel still raises `SpanPanelAdapterMissingError` naming `schema_1`, which is the honest answer until the
parser can build a snapshot.

What exists today is `BridgeControllerTransport` — an `ebus_sdk.MqttControllerTransport` backed by span-panel-api's own MQTT connection, so the eBus SDK can parse the parent/child tree while the connection to the panel's broker stays ours.
