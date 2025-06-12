from collections.abc import Mapping
from typing import Any, TypeVar, Union

from attrs import define as _attrs_define, field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AuthIn")


@_attrs_define
class AuthIn:
    """
    Attributes:
        name (str):
        description (Union[Unset, str]):
        otp (Union[Unset, str]):
        dashboard_password (Union[Unset, str]):
    """

    name: str
    description: Union[Unset, str] = UNSET
    otp: Union[Unset, str] = UNSET
    dashboard_password: Union[Unset, str] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        description = self.description

        otp = self.otp

        dashboard_password = self.dashboard_password

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if otp is not UNSET:
            field_dict["otp"] = otp
        if dashboard_password is not UNSET:
            field_dict["dashboardPassword"] = dashboard_password

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        description = d.pop("description", UNSET)

        otp = d.pop("otp", UNSET)

        dashboard_password = d.pop("dashboardPassword", UNSET)

        auth_in = cls(
            name=name,
            description=description,
            otp=otp,
            dashboard_password=dashboard_password,
        )

        auth_in.additional_properties = d
        return auth_in

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
