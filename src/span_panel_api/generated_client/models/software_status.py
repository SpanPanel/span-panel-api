from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SoftwareStatus")


@_attrs_define
class SoftwareStatus:
    """
    Attributes:
        firmware_version (str):
        update_status (str):
        env (str):
    """

    firmware_version: str
    update_status: str
    env: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        firmware_version = self.firmware_version

        update_status = self.update_status

        env = self.env

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "firmwareVersion": firmware_version,
                "updateStatus": update_status,
                "env": env,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        firmware_version = d.pop("firmwareVersion")

        update_status = d.pop("updateStatus")

        env = d.pop("env")

        software_status = cls(
            firmware_version=firmware_version,
            update_status=update_status,
            env=env,
        )

        software_status.additional_properties = d
        return software_status

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
