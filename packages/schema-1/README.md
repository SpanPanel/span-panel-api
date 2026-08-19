# span-panel-api-schema-1

Parent/child schema parser (`data-model-version` 1.x, SPAN firmware r202633+) for [span-panel-api](https://github.com/SpanPanel/span-panel-api).

**Status: incomplete.** This distribution does not yet register a `schema_1` adapter, so installing it does not make a parent/child panel work. A 1.x panel still raises `SpanPanelAdapterMissingError` naming `schema_1`, which is the honest answer until the
parser can build a snapshot.

What exists today is `BridgeControllerTransport` — an `ebus_sdk.MqttControllerTransport` backed by span-panel-api's own MQTT connection, so the eBus SDK can parse the parent/child tree while the connection to the panel's broker stays ours.

## Reference payloads

A retained-topic capture of a full 40-space parent/child panel ships as package data, with the replay that turns it back into devices:

```python
from span_panel_api_schema_1.reference_payloads import devices_from_tree, parent_child_tree

devices = devices_from_tree(parent_child_tree())
```

It ships here rather than from the bootstrap because a retained topic tree is only interpretable by the parser that speaks its vocabulary, and the eBus SDK is this distribution's dependency alone. `devices_from_tree` takes the tree rather than reading it,
so a consumer can filter the capture first — dropping the BESS to model a panel that has none — and still build devices the same way. The bootstrap ships the schema document it fetches; see `span_panel_api.reference_payloads`.
