# Electrification Bus Device Type Registry

**Status:** DRAFT v0.5
**Date:** 2026-08-05
**Authors:** Don Jackson

## Purpose

This document is the canonical registry of `energy.ebus.device.*` device-type identifiers used across all Electrification Bus (eBus for short) data models. Data-model documents reference identifiers from this registry; new identifiers are added to this registry when a data-model document introduces them.

In the eBus Homie model, every device participating in the bus declares its device type via the `$type` attribute drawn from this namespace. A device-type identifier names a category of physical or logical device — for example, a distribution enclosure, a battery energy storage system, an electric vehicle supply equipment unit — and constrains what device structure (child devices, capabilities) the parent of that type is expected to expose.

This registry is descriptive, not exhaustive: it lists what is currently registered. Consumers MUST tolerate unknown `$type` values (e.g., accept and persist them; apply only generic Homie handling).

## Format rules

- Identifiers are of the form `energy.ebus.device.<name>`.
- The `<name>` portion is lowercase kebab-case ASCII: lowercase letters, digits, and hyphens only.
- No leading or trailing hyphens; no consecutive hyphens.
- Identifiers are case-sensitive.

## Registered device types

The **Source** column references the data-model document where the identifier currently appears. For identifiers that appear only as forward references (the full data model has not yet been published), the source is the document that introduced the reference.

| Identifier | Description | Source |
|---|---|---|
| `energy.ebus.device.distribution-enclosure` | Parent device for an electrical distribution enclosure (panel / load center / consumer unit / switchboard). Hosts child devices for circuits, feed points, and (in some installations) integrated DERs. | [`devices/distribution-enclosure.md`](../devices/distribution-enclosure.md) |
| `energy.ebus.device.circuit` | Child device representing one branch circuit within a distribution enclosure. | [`devices/distribution-enclosure.md`](../devices/distribution-enclosure.md) |
| `energy.ebus.device.lugs` | Child device representing a feed point (upstream or downstream lugs) on a distribution enclosure. Carries the meter for that feed. | [`devices/distribution-enclosure.md`](../devices/distribution-enclosure.md) |
| `energy.ebus.device.bess` | Battery Energy Storage System (whole-home grid-forming, plug-in / UPS, or grid-following-only). May be published natively (as its own Homie root) or proxied as a child of a distribution enclosure. | [`devices/bess.md`](../devices/bess.md) |
| `energy.ebus.device.pv` | Photovoltaic inverter. May be published natively or proxied as a child of a distribution enclosure. *Full data model pending — currently described in the dist-enclosure spec via the proxied-PV child structure.* | [`devices/distribution-enclosure.md`](../devices/distribution-enclosure.md) (forward reference) |
| `energy.ebus.device.evse` | Electric Vehicle Supply Equipment. May be published natively or proxied as a child of a distribution enclosure. *Full data model pending — currently described in the dist-enclosure spec via the proxied-EVSE child structure.* | [`devices/distribution-enclosure.md`](../devices/distribution-enclosure.md) (forward reference) |
| `energy.ebus.device.mid` | Microgrid Interconnect Device — the grid-relay + per-side meters + controller subsystem that handles islanding for a grid-forming-capable site. May appear as a child device of a distribution enclosure (enclosure-integrated MID) or a BESS (BESS-integrated MID or its proxied equivalent); may also be published as a first-class standalone device. *Full data model pending — currently described in the dist-enclosure spec's MID and proxied-BESS sections.* | [`devices/distribution-enclosure.md`](../devices/distribution-enclosure.md) (forward reference) |
| `energy.ebus.device.bridge` | Standalone proxy host — an entity whose sole role is to bridge one or more non-eBus-native devices into the eBus tree (e.g., a Linux service polling a Tesla cloud API and publishing the Powerwall as a proxy; a Modbus-to-eBus appliance; a CTA-2045 Universal Communications Module bridging a water heater). The bridge anchors the Homie tree as the root of its proxied children; the proxied devices are named per the `{proxier-id}-{proxied-id}` convention in `devices/proxy.md`. The bridge does not publish HEI-device capabilities of its own. Semantic parallel to Matter's *Bridge* device type. | [`framework.md`](../framework.md#standalone-proxy-hosts-bridges) |
| `energy.ebus.device.pdu` | Parent device for a Power Distribution Unit: distributes power to switchable, metered `outlet` children. No storage and no generation. | [`devices/pdu.md`](../devices/pdu.md) |
| `energy.ebus.device.outlet` | One switchable, metered output port (an AC receptacle, or a USB / DC port). Used as a child of a host (PDU, plug-in BESS / UPS) or standalone (a smart plug / smart receptacle). | [`devices/outlet.md`](../devices/outlet.md) |
| `energy.ebus.device.water-heater` | A storage water heater (heat-pump, electric-resistance, gas, or hybrid) modeled as a controllable, grid-flexible load and dispatchable thermal-storage resource. May be published natively or proxied (e.g., as the child of a CTA-2045 UCM bridge). | [`devices/water-heater.md`](../devices/water-heater.md) |
| `energy.ebus.device.utility-meter` | The revenue-grade metering device installed by an electric utility at a customer's service entrance, between the utility's distribution system and the premises wiring. The site's primary point of measurement for energy billing and its most authoritative observer of the utility supply. May be published natively or by a proxy publisher with access to the underlying values. | [`devices/utility-meter.md`](../devices/utility-meter.md) |
| `energy.ebus.device.battery` | Individual battery pack. Child of a BESS. | [`devices/bess.md`](../devices/bess.md) |
| `energy.ebus.device.inverter` | DC-AC inverter. May handle both battery and solar (e.g., Powerwall 3). Child of a BESS. | [`devices/bess.md`](../devices/bess.md) |
| `energy.ebus.device.meter` | Metering point within a larger system, used for site, load, or solar metering. Child of a BESS. Distinct from `utility-meter`, which is the utility's revenue meter at the service entrance. | [`devices/bess.md`](../devices/bess.md) |

## Adding new device types

New identifiers may be added to this registry as new data-model documents are published. The process:

1. The proposed identifier follows the format rules above.
2. The proposed identifier is genuinely new — not a synonym of an existing entry.
3. A data-model document defines (or explicitly references) the device and is the source for the registry row.
4. The proposed identifier is added to this registry with a description and a source reference.
5. This document's version is bumped.

Forward references (a data model that mentions a device type whose full model isn't published yet) are valid registry entries; they are marked as such in the Source column and re-pointed at the full data model when it lands.

Producers and consumers SHOULD treat unknown `$type` values as opaque — accept and persist them, but apply only the generic Homie / eBus framework defaults. This permits forward-compatibility: a device using a newer type identifier than the consumer knows about should still be handled gracefully.
