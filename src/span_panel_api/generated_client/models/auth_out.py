from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define, field as _attrs_field

T = TypeVar("T", bound="AuthOut")


@_attrs_define
class AuthOut:
    """
    Attributes:
        access_token (str):
        token_type (str):
        iat_ms (int):
    """

    access_token: str
    token_type: str
    iat_ms: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        access_token = self.access_token

        token_type = self.token_type

        iat_ms = self.iat_ms

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "accessToken": access_token,
                "tokenType": token_type,
                "iatMs": iat_ms,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        access_token = d.pop("accessToken")

        token_type = d.pop("tokenType")

        iat_ms = d.pop("iatMs")

        auth_out = cls(
            access_token=access_token,
            token_type=token_type,
            iat_ms=iat_ms,
        )

        auth_out.additional_properties = d
        return auth_out

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
