"""Tests for v2 REST Endpoints & Detection."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api._http import _build_url, _get_client
from span_panel_api.detection import detect_api_version
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
    delete_fqdn,
    download_ca_cert,
    get_fqdn,
    get_homie_schema,
    get_v2_status,
    regenerate_passphrase,
    register_fqdn,
    register_v2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHttpHelpers:
    def test_build_url_omits_default_port(self) -> None:
        assert _build_url("panel.local", 80, "/api/v2/status") == "http://panel.local/api/v2/status"
        assert _build_url("panel.local", 8080, "/api/v2/status") == "http://panel.local:8080/api/v2/status"

    @pytest.mark.asyncio
    async def test_get_client_yields_injected_client_without_closing(self) -> None:
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.aclose = AsyncMock()

        async with _get_client(injected, timeout=7.5) as client:
            assert client is injected

        injected.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_client_creates_and_closes_fallback_client(self) -> None:
        with (
            patch("span_panel_api._http.httpx.AsyncClient") as mock_cls,
            patch("span_panel_api._http._create_ssl_context", new_callable=AsyncMock) as mock_ctx,
        ):
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            async with _get_client(None, timeout=12.5) as client:
                assert client is mock_instance

            mock_cls.assert_called_once_with(
                timeout=12.5,
                verify=mock_ctx.return_value,
            )
            mock_instance.__aenter__.assert_awaited_once()
            mock_instance.__aexit__.assert_awaited_once()


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
# httpx_client injection (shared client, timeout contract)
# ===================================================================


class TestHttpxClientInjection:
    @pytest.mark.asyncio
    async def test_register_v2_uses_injected_client_and_does_not_close(self) -> None:
        mock_response = _mock_response(200, V2_AUTH_JSON)
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.post = AsyncMock(return_value=mock_response)
        injected.aclose = AsyncMock()

        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            result = await register_v2("192.168.65.70", "HA", "my-passphrase", httpx_client=injected)

        assert isinstance(result, V2AuthResponse)
        mock_cls.assert_not_called()
        injected.post.assert_awaited_once()
        injected.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_client_uses_register_v2_timeout(self) -> None:
        mock_response = _mock_response(200, V2_AUTH_JSON)
        with (
            patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls,
            patch("span_panel_api._http._create_ssl_context", new_callable=AsyncMock) as mock_ctx,
        ):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await register_v2("192.168.65.70", "HA", "p", timeout=42.5)

        mock_client_cls.assert_called_once_with(
            timeout=42.5,
            verify=mock_ctx.return_value,
        )

    @pytest.mark.asyncio
    async def test_get_v2_status_injected_skips_async_client_constructor(self) -> None:
        mock_response = _mock_response(200, V2_STATUS_JSON)
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.get = AsyncMock(return_value=mock_response)
        injected.aclose = AsyncMock()

        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            await get_v2_status("192.168.65.70", timeout=999.0, httpx_client=injected)

        mock_cls.assert_not_called()
        injected.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_api_version_uses_injected_client(self) -> None:
        mock_response = _mock_response(200, V2_STATUS_JSON)
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.get = AsyncMock(return_value=mock_response)
        injected.aclose = AsyncMock()

        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            result = await detect_api_version("192.168.65.70", httpx_client=injected)

        assert result.api_version == "v2"
        mock_cls.assert_not_called()
        injected.get.assert_awaited_once()
        injected.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_api_version_fallback_uses_timeout(self) -> None:
        mock_response = _mock_response(200, V2_STATUS_JSON)
        with (
            patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls,
            patch("span_panel_api._http._create_ssl_context", new_callable=AsyncMock) as mock_ctx,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await detect_api_version("192.168.65.70", timeout=3.25)

        mock_client_cls.assert_called_once_with(
            timeout=3.25,
            verify=mock_ctx.return_value,
        )


# ===================================================================
# detect_api_version
# ===================================================================


class TestDetectApiVersion:
    @pytest.mark.asyncio
    async def test_detect_v2_panel(self):
        mock_response = _mock_response(200, V2_STATUS_JSON)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        assert result.probe_failed is False

    @pytest.mark.asyncio
    async def test_detect_v1_panel_404(self):
        mock_response = _mock_response(404)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await detect_api_version("192.168.1.1")

        assert result.api_version == "v1"
        assert result.status_info is None
        assert result.probe_failed is False

    @pytest.mark.asyncio
    async def test_detect_v1_connection_error(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await detect_api_version("192.168.1.1")

        assert result.api_version == "v1"
        assert result.status_info is None
        assert result.probe_failed is True

    @pytest.mark.asyncio
    async def test_detect_v1_timeout(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("Timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await detect_api_version("192.168.1.1")

        assert result.api_version == "v1"
        assert result.status_info is None
        assert result.probe_failed is True


# ===================================================================
# register_v2
# ===================================================================


class TestRegisterV2:
    @pytest.mark.asyncio
    async def test_register_success(self):
        mock_response = _mock_response(200, V2_AUTH_JSON)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="422"):
                await register_v2("192.168.65.70", "HA", "wrong")

    @pytest.mark.asyncio
    async def test_register_connection_error(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelConnectionError):
                await register_v2("192.168.65.70", "HA", "pass")

    @pytest.mark.asyncio
    async def test_register_timeout(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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

    def test_panel_size_from_space_format(self):
        """panel_size extracts max from circuit space format 'min:max:step'."""
        types = {
            "energy.ebus.device.circuit": {
                "space": {"datatype": "integer", "format": "1:32:1"},
            },
        }
        schema = V2HomieSchema(firmware_version="fw", types_schema_hash="hash", types=types)
        assert schema.panel_size == 32

    def test_panel_size_different_max(self):
        types = {
            "energy.ebus.device.circuit": {
                "space": {"datatype": "integer", "format": "1:40:1"},
            },
        }
        schema = V2HomieSchema(firmware_version="fw", types_schema_hash="hash", types=types)
        assert schema.panel_size == 40

    def test_panel_size_missing_circuit_type_raises(self):
        schema = V2HomieSchema(firmware_version="fw", types_schema_hash="hash", types={})
        with pytest.raises(ValueError, match="space"):
            _ = schema.panel_size

    def test_panel_size_missing_space_property_raises(self):
        types = {"energy.ebus.device.circuit": {"name": {"datatype": "string"}}}
        schema = V2HomieSchema(firmware_version="fw", types_schema_hash="hash", types=types)
        with pytest.raises(ValueError, match="space"):
            _ = schema.panel_size

    def test_panel_size_bad_format_raises(self):
        types = {
            "energy.ebus.device.circuit": {
                "space": {"datatype": "integer", "format": "invalid"},
            },
        }
        schema = V2HomieSchema(firmware_version="fw", types_schema_hash="hash", types=types)
        with pytest.raises(ValueError, match="format"):
            _ = schema.panel_size

    def test_panel_size_from_live_fixture(self):
        """panel_size works with the real panel schema fixture."""
        import json
        from pathlib import Path

        fixture = Path(__file__).parent / "fixtures" / "v2" / "homie_schema.json"
        data = json.loads(fixture.read_text())
        schema = V2HomieSchema(
            firmware_version=data["firmwareVersion"],
            types_schema_hash="sha256:test",
            types=data["types"],
        )
        assert schema.panel_size == 32


# ===================================================================
# regenerate_passphrase
# ===================================================================


class TestRegeneratePassphrase:
    @pytest.mark.asyncio
    async def test_regenerate_success(self):
        mock_response = _mock_response(200, {"ebusBrokerPassword": "new-password-123"})
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.put.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="412"):
                await regenerate_passphrase("192.168.65.70", "")


# ===================================================================
# register_fqdn
# ===================================================================


class TestRegisterFqdn:
    @pytest.mark.asyncio
    async def test_register_fqdn_success(self):
        mock_response = _mock_response(200)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await register_fqdn("192.168.65.70", "jwt-token", "panel.example.com")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"] == {"ebusTlsFqdn": "panel.example.com"}

    @pytest.mark.asyncio
    async def test_register_fqdn_accepts_201(self):
        mock_response = _mock_response(201)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await register_fqdn("192.168.65.70", "jwt-token", "panel.example.com")

    @pytest.mark.asyncio
    async def test_register_fqdn_accepts_204(self):
        mock_response = _mock_response(204)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await register_fqdn("192.168.65.70", "jwt-token", "panel.example.com")

    @pytest.mark.asyncio
    async def test_register_fqdn_auth_error(self):
        mock_response = _mock_response(401)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="401"):
                await register_fqdn("192.168.65.70", "bad-token", "panel.example.com")

    @pytest.mark.asyncio
    async def test_register_fqdn_403(self):
        mock_response = _mock_response(403)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="403"):
                await register_fqdn("192.168.65.70", "bad-token", "panel.example.com")

    @pytest.mark.asyncio
    async def test_register_fqdn_api_error(self):
        mock_response = _mock_response(500)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAPIError, match="500"):
                await register_fqdn("192.168.65.70", "jwt-token", "panel.example.com")

    @pytest.mark.asyncio
    async def test_register_fqdn_connection_error(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelConnectionError):
                await register_fqdn("192.168.65.70", "jwt-token", "panel.example.com")

    @pytest.mark.asyncio
    async def test_register_fqdn_timeout(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("Timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelTimeoutError):
                await register_fqdn("192.168.65.70", "jwt-token", "panel.example.com")


# ===================================================================
# get_fqdn
# ===================================================================


class TestGetFqdn:
    @pytest.mark.asyncio
    async def test_get_fqdn_success(self):
        mock_response = _mock_response(200, {"ebusTlsFqdn": "panel.example.com"})
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await get_fqdn("192.168.65.70", "jwt-token")

        assert result == "panel.example.com"

    @pytest.mark.asyncio
    async def test_get_fqdn_not_configured_returns_none(self):
        mock_response = _mock_response(404)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await get_fqdn("192.168.65.70", "jwt-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_fqdn_missing_field_returns_none(self):
        mock_response = _mock_response(200, {})
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await get_fqdn("192.168.65.70", "jwt-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_fqdn_empty_string_preserved(self):
        mock_response = _mock_response(200, {"ebusTlsFqdn": ""})
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await get_fqdn("192.168.65.70", "jwt-token")

        assert result == ""

    @pytest.mark.asyncio
    async def test_get_fqdn_auth_error(self):
        mock_response = _mock_response(401)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="401"):
                await get_fqdn("192.168.65.70", "bad-token")

    @pytest.mark.asyncio
    async def test_get_fqdn_api_error(self):
        mock_response = _mock_response(500)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAPIError, match="500"):
                await get_fqdn("192.168.65.70", "jwt-token")

    @pytest.mark.asyncio
    async def test_get_fqdn_connection_error(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelConnectionError):
                await get_fqdn("192.168.65.70", "jwt-token")

    @pytest.mark.asyncio
    async def test_get_fqdn_timeout(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("Timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelTimeoutError):
                await get_fqdn("192.168.65.70", "jwt-token")


# ===================================================================
# delete_fqdn
# ===================================================================


class TestDeleteFqdn:
    @pytest.mark.asyncio
    async def test_delete_fqdn_success_200(self):
        mock_response = _mock_response(200)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.delete.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await delete_fqdn("192.168.65.70", "jwt-token")

    @pytest.mark.asyncio
    async def test_delete_fqdn_success_204(self):
        mock_response = _mock_response(204)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.delete.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await delete_fqdn("192.168.65.70", "jwt-token")

    @pytest.mark.asyncio
    async def test_delete_fqdn_auth_error(self):
        mock_response = _mock_response(403)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.delete.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAuthError, match="403"):
                await delete_fqdn("192.168.65.70", "bad-token")

    @pytest.mark.asyncio
    async def test_delete_fqdn_api_error(self):
        mock_response = _mock_response(500)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.delete.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAPIError, match="500"):
                await delete_fqdn("192.168.65.70", "jwt-token")

    @pytest.mark.asyncio
    async def test_delete_fqdn_connection_error(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.delete.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelConnectionError):
                await delete_fqdn("192.168.65.70", "jwt-token")

    @pytest.mark.asyncio
    async def test_delete_fqdn_timeout(self):
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.delete.side_effect = httpx.TimeoutException("Timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelTimeoutError):
                await delete_fqdn("192.168.65.70", "jwt-token")


# ===================================================================
# get_v2_status
# ===================================================================


class TestGetV2Status:
    @pytest.mark.asyncio
    async def test_get_status_success(self):
        mock_response = _mock_response(200, V2_STATUS_JSON)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
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
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SpanPanelAPIError, match="does not support v2"):
                await get_v2_status("192.168.1.1")
