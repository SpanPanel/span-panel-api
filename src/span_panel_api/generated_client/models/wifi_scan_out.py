from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define, field as _attrs_field

if TYPE_CHECKING:
    from ..models.wifi_access_point import WifiAccessPoint


T = TypeVar("T", bound="WifiScanOut")


@_attrs_define
class WifiScanOut:
    """
    Attributes:
        access_points (list['WifiAccessPoint']):
    """

    access_points: list["WifiAccessPoint"]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        access_points = []
        for access_points_item_data in self.access_points:
            access_points_item = access_points_item_data.to_dict()
            access_points.append(access_points_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "accessPoints": access_points,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.wifi_access_point import WifiAccessPoint

        d = dict(src_dict)
        access_points = []
        _access_points = d.pop("accessPoints")
        for access_points_item_data in _access_points:
            access_points_item = WifiAccessPoint.from_dict(access_points_item_data)

            access_points.append(access_points_item)

        wifi_scan_out = cls(
            access_points=access_points,
        )

        wifi_scan_out.additional_properties = d
        return wifi_scan_out

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
