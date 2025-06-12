from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define, field as _attrs_field

T = TypeVar("T", bound="FeedthroughEnergy")


@_attrs_define
class FeedthroughEnergy:
    """
    Attributes:
        produced_energy_wh (float):
        consumed_energy_wh (float):
    """

    produced_energy_wh: float
    consumed_energy_wh: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        produced_energy_wh = self.produced_energy_wh

        consumed_energy_wh = self.consumed_energy_wh

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "producedEnergyWh": produced_energy_wh,
                "consumedEnergyWh": consumed_energy_wh,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        produced_energy_wh = d.pop("producedEnergyWh")

        consumed_energy_wh = d.pop("consumedEnergyWh")

        feedthrough_energy = cls(
            produced_energy_wh=produced_energy_wh,
            consumed_energy_wh=consumed_energy_wh,
        )

        feedthrough_energy.additional_properties = d
        return feedthrough_energy

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
