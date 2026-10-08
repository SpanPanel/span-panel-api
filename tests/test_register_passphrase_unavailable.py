"""Registration against a panel that cannot read its own passphrase, or is still starting.

From firmware r202639 a successful registration may carry null broker
credentials beside a valid access token, a 422 can mean the panel's own
passphrase is unreadable rather than that the caller's was wrong, and a 503
means the panel does not know its serial number yet.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api import SpanPanelPassphraseUnavailableError
from span_panel_api.auth import REGISTRATION_UNAVAILABLE_DETAIL, register_v2
from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelAuthError, SpanPanelServerError
from span_panel_api.factory import create_span_client
from span_panel_api.models import V2AuthResponse, V2StatusInfo

HOST = "panel.invalid"
SECRET = "correct-horse-battery-staple"

AUTH_JSON: dict[str, object] = {
    "accessToken": "jwt",
    "tokenType": "Bearer",
    "iatMs": 1700000000000,
    "ebusBrokerUsername": "broker-user",
    "ebusBrokerPassword": "broker-pass",
    "ebusBrokerHost": HOST,
    "ebusBrokerMqttsPort": 8883,
    "ebusBrokerWsPort": 9001,
    "ebusBrokerWssPort": 9002,
    "hostname": "panel",
    "serialNumber": "SYN-0000-0001",
    "hopPassphrase": "hop",
}


def _client(status_code: int, payload: object | None = None, *, text: str = "") -> AsyncMock:
    if payload is not None:
        content, content_type = json.dumps(payload).encode(), "application/json"
    else:
        content, content_type = text.encode(), "text/plain"
    response = httpx.Response(
        status_code,
        content=content,
        headers={"content-type": content_type},
        request=httpx.Request("POST", f"http://{HOST}/api/v2/auth/register"),
    )
    injected = AsyncMock(spec=httpx.AsyncClient)
    injected.post = AsyncMock(return_value=response)
    return injected


async def _register(client: AsyncMock) -> V2AuthResponse:
    return await register_v2(HOST, "home-assistant", SECRET, httpx_client=client)


class TestCredentialsMayBeMissing:
    @pytest.mark.asyncio
    async def test_null_credentials_read_as_none_and_keep_the_token(self) -> None:
        body = {**AUTH_JSON, "ebusBrokerPassword": None, "hopPassphrase": None}
        result = await _register(_client(200, body))
        assert result.ebus_broker_password is None
        assert result.hop_passphrase is None
        assert result.access_token == "jwt"
        assert result.serial_number == "SYN-0000-0001"

    @pytest.mark.asyncio
    async def test_absent_credentials_read_as_none_rather_than_failing(self) -> None:
        body = {k: v for k, v in AUTH_JSON.items() if k not in ("ebusBrokerPassword", "hopPassphrase")}
        result = await _register(_client(200, body))
        assert result.ebus_broker_password is None
        assert result.hop_passphrase is None
        assert result.access_token == "jwt"

    @pytest.mark.asyncio
    async def test_present_credentials_are_unchanged(self) -> None:
        result = await _register(_client(200, AUTH_JSON))
        assert result.ebus_broker_password == "broker-pass"
        assert result.hop_passphrase == "hop"


class TestNotReady:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_a_server_error_is_retryable_and_carries_its_status(self, status: int) -> None:
        with pytest.raises(SpanPanelServerError) as caught:
            await _register(_client(status, {"detail": "Serial number is not available yet"}))
        assert caught.value.status_code == status
        assert "Serial number" not in str(caught.value)


class TestUnprocessable:
    @pytest.mark.asyncio
    async def test_an_unreadable_panel_passphrase_is_its_own_error(self) -> None:
        with pytest.raises(SpanPanelPassphraseUnavailableError) as caught:
            await _register(_client(422, {"detail": REGISTRATION_UNAVAILABLE_DETAIL}))
        error = caught.value
        assert not isinstance(error, SpanPanelAuthError)
        assert isinstance(error, SpanPanelAPIError)
        assert error.status_code == 422
        assert REGISTRATION_UNAVAILABLE_DETAIL in str(error)

    @pytest.mark.asyncio
    async def test_any_other_detail_is_still_a_rejected_credential(self) -> None:
        """Only the known constant changes the class; nothing else from the body reaches the message."""
        with pytest.raises(SpanPanelAuthError) as caught:
            await _register(_client(422, {"detail": f"passphrase {SECRET} does not match"}))
        assert not isinstance(caught.value, SpanPanelPassphraseUnavailableError)
        assert str(caught.value) == "Authentication failed (HTTP 422)"

    @pytest.mark.asyncio
    async def test_a_non_json_422_is_still_a_rejected_credential(self) -> None:
        with pytest.raises(SpanPanelAuthError):
            await _register(_client(422, text=REGISTRATION_UNAVAILABLE_DETAIL))


class TestFactoryRefusesAPasswordlessBroker:
    @pytest.mark.asyncio
    async def test_no_broker_password_raises_before_any_connection(self) -> None:
        body = {**AUTH_JSON, "ebusBrokerPassword": None, "hopPassphrase": None}
        with (
            patch("span_panel_api.factory.SpanMqttClient") as mqtt_client,
            patch("span_panel_api.factory.get_homie_schema") as schema_fetch,
        ):
            with pytest.raises(SpanPanelPassphraseUnavailableError):
                await create_span_client(HOST, passphrase=SECRET, httpx_client=_client(200, body))
        mqtt_client.assert_not_called()
        schema_fetch.assert_not_called()


def test_status_tolerates_the_hardware_version_field() -> None:
    """r202639 adds a required ``hardwareVersion`` to ``/api/v2/status``; it is read, not rejected."""
    status = V2StatusInfo.from_status_payload(
        {
            "serialNumber": "SYN-0000-0001",
            "firmwareVersion": "spanos2/r202639/03",
            "proximityProven": False,
            "hardwareVersion": "UNKNOWN",
        }
    )
    assert status == V2StatusInfo(
        serial_number="SYN-0000-0001",
        firmware_version="spanos2/r202639/03",
        proximity_proven=False,
        hardware_version="UNKNOWN",
    )
