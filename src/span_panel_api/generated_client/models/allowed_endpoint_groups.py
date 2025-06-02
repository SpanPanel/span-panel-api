from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="AllowedEndpointGroups")


@_attrs_define
class AllowedEndpointGroups:
    """
    Attributes:
        delete (list[str]):
        get (list[str]):
        post (list[str]):
        push (list[str]):
    """

    delete: list[str]
    get: list[str]
    post: list[str]
    push: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        delete = self.delete

        get = self.get

        post = self.post

        push = self.push

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "delete": delete,
                "get": get,
                "post": post,
                "push": push,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        delete = cast(list[str], d.pop("delete"))

        get = cast(list[str], d.pop("get"))

        post = cast(list[str], d.pop("post"))

        push = cast(list[str], d.pop("push"))

        allowed_endpoint_groups = cls(
            delete=delete,
            get=get,
            post=post,
            push=push,
        )

        allowed_endpoint_groups.additional_properties = d
        return allowed_endpoint_groups

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
