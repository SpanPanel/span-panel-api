"""`/api/v2/status` is read once, by one reader.

It was read twice — by the detector, deciding whether the panel speaks v2 at
all, and by `get_v2_status`, reading the same answer for a caller that already
knows it does. The two had already drifted: only the detector read
`proximityProven`, so the same panel answered "proximity unknown" or "proximity
proven" depending on which call had asked.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from span_panel_api.auth import get_v2_status
from span_panel_api.detection import detect_api_version
from span_panel_api.models import V2StatusInfo

HOST = "panel.invalid"

STATUS_JSON = {
    "serialNumber": "SYN-0000-0001",
    "firmwareVersion": "spanos2/r202609/01",
    "proximityProven": True,
}


def _client(payload: object) -> AsyncMock:
    response = httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", f"http://{HOST}/api/v2/status"),
    )
    injected = AsyncMock(spec=httpx.AsyncClient)
    injected.get = AsyncMock(return_value=response)
    return injected


class TestBothCallersAgree:
    @pytest.mark.asyncio
    async def test_the_same_body_produces_the_same_status(self) -> None:
        """The regression: `get_v2_status` dropped `proximityProven` on the floor."""
        probed = await detect_api_version(HOST, httpx_client=_client(STATUS_JSON))
        fetched = await get_v2_status(HOST, httpx_client=_client(STATUS_JSON))
        assert probed.status_info == fetched
        assert fetched.proximity_proven is True


class TestStatusPayload:
    def test_reads_every_field(self) -> None:
        status = V2StatusInfo.from_status_payload(STATUS_JSON)
        assert status.serial_number == "SYN-0000-0001"
        assert status.firmware_version == "spanos2/r202609/01"
        assert status.proximity_proven is True

    def test_an_omitted_field_reads_as_empty_rather_than_failing(self) -> None:
        """This endpoint answers for panels that may not fully support it."""
        assert V2StatusInfo.from_status_payload({}) == V2StatusInfo(serial_number="", firmware_version="")

    def test_absent_proximity_stays_none_rather_than_false(self) -> None:
        """Firmware below 202609 does not report it, and that is not "not proven"."""
        assert V2StatusInfo.from_status_payload({"serialNumber": "SYN-0000-0001"}).proximity_proven is None

    @pytest.mark.parametrize("raw", ["true", 1, None, {}], ids=["string", "int", "null", "object"])
    def test_a_non_boolean_proximity_is_not_believed(self, raw: object) -> None:
        """Coercing `"false"` to True is the failure mode this guards against."""
        assert V2StatusInfo.from_status_payload({"proximityProven": raw}).proximity_proven is None

    def test_a_false_proximity_is_kept(self) -> None:
        assert V2StatusInfo.from_status_payload({"proximityProven": False}).proximity_proven is False
