"""The schema hash must change when the schema does.

Parent/child panels publish `deviceClasses` and their own `deviceClassesSchemaHash`
in place of flat firmware's `types`. Hashing the absent `types` gave every such
panel one constant hash. A flat panel's hash must not move, or a value stored for
a flat panel would stop matching.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from span_panel_api.auth import get_homie_schema
from span_panel_api.models import V2HomieSchema

from reference_payloads.bootstrap import homie_schema

HOST = "panel.invalid"


async def _schema(body: dict[str, object]) -> V2HomieSchema:
    """Answer the schema request with `body` and return what the library parsed."""
    response = httpx.Response(
        200,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", f"http://{HOST}/api/v2/homie/schema"),
    )
    injected = AsyncMock(spec=httpx.AsyncClient)
    injected.get = AsyncMock(return_value=response)
    return await get_homie_schema(HOST, httpx_client=injected)


def _hash_of(block: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()[:16]


@pytest.mark.asyncio
async def test_a_published_device_classes_hash_is_the_key() -> None:
    schema = await _schema(
        {"dataModelVersion": "1.0", "deviceClasses": {"a": {}}, "deviceClassesSchemaHash": "sha256:0123456789abcdef"}
    )
    assert schema.types_schema_hash == "sha256:0123456789abcdef"


@pytest.mark.asyncio
async def test_device_classes_without_a_published_hash_still_drift() -> None:
    first = await _schema({"dataModelVersion": "1.0", "deviceClasses": {"a": {"p": 1}}})
    second = await _schema({"dataModelVersion": "1.0", "deviceClasses": {"a": {"p": 2}}})
    assert first.types_schema_hash != second.types_schema_hash
    assert first.types_schema_hash == _hash_of({"a": {"p": 1}})


@pytest.mark.asyncio
@pytest.mark.parametrize("published", ["", None, 123, ["sha256:0123456789abcdef"]], ids=["empty", "null", "number", "list"])
async def test_an_unusable_published_hash_falls_back_to_hashing_device_classes(published: object) -> None:
    """Only a non-empty string is believed; otherwise the block itself is hashed."""
    device_classes = {"a": {"p": 1}}
    schema = await _schema(
        {"dataModelVersion": "1.0", "deviceClasses": device_classes, "deviceClassesSchemaHash": published}
    )
    assert schema.types_schema_hash == _hash_of(device_classes)


@pytest.mark.asyncio
async def test_a_flat_schema_hashes_exactly_as_before() -> None:
    """A flat panel's cached hash is byte-identical to 3.6.2's."""
    types = {"circuit": {"name": {"datatype": "string"}}}
    schema = await _schema({"firmwareVersion": "spanos2/r202603/05", "types": types})
    expected = "sha256:" + hashlib.sha256(json.dumps(types, sort_keys=True).encode()).hexdigest()[:16]
    assert schema.types_schema_hash == expected


@pytest.mark.asyncio
async def test_the_captured_flat_schema_keeps_its_3_6_2_hash() -> None:
    """The captured flat response hashes to the value 3.6.2 computed for it.

    A literal rather than a recomputation, so a change to the formula itself
    cannot pass by changing both sides.
    """
    schema = await _schema(dict(homie_schema()))
    assert schema.types_schema_hash == "sha256:26ef2e6ae169f803"
