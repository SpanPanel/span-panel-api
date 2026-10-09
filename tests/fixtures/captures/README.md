# Reference captures

Masked captures of real panels on firmware `spanos3/r202639/03`. Each `r202639-<x>-tree-v1.json` (`r202639-a` to `r202639-e`) is byte-identical to `tests/fixtures/r202639-<x>-tree-v1.json` in
[electrification-bus/distribution-enclosure-simulator](https://github.com/electrification-bus/distribution-enclosure-simulator) at commit `79aa07c5382f4f2a253aec2143309b67f0b3a77f`, and is distributed under that repository's MIT license. `LICENSE` here is
that repository's `LICENSE` at the same commit, copied byte for byte.

**Never regenerated in-test, and never edited here.** No emitter release produces these files, so unlike the payloads under `tests/reference_payloads/` there is nothing to regenerate them from. `SHA256SUMS` pins every byte of the captures and of `LICENSE`,
and `tests/test_captures_unchanged.py` fails on a copy that differs from its pin, on a capture without a pin, on a pin without a file, and on any other file in this directory. The same check by hand: `shasum -a 256 -c SHA256SUMS`, run here.

**Format.** The emitter's `tree-v1`: per device, the parsed `$description` and the retained values split into `properties` and `numeric_properties`, every value the wire string as published. Identifiers, names, addresses and serial numbers are masked
upstream and are kept exactly as masked.

**Reading them.** `tests/reference_payloads/captures.py` parses a capture into a `CaptureTree`, replays it through the schema-1 adapter, and offers selectors by what a tree declares. Tests over the captures are properties over `CAPTURES`: they read the
values they expect from the capture they replay, and name no capture, device id or value.

**Following upstream.** Copy each capture from the new commit with `git show <commit>:tests/fixtures/<file>`, and `LICENSE` with `git show <commit>:LICENSE`, check each with `git hash-object` against `git rev-parse` of the same path, update `SHA256SUMS`
and the commit above, and expect the capture tests to show what changed.
