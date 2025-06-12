from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define, field as _attrs_field

if TYPE_CHECKING:
    from ..models.network_status import NetworkStatus
    from ..models.software_status import SoftwareStatus
    from ..models.system_status import SystemStatus


T = TypeVar("T", bound="StatusOut")


@_attrs_define
class StatusOut:
    """
    Attributes:
        software (SoftwareStatus):
        system (SystemStatus):
        network (NetworkStatus):
    """

    software: "SoftwareStatus"
    system: "SystemStatus"
    network: "NetworkStatus"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        software = self.software.to_dict()

        system = self.system.to_dict()

        network = self.network.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "software": software,
                "system": system,
                "network": network,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.network_status import NetworkStatus
        from ..models.software_status import SoftwareStatus
        from ..models.system_status import SystemStatus

        d = dict(src_dict)
        software = SoftwareStatus.from_dict(d.pop("software"))

        system = SystemStatus.from_dict(d.pop("system"))

        network = NetworkStatus.from_dict(d.pop("network"))

        status_out = cls(
            software=software,
            system=system,
            network=network,
        )

        status_out.additional_properties = d
        return status_out

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
