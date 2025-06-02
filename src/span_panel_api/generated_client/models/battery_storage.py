from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.state_of_energy import StateOfEnergy


T = TypeVar("T", bound="BatteryStorage")


@_attrs_define
class BatteryStorage:
    """
    Attributes:
        soe (StateOfEnergy):
    """

    soe: "StateOfEnergy"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        soe = self.soe.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "soe": soe,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.state_of_energy import StateOfEnergy

        d = dict(src_dict)
        soe = StateOfEnergy.from_dict(d.pop("soe"))

        battery_storage = cls(
            soe=soe,
        )

        battery_storage.additional_properties = d
        return battery_storage

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
