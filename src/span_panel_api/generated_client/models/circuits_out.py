from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.circuits_out_circuits import CircuitsOutCircuits


T = TypeVar("T", bound="CircuitsOut")


@_attrs_define
class CircuitsOut:
    """
    Attributes:
        circuits (CircuitsOutCircuits):
    """

    circuits: "CircuitsOutCircuits"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        circuits = self.circuits.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "circuits": circuits,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.circuits_out_circuits import CircuitsOutCircuits

        d = dict(src_dict)
        circuits = CircuitsOutCircuits.from_dict(d.pop("circuits"))

        circuits_out = cls(
            circuits=circuits,
        )

        circuits_out.additional_properties = d
        return circuits_out

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
