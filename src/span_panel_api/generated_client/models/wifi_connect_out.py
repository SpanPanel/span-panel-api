from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="WifiConnectOut")


@_attrs_define
class WifiConnectOut:
    """
    Attributes:
        bssid (str):
        ssid (str):
        signal (int):
        encrypted (bool):
        connected (bool):
        error (str):
    """

    bssid: str
    ssid: str
    signal: int
    encrypted: bool
    connected: bool
    error: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bssid = self.bssid

        ssid = self.ssid

        signal = self.signal

        encrypted = self.encrypted

        connected = self.connected

        error = self.error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bssid": bssid,
                "ssid": ssid,
                "signal": signal,
                "encrypted": encrypted,
                "connected": connected,
                "error": error,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bssid = d.pop("bssid")

        ssid = d.pop("ssid")

        signal = d.pop("signal")

        encrypted = d.pop("encrypted")

        connected = d.pop("connected")

        error = d.pop("error")

        wifi_connect_out = cls(
            bssid=bssid,
            ssid=ssid,
            signal=signal,
            encrypted=encrypted,
            connected=connected,
            error=error,
        )

        wifi_connect_out.additional_properties = d
        return wifi_connect_out

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
