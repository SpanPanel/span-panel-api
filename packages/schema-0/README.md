# span-panel-api-schema-0

The **flat-schema** parser for [`span-panel-api`](https://github.com/SpanPanel/span-panel-api): the single-device Homie model published by SPAN firmware `r202603` through `r202627`, which carries no `data-model-version`.

## Why this is a separate distribution

`span-panel-api` is a transport and a dispatcher. It knows how to connect to a panel's MQTT broker, route messages, and choose a parser — but it contains no parsing code and no Homie type strings. Each wire format ships as its own distribution and
registers itself under the `span_panel_api.schema_adapters` entry-point group.

That split exists because the two halves break on different axes. The wire format changes when SPAN ships firmware; the library API changes when we do. Separate distributions let each carry its own version, so a consumer can pin them independently and add
support for a new panel schema by installing a package rather than by upgrading the transport.

## Installation

```console
pip install "span-panel-api[schema-0]"
```

Installing this package is what makes flat-schema panels work. `span-panel-api` on its own will connect and then raise `SpanPanelAdapterMissingError` naming the adapter it could not find.

A consumer that wants to support panels on either schema installs both adapters:

```console
pip install "span-panel-api[schema-0,schema-1]"
```

Dispatch happens at runtime, per panel, from the `data-model-version` the panel reports. The extras are the recommended spelling because they give `pip install -U` a correct upgrade path — the dependency arrow runs from adapter to bootstrap, so upgrading
the bootstrap alone would otherwise leave a stale adapter wheel that discovery then rejects, with pip reporting success. Naming the distributions directly works too.

## Retirement

SPAN retires the flat schema in the same release that introduces the parent/child model (`r202633`, fleet rollout projected for early September 2026). When the fleet has moved, consumers drop this package from their requirements. Published versions stay on
PyPI for anyone still running older firmware.
