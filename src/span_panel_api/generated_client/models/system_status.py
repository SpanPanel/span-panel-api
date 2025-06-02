from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.door_state import DoorState

T = TypeVar("T", bound="SystemStatus")


@_attrs_define
class SystemStatus:
    """
    Attributes:
        manufacturer (str):
        serial (str):
        model (str):
        door_state (DoorState): An enumeration.
        proximity_proven (bool):
        uptime (int):
    """

    manufacturer: str
    serial: str
    model: str
    door_state: DoorState
    proximity_proven: bool
    uptime: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        manufacturer = self.manufacturer

        serial = self.serial

        model = self.model

        door_state = self.door_state.value

        proximity_proven = self.proximity_proven

        uptime = self.uptime

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "manufacturer": manufacturer,
                "serial": serial,
                "model": model,
                "doorState": door_state,
                "proximityProven": proximity_proven,
                "uptime": uptime,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        manufacturer = d.pop("manufacturer")

        serial = d.pop("serial")

        model = d.pop("model")

        door_state = DoorState(d.pop("doorState"))

        proximity_proven = d.pop("proximityProven")

        uptime = d.pop("uptime")

        system_status = cls(
            manufacturer=manufacturer,
            serial=serial,
            model=model,
            door_state=door_state,
            proximity_proven=proximity_proven,
            uptime=uptime,
        )

        system_status.additional_properties = d
        return system_status

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
