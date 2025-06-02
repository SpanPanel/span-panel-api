from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.relay_state import RelayState

T = TypeVar("T", bound="Branch")


@_attrs_define
class Branch:
    """
    Attributes:
        id (int):
        relay_state (RelayState): An enumeration.
        instant_power_w (float):
        imported_active_energy_wh (float):
        exported_active_energy_wh (float):
        measure_start_ts_ms (int):
        measure_duration_ms (int):
        is_measure_valid (bool):
    """

    id: int
    relay_state: RelayState
    instant_power_w: float
    imported_active_energy_wh: float
    exported_active_energy_wh: float
    measure_start_ts_ms: int
    measure_duration_ms: int
    is_measure_valid: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        relay_state = self.relay_state.value

        instant_power_w = self.instant_power_w

        imported_active_energy_wh = self.imported_active_energy_wh

        exported_active_energy_wh = self.exported_active_energy_wh

        measure_start_ts_ms = self.measure_start_ts_ms

        measure_duration_ms = self.measure_duration_ms

        is_measure_valid = self.is_measure_valid

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "relayState": relay_state,
                "instantPowerW": instant_power_w,
                "importedActiveEnergyWh": imported_active_energy_wh,
                "exportedActiveEnergyWh": exported_active_energy_wh,
                "measureStartTsMs": measure_start_ts_ms,
                "measureDurationMs": measure_duration_ms,
                "isMeasureValid": is_measure_valid,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        relay_state = RelayState(d.pop("relayState"))

        instant_power_w = d.pop("instantPowerW")

        imported_active_energy_wh = d.pop("importedActiveEnergyWh")

        exported_active_energy_wh = d.pop("exportedActiveEnergyWh")

        measure_start_ts_ms = d.pop("measureStartTsMs")

        measure_duration_ms = d.pop("measureDurationMs")

        is_measure_valid = d.pop("isMeasureValid")

        branch = cls(
            id=id,
            relay_state=relay_state,
            instant_power_w=instant_power_w,
            imported_active_energy_wh=imported_active_energy_wh,
            exported_active_energy_wh=exported_active_energy_wh,
            measure_start_ts_ms=measure_start_ts_ms,
            measure_duration_ms=measure_duration_ms,
            is_measure_valid=is_measure_valid,
        )

        branch.additional_properties = d
        return branch

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
