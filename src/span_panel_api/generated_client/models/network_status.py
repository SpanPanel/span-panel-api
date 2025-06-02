from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="NetworkStatus")


@_attrs_define
class NetworkStatus:
    """
    Attributes:
        eth_0_link (bool):
        wlan_link (bool):
        wwan_link (bool):
    """

    eth_0_link: bool
    wlan_link: bool
    wwan_link: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        eth_0_link = self.eth_0_link

        wlan_link = self.wlan_link

        wwan_link = self.wwan_link

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "eth0Link": eth_0_link,
                "wlanLink": wlan_link,
                "wwanLink": wwan_link,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        eth_0_link = d.pop("eth0Link")

        wlan_link = d.pop("wlanLink")

        wwan_link = d.pop("wwanLink")

        network_status = cls(
            eth_0_link=eth_0_link,
            wlan_link=wlan_link,
            wwan_link=wwan_link,
        )

        network_status.additional_properties = d
        return network_status

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
