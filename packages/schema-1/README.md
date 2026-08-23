# span-panel-api-schema-1

The **parent/child** schema parser for [`span-panel-api`](https://github.com/SpanPanel/span-panel-api): the multi-device Homie tree published by SPAN firmware `r202633+`, which reports `data-model-version` `1.x`.

## Why this is a separate distribution

`span-panel-api` is a transport and a dispatcher. It knows how to connect to a panel's MQTT broker, route messages, and choose a parser — but it contains no parsing code. Each wire format ships as its own distribution and registers itself under the
`span_panel_api.schema_adapters` entry-point group, so the bootstrap never imports this package until a panel asks for it by name.

The split matters more here than anywhere else in the workspace: this parser depends on the [eBus SDK](https://github.com/electrification-bus/python-sdk) to turn the tree back into devices, and that dependency is this distribution's alone. A flat-panel
install never pulls it in.

## Installation

```console
pip install "span-panel-api[schema-1]"
```

Installing this package is what makes parent/child panels work. `span-panel-api` on its own will connect and then raise `SpanPanelAdapterMissingError` naming the adapter it could not find.

A consumer that wants to support panels on either schema installs both adapters, and dispatch happens at runtime, per panel, from the `data-model-version` the panel reports:

```console
pip install "span-panel-api[schema-0,schema-1]"
```

## What it parses

`SchemaOneAdapter` maps the device tree onto the same `SpanPanelSnapshot` the flat adapter produces — circuits, both lugs devices, the BESS, PV and EVSE — plus the surface that only exists under the parent/child model:

- **The MID** (`SpanPanelSnapshot.mid`). The enclosure model puts the `grid` capability on a Microgrid Interconnect Device rather than on the enclosure, so islanding state, grid state and the grid-forming entity live there.
- **Adopted devices** (`SpanPanelSnapshot.adopted_devices`). A device type this parser models nothing for is reported whole — identity and readings — rather than dropped. The schema is explicitly vendor-extensible, so an unmodelled device is an expected
  arrival rather than a hypothetical one.
- **Extension properties** (`SpanPanelSnapshot.extension_properties`). A vendor property on a device this parser _does_ model, carried with its value and the snapshot subject it hangs off.

Devices are sorted by declared device type, never by device id. Field metadata comes from each device's own `$description` rather than from a schema document, because the same capability exposes different properties on different device classes — `meter` is
voltage on the panel, power and energy on a circuit, and both currents on the lugs.

`ControllerRoutes` is how the eBus SDK reaches the panel without opening its own connection: it is an `ebus_sdk.MqttControllerTransport` that records `Controller`'s subscriptions instead of making them, so a single wildcard subscription owned by
span-panel-api's transport covers the whole tree and each message is routed to whichever SDK callback asked for it.

## Conformance

`spec_lock.json` ships with the package and records what this parser targets: the firmware range, the eBus specification commit its vocabulary was read from, and the version of every capability, device and registry it implements. The capability catalogs it
addresses are byte-copied under `spec/`.

Those copies exist to be **checked against, never parsed in production** — units and datatypes come from each device's `$description`, since a catalog is the superset across all hardware rather than a statement about the panel in front of you. The suite
asks the consumer's question rather than the publisher's: is every name this adapter _reads_ one the specification defines? A consumer addressing a name that no longer exists does not fail loudly, it goes quiet — the property never arrives, metadata lookup
returns `None`, and an entity disappears.

## Reference payloads

A retained-topic capture of a full 40-space parent/child panel ships as package data, with the replay that turns it back into devices:

```python
from span_panel_api_schema_1.reference_payloads import devices_from_tree, parent_child_tree

devices = devices_from_tree(parent_child_tree())
```

It ships here rather than from the bootstrap because a retained topic tree is only interpretable by the parser that speaks its vocabulary, and the eBus SDK is this distribution's dependency alone. `devices_from_tree` takes the tree rather than reading it,
so a consumer can filter the capture first — dropping the BESS to model a panel that has none — and still build devices the same way. The bootstrap ships the schema document it fetches; see `span_panel_api.reference_payloads`.

Each payload carries the version of the release it shipped in. Pin a version and you read the bytes that version was written against.
