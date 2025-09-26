from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define, field as _attrs_field

from ..models.priority import Priority
from ..models.relay_state import RelayState
from ..types import UNSET, Unset

T = TypeVar("T", bound="Circuit")


@_attrs_define
class Circuit:
    """
    Attributes:
        id (str):
        relay_state (RelayState): An enumeration.
        instant_power_w (float):
        instant_power_update_time_s (int):
        produced_energy_wh (float):
        consumed_energy_wh (float):
        energy_accum_update_time_s (int):
        priority (Priority): An enumeration.
        is_user_controllable (bool):
        is_sheddable (bool):
        is_never_backup (bool):
        name (Union[Unset, str]):
        tabs (Union[Unset, list[int]]):
    """

    id: str
    relay_state: RelayState
    instant_power_w: float
    instant_power_update_time_s: int
    produced_energy_wh: float | None
    consumed_energy_wh: float | None
    energy_accum_update_time_s: int
    priority: Priority
    is_user_controllable: bool
    is_sheddable: bool
    is_never_backup: bool
    name: Unset | str = UNSET
    tabs: Unset | list[int] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        relay_state = self.relay_state.value

        instant_power_w = self.instant_power_w

        instant_power_update_time_s = self.instant_power_update_time_s

        produced_energy_wh = self.produced_energy_wh

        consumed_energy_wh = self.consumed_energy_wh

        energy_accum_update_time_s = self.energy_accum_update_time_s

        priority = self.priority.value

        is_user_controllable = self.is_user_controllable

        is_sheddable = self.is_sheddable

        is_never_backup = self.is_never_backup

        name = self.name

        tabs: Unset | list[int] = UNSET
        if not isinstance(self.tabs, Unset):
            tabs = self.tabs

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "relayState": relay_state,
                "instantPowerW": instant_power_w,
                "instantPowerUpdateTimeS": instant_power_update_time_s,
                "producedEnergyWh": produced_energy_wh,
                "consumedEnergyWh": consumed_energy_wh,
                "energyAccumUpdateTimeS": energy_accum_update_time_s,
                "priority": priority,
                "isUserControllable": is_user_controllable,
                "isSheddable": is_sheddable,
                "isNeverBackup": is_never_backup,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if tabs is not UNSET:
            field_dict["tabs"] = tabs

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        relay_state = RelayState(d.pop("relayState"))

        instant_power_w = d.pop("instantPowerW")

        instant_power_update_time_s = d.pop("instantPowerUpdateTimeS")

        produced_energy_wh = d.pop("producedEnergyWh")

        consumed_energy_wh = d.pop("consumedEnergyWh")

        energy_accum_update_time_s = d.pop("energyAccumUpdateTimeS")

        priority = Priority(d.pop("priority"))

        is_user_controllable = d.pop("isUserControllable")

        is_sheddable = d.pop("isSheddable")

        is_never_backup = d.pop("isNeverBackup")

        name = d.pop("name", UNSET)

        tabs = cast(list[int], d.pop("tabs", UNSET))

        # Apply SPAN panel sign correction for power values
        # SPAN panels report negative power for consumption on some installations
        # Correct the sign based on energy consumption patterns
        corrected_instant_power_w = cls._apply_power_sign_correction(
            instant_power_w, produced_energy_wh, consumed_energy_wh, name
        )

        circuit = cls(
            id=id,
            relay_state=relay_state,
            instant_power_w=corrected_instant_power_w,
            instant_power_update_time_s=instant_power_update_time_s,
            produced_energy_wh=produced_energy_wh,
            consumed_energy_wh=consumed_energy_wh,
            energy_accum_update_time_s=energy_accum_update_time_s,
            priority=priority,
            is_user_controllable=is_user_controllable,
            is_sheddable=is_sheddable,
            is_never_backup=is_never_backup,
            name=name,
            tabs=tabs,
        )

        circuit.additional_properties = d
        return circuit

    @classmethod
    def _apply_power_sign_correction(
        cls,
        instant_power_w: float,
        produced_energy_wh: float,
        consumed_energy_wh: float,
        name: str | Any,
    ) -> float:
        """Apply sign correction for SPAN panel power reporting quirks.
        
        This method corrects the sign based on energy consumption patterns:
        - Consumer circuits: ensure positive power (consumption)
        - Producer circuits: ensure positive power (production) - HA energy convention
        
        Args:
            instant_power_w: Raw instantaneous power from panel
            produced_energy_wh: Total produced energy 
            consumed_energy_wh: Total consumed energy
            name: Circuit name for logging/debugging
            
        Returns:
            Corrected power value with proper sign convention (positive for both consumption and production)
        """
        # If power is zero, no correction needed
        if instant_power_w == 0.0:
            return instant_power_w
            
        # Determine if this is likely a producer circuit based on energy patterns
        # Producer circuits have significantly more produced than consumed energy
        total_energy = produced_energy_wh + consumed_energy_wh
        if total_energy > 0:
            production_ratio = produced_energy_wh / total_energy
            is_likely_producer = production_ratio > 0.7  # More than 70% production
        else:
            # No energy history, use name-based heuristics
            circuit_name = str(name).lower() if name and str(name) != "UNSET" else ""
            producer_keywords = ["solar", "inverter", "generator", "battery", "production"]
            is_likely_producer = any(keyword in circuit_name for keyword in producer_keywords)
        
        if is_likely_producer:
            # Producer circuit: ensure positive power for production (new HA energy convention)
            # Solar and other producers should show positive power values
            return abs(instant_power_w)
        else:
            # Consumer circuit: ensure positive power for consumption  
            # If power is negative, it means consuming but sign is wrong
            return abs(instant_power_w)

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
