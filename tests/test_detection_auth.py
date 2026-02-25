"""Tests for v2 REST Endpoints & Detection."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api.detection import DetectionResult, detect_api_version
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
)
from span_panel_api.models import (
    V2AuthResponse,
    V2HomieSchema,
    V2StatusInfo,
)
from span_panel_api.auth import (
    download_ca_cert,
    get_homie_schema,
    get_v2_status,
    regenerate_passphrase,
    register_v2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = "") -> httpx.Response:
    """Build a mock httpx.Response."""
    import json

    if json_data is not None:
        content = json.dumps(json_data).encode()
        headers = {"content-type": "application/json"}
    else:
        content = text.encode()
        headers = {"content-type": "text/plain"}

    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=headers,
        request=httpx.Request("GET", "http://test"),
    )


V2_STATUS_JSON = {"serialNumber": "nj-2316-XXXX", "firmwareVersion": "spanos2/r202603/05"}

V2_AUTH_JSON = {
    "accessToken": "jwt-token-here",
    "tokenType": "Bearer",
    "iatMs": 1700000000000,
    "ebusBrokerUsername": "user123",
    "ebusBrokerPassword": "pass456",
    "ebusBrokerHost": "192.168.65.70",
    "ebusBrokerMqttsPort": 8883,
    "ebusBrokerWsPort": 9001,
    "ebusBrokerWssPort": 9002,
    "hostname": "spanpanel",
    "serialNumber": "nj-2316-XXXX",
    "hopPassphrase": "hop-secret",
}

PEM_CERT = """-----BEGIN CERTIFICATE-----
MIIBkTCB+wIJALRiMLAh2FfIMA0GCSqGSIb3DQEBCwUAMBExDzANBgNVBAMMBnRl
c3RjYTAeFw0yNDAyMjQwMDAwMDBaFw0yNTAyMjQwMDAwMDBaMBExDzANBgNVBAMM
BnRlc3RjYTBcMA0GCSqGSIb3DQEBAQUAA0sAMEgCQQC7o96VJ4GL0xNPHOVQ+Knx
-----END CERTIFICATE-----"""


# ===================================================================
# detect_api_version
# ===================================================================


class TestDetectApiVersion:
    @pytest.mark.asyncio
    async def test_detect_v2_panel(self):
        mock_response = _mock_response(200, V2_STATUS_JSON)
        with patch("span_panel_api.detection.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await detect_api_version("192.168.65.70")

        assert result.api_version == "v2"
        assert result.status_info is not None
        assert result.status_info.serial_number == "nj-2316-XXXX"
        assert result.status_info.firmware_version == "spanos2/r202603/05"

    @pytest.mark.asyncio
    async def test_detect_v1_panel_404(self):
        mock_response = _mock_response(404)
        with patch("span_panel_api.detection.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await detect_api_version("192.168.1.1")

        assert result.api_version == "v1"
        assert result.status_info is None

    @pytest.mark.asyncio
    async def test_detect_v1_connection_error(self):
        with patch("span_panel_api.detection.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await detect_api_version("192.168.1.1")

        assert result.api_version == "v1"
        assert result.status_info is None

    @pytest.mark.asyncio
    async def test_detect_v1_timeout(self):
        with patch("span_panel_api.detection.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("Timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await detect_api_version("192.168.1.1")

        assert result.api_version == "v1"
        assert result.status_info is None

    def test_detection_result_frozen(self):
        result = DetectionResult(api_version="v2", status_info=V2StatusInfo("serial", "fw"))
        with pytest.raises(AttributeError):
            result.api_version = "v1"  # type: ignore[misc]


# ===================================================================
# register_v2
# ===================================================================


class TestRegisterV2:
    @pytest.mark.asyncio
    async def test_register_success(self):
        mock_response = _mock_response(200, V2_AUTH_JSON)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await register_v2("192.168.65.70", "Home Assistant", "my-passphrase")

        assert isinstance(result, V2AuthResponse)
        assert result.access_token == "jwt-token-here"
        assert result.token_type == "Bearer"
        assert result.iat_ms == 1700000000000
        assert result.ebus_broker_username == "user123"
        assert result.ebus_broker_password == "pass456"
        assert result.ebus_broker_host == "192.168.65.70"
        assert result.ebus_broker_mqtts_port == 8883
        assert result.ebus_broker_ws_port == 9001
        assert result.ebus_broker_wss_port == 9002
        assert result.hostname == "spanpanel"
        assert result.serial_number == "nj-2316-XXXX"
        assert result.hop_passphrase == "hop-secret"

    @pytest.mark.asyncio
    async def test_register_invalid_passphrase(self):
        mock_response = _mock_response(422, text="Invalid passphrase")
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="422"):
                await register_v2("192.168.65.70", "HA", "wrong")

    @pytest.mark.asyncio
    async def test_register_connection_error(self):
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelConnectionError):
                await register_v2("192.168.65.70", "HA", "pass")

    @pytest.mark.asyncio
    async def test_register_timeout(self):
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("Timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelTimeoutError):
                await register_v2("192.168.65.70", "HA", "pass")


# ===================================================================
# download_ca_cert
# ===================================================================


class TestDownloadCaCert:
    @pytest.mark.asyncio
    async def test_download_success(self):
        mock_response = _mock_response(200, text=PEM_CERT)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await download_ca_cert("192.168.65.70")

        assert result.startswith("-----BEGIN")

    @pytest.mark.asyncio
    async def test_download_invalid_pem(self):
        mock_response = _mock_response(200, text="not-a-pem")
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAPIError, match="not a valid PEM"):
                await download_ca_cert("192.168.65.70")

    @pytest.mark.asyncio
    async def test_download_http_error(self):
        mock_response = _mock_response(500)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAPIError, match="500"):
                await download_ca_cert("192.168.65.70")


# ===================================================================
# get_homie_schema
# ===================================================================


class TestGetHomieSchema:
    @pytest.mark.asyncio
    async def test_parse_schema(self):
        schema_json = {
            "firmwareVersion": "spanos2/r202603/05",
            "homieDomain": "ebus",
            "homieVersion": 5,
            "types": {
                "energy.ebus.device.distribution-enclosure.core": {
                    "door": {"name": "Door state", "datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"},
                }
            },
        }
        mock_response = _mock_response(200, schema_json)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await get_homie_schema("192.168.65.70")

        assert isinstance(result, V2HomieSchema)
        assert result.firmware_version == "spanos2/r202603/05"
        assert result.types_schema_hash.startswith("sha256:")
        assert len(result.types_schema_hash) == len("sha256:") + 16
        assert "energy.ebus.device.distribution-enclosure.core" in result.types
        core_type = result.types["energy.ebus.device.distribution-enclosure.core"]
        assert "door" in core_type

    @pytest.mark.asyncio
    async def test_schema_frozen(self):
        result = V2HomieSchema(firmware_version="fw", types_schema_hash="hash", types={})
        with pytest.raises(AttributeError):
            result.firmware_version = "changed"  # type: ignore[misc]


# ===================================================================
# regenerate_passphrase
# ===================================================================


class TestRegeneratePassphrase:
    @pytest.mark.asyncio
    async def test_regenerate_success(self):
        mock_response = _mock_response(200, {"ebusBrokerPassword": "new-password-123"})
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.put.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await regenerate_passphrase("192.168.65.70", "jwt-token")

        assert result == "new-password-123"

    @pytest.mark.asyncio
    async def test_regenerate_auth_error(self):
        mock_response = _mock_response(401)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.put.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="401"):
                await regenerate_passphrase("192.168.65.70", "bad-token")

    @pytest.mark.asyncio
    async def test_regenerate_412_precondition_failed(self):
        mock_response = _mock_response(412)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.put.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="412"):
                await regenerate_passphrase("192.168.65.70", "")


# ===================================================================
# get_v2_status
# ===================================================================


class TestGetV2Status:
    @pytest.mark.asyncio
    async def test_get_status_success(self):
        mock_response = _mock_response(200, V2_STATUS_JSON)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await get_v2_status("192.168.65.70")

        assert isinstance(result, V2StatusInfo)
        assert result.serial_number == "nj-2316-XXXX"
        assert result.firmware_version == "spanos2/r202603/05"

    @pytest.mark.asyncio
    async def test_get_status_not_v2(self):
        mock_response = _mock_response(404)
        with patch("span_panel_api.auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAPIError, match="does not support v2"):
                await get_v2_status("192.168.1.1")


# ===================================================================
# V2 data model immutability
# ===================================================================


class TestV2ModelImmutability:
    def test_v2_auth_response_frozen(self):
        resp = V2AuthResponse(
            access_token="t",
            token_type="Bearer",
            iat_ms=0,
            ebus_broker_username="u",
            ebus_broker_password="p",
            ebus_broker_host="h",
            ebus_broker_mqtts_port=8883,
            ebus_broker_ws_port=9001,
            ebus_broker_wss_port=9002,
            hostname="h",
            serial_number="s",
            hop_passphrase="hp",
        )
        with pytest.raises(AttributeError):
            resp.access_token = "changed"  # type: ignore[misc]

    def test_v2_status_info_frozen(self):
        info = V2StatusInfo(serial_number="s", firmware_version="fw")
        with pytest.raises(AttributeError):
            info.serial_number = "changed"  # type: ignore[misc]


# ===================================================================
# Exports
# ===================================================================


class TestPhase2Exports:
    def test_detection_exports(self):
        import span_panel_api

        assert hasattr(span_panel_api, "DetectionResult")
        assert hasattr(span_panel_api, "detect_api_version")

    def test_v2_model_exports(self):
        import span_panel_api

        assert hasattr(span_panel_api, "V2AuthResponse")
        assert hasattr(span_panel_api, "V2StatusInfo")

    def test_v2_auth_function_exports(self):
        import span_panel_api

        assert hasattr(span_panel_api, "register_v2")
        assert hasattr(span_panel_api, "download_ca_cert")
        assert hasattr(span_panel_api, "get_homie_schema")
        assert hasattr(span_panel_api, "regenerate_passphrase")

    def test_version_bumped(self):
        import span_panel_api

        assert span_panel_api.__version__ == "2.0.0"
