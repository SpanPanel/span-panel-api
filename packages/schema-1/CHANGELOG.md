# Changelog

All notable changes to `span-panel-api-schema-1` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`BridgeControllerTransport`** — an `ebus_sdk.MqttControllerTransport` over `AsyncMqttBridge`, so `Controller` parses the parent/child tree over span-panel-api's own connection to the panel's broker. Owns the per-subscription routing table the SDK's
  client keeps internally, because our bridge has one message callback for the whole connection.

### Not yet

- No `schema_1` entry point is registered. Until this package can build a snapshot, a 1.x panel gets `SpanPanelAdapterMissingError` naming `schema_1` rather than a late failure from a partial parser.
