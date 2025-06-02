from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.circuit import Circuit


T = TypeVar("T", bound="CircuitsOutCircuits")


@_attrs_define
class CircuitsOutCircuits:
    """ """

    additional_properties: dict[str, "Circuit"] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.to_dict()

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.circuit import Circuit

        d = dict(src_dict)
        circuits_out_circuits = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = Circuit.from_dict(prop_dict)

            additional_properties[prop_name] = additional_property

        circuits_out_circuits.additional_properties = additional_properties
        return circuits_out_circuits

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> "Circuit":
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: "Circuit") -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
