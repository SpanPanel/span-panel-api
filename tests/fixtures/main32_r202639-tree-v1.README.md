# main32_r202639-tree-v1.json

The device tree a MAIN 32 panel publishes on firmware `spanos3/r202639/03`, as a `tree-v1` snapshot, with its device ids, serial numbers and postal code masked. It declares `status/wifi-ssid` and publishes no value for it.

It is byte-identical to `tests/fixtures/main32_r202639-tree-v1.json` in [electrification-bus/distribution-enclosure-simulator](https://github.com/electrification-bus/distribution-enclosure-simulator) at tag `v0.9.0` (commit
`2dbddf7c507776a08e03a64825454972696d23df`), and is distributed under that repository's MIT license. `distribution-enclosure-simulator.LICENSE` beside it is that repository's `LICENSE` at the same tag, copied byte for byte.

It is never regenerated here. `tests/adapter_fidelity/test_main32_fidelity.py` pins the SHA-256 of the capture and of the license, and replays the capture through the schema-1 adapter.
