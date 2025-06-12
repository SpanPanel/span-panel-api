from collections.abc import Mapping
from typing import Any, TypeVar, Union

from attrs import define as _attrs_define, field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="WifiAccessPoint")


@_attrs_define
class WifiAccessPoint:
    """
    Attributes:
        bssid (str):
        ssid (str):
        signal (int):
        frequency (str):
        encrypted (bool):
        connected (bool):
        error (Union[Unset, str]):  Default: ''.
    """

    bssid: str
    ssid: str
    signal: int
    frequency: str
    encrypted: bool
    connected: bool
    error: Union[Unset, str] = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bssid = self.bssid

        ssid = self.ssid

        signal = self.signal

        frequency = self.frequency

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
                "frequency": frequency,
                "encrypted": encrypted,
                "connected": connected,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bssid = d.pop("bssid")

        ssid = d.pop("ssid")

        signal = d.pop("signal")

        frequency = d.pop("frequency")

        encrypted = d.pop("encrypted")

        connected = d.pop("connected")

        error = d.pop("error", UNSET)

        wifi_access_point = cls(
            bssid=bssid,
            ssid=ssid,
            signal=signal,
            frequency=frequency,
            encrypted=encrypted,
            connected=connected,
            error=error,
        )

        wifi_access_point.additional_properties = d
        return wifi_access_point

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
