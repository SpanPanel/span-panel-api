from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, Union

from attrs import define as _attrs_define, field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.allowed_endpoint_groups import AllowedEndpointGroups


T = TypeVar("T", bound="Client")


@_attrs_define
class Client:
    """
    Attributes:
        allowed_endpoint_groups (AllowedEndpointGroups):
        description (Union[Unset, str]):
        issued_at (Union[Unset, int]):
    """

    allowed_endpoint_groups: "AllowedEndpointGroups"
    description: Union[Unset, str] = UNSET
    issued_at: Union[Unset, int] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowed_endpoint_groups = self.allowed_endpoint_groups.to_dict()

        description = self.description

        issued_at = self.issued_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowed_endpoint_groups": allowed_endpoint_groups,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if issued_at is not UNSET:
            field_dict["issued_at"] = issued_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.allowed_endpoint_groups import AllowedEndpointGroups

        d = dict(src_dict)
        allowed_endpoint_groups = AllowedEndpointGroups.from_dict(d.pop("allowed_endpoint_groups"))

        description = d.pop("description", UNSET)

        issued_at = d.pop("issued_at", UNSET)

        client = cls(
            allowed_endpoint_groups=allowed_endpoint_groups,
            description=description,
            issued_at=issued_at,
        )

        client.additional_properties = d
        return client

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
