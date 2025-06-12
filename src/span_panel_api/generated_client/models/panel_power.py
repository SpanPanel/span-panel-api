from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define, field as _attrs_field

T = TypeVar("T", bound="PanelPower")


@_attrs_define
class PanelPower:
    """
    Attributes:
        instant_grid_power_w (float):
        feedthrough_power_w (float):
    """

    instant_grid_power_w: float
    feedthrough_power_w: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        instant_grid_power_w = self.instant_grid_power_w

        feedthrough_power_w = self.feedthrough_power_w

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "instantGridPowerW": instant_grid_power_w,
                "feedthroughPowerW": feedthrough_power_w,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        instant_grid_power_w = d.pop("instantGridPowerW")

        feedthrough_power_w = d.pop("feedthroughPowerW")

        panel_power = cls(
            instant_grid_power_w=instant_grid_power_w,
            feedthrough_power_w=feedthrough_power_w,
        )

        panel_power.additional_properties = d
        return panel_power

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
