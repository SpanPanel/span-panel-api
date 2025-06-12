from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, Union

from attrs import define as _attrs_define, field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.state_of_energy import StateOfEnergy


T = TypeVar("T", bound="NiceToHaveThreshold")


@_attrs_define
class NiceToHaveThreshold:
    """
    Attributes:
        nice_to_have_threshold_low_soe (Union[Unset, StateOfEnergy]):
        nice_to_have_threshold_high_soe (Union[Unset, StateOfEnergy]):
    """

    nice_to_have_threshold_low_soe: Union[Unset, "StateOfEnergy"] = UNSET
    nice_to_have_threshold_high_soe: Union[Unset, "StateOfEnergy"] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        nice_to_have_threshold_low_soe: Union[Unset, dict[str, Any]] = UNSET
        if not isinstance(self.nice_to_have_threshold_low_soe, Unset):
            nice_to_have_threshold_low_soe = self.nice_to_have_threshold_low_soe.to_dict()

        nice_to_have_threshold_high_soe: Union[Unset, dict[str, Any]] = UNSET
        if not isinstance(self.nice_to_have_threshold_high_soe, Unset):
            nice_to_have_threshold_high_soe = self.nice_to_have_threshold_high_soe.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if nice_to_have_threshold_low_soe is not UNSET:
            field_dict["nice_to_have_threshold_low_soe"] = nice_to_have_threshold_low_soe
        if nice_to_have_threshold_high_soe is not UNSET:
            field_dict["nice_to_have_threshold_high_soe"] = nice_to_have_threshold_high_soe

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.state_of_energy import StateOfEnergy

        d = dict(src_dict)
        _nice_to_have_threshold_low_soe = d.pop("nice_to_have_threshold_low_soe", UNSET)
        nice_to_have_threshold_low_soe: Union[Unset, StateOfEnergy]
        if isinstance(_nice_to_have_threshold_low_soe, Unset):
            nice_to_have_threshold_low_soe = UNSET
        else:
            nice_to_have_threshold_low_soe = StateOfEnergy.from_dict(_nice_to_have_threshold_low_soe)

        _nice_to_have_threshold_high_soe = d.pop("nice_to_have_threshold_high_soe", UNSET)
        nice_to_have_threshold_high_soe: Union[Unset, StateOfEnergy]
        if isinstance(_nice_to_have_threshold_high_soe, Unset):
            nice_to_have_threshold_high_soe = UNSET
        else:
            nice_to_have_threshold_high_soe = StateOfEnergy.from_dict(_nice_to_have_threshold_high_soe)

        nice_to_have_threshold = cls(
            nice_to_have_threshold_low_soe=nice_to_have_threshold_low_soe,
            nice_to_have_threshold_high_soe=nice_to_have_threshold_high_soe,
        )

        nice_to_have_threshold.additional_properties = d
        return nice_to_have_threshold

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
