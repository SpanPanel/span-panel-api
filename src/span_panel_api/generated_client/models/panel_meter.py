from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.feedthrough_energy import FeedthroughEnergy
    from ..models.main_meter_energy import MainMeterEnergy


T = TypeVar("T", bound="PanelMeter")


@_attrs_define
class PanelMeter:
    """
    Attributes:
        main_meter (MainMeterEnergy):
        feedthrough (FeedthroughEnergy):
    """

    main_meter: "MainMeterEnergy"
    feedthrough: "FeedthroughEnergy"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        main_meter = self.main_meter.to_dict()

        feedthrough = self.feedthrough.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "mainMeter": main_meter,
                "feedthrough": feedthrough,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.feedthrough_energy import FeedthroughEnergy
        from ..models.main_meter_energy import MainMeterEnergy

        d = dict(src_dict)
        main_meter = MainMeterEnergy.from_dict(d.pop("mainMeter"))

        feedthrough = FeedthroughEnergy.from_dict(d.pop("feedthrough"))

        panel_meter = cls(
            main_meter=main_meter,
            feedthrough=feedthrough,
        )

        panel_meter.additional_properties = d
        return panel_meter

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
