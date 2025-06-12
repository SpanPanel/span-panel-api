from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define, field as _attrs_field

from ..models.relay_state import RelayState

if TYPE_CHECKING:
    from ..models.branch import Branch
    from ..models.feedthrough_energy import FeedthroughEnergy
    from ..models.main_meter_energy import MainMeterEnergy


T = TypeVar("T", bound="PanelState")


@_attrs_define
class PanelState:
    """
    Attributes:
        main_relay_state (RelayState): An enumeration.
        main_meter_energy (MainMeterEnergy):
        instant_grid_power_w (float):
        feedthrough_power_w (float):
        feedthrough_energy (FeedthroughEnergy):
        grid_sample_start_ms (int):
        grid_sample_end_ms (int):
        dsm_grid_state (str):
        dsm_state (str):
        current_run_config (str):
        branches (list['Branch']):
    """

    main_relay_state: RelayState
    main_meter_energy: "MainMeterEnergy"
    instant_grid_power_w: float
    feedthrough_power_w: float
    feedthrough_energy: "FeedthroughEnergy"
    grid_sample_start_ms: int
    grid_sample_end_ms: int
    dsm_grid_state: str
    dsm_state: str
    current_run_config: str
    branches: list["Branch"]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        main_relay_state = self.main_relay_state.value

        main_meter_energy = self.main_meter_energy.to_dict()

        instant_grid_power_w = self.instant_grid_power_w

        feedthrough_power_w = self.feedthrough_power_w

        feedthrough_energy = self.feedthrough_energy.to_dict()

        grid_sample_start_ms = self.grid_sample_start_ms

        grid_sample_end_ms = self.grid_sample_end_ms

        dsm_grid_state = self.dsm_grid_state

        dsm_state = self.dsm_state

        current_run_config = self.current_run_config

        branches = []
        for branches_item_data in self.branches:
            branches_item = branches_item_data.to_dict()
            branches.append(branches_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "mainRelayState": main_relay_state,
                "mainMeterEnergy": main_meter_energy,
                "instantGridPowerW": instant_grid_power_w,
                "feedthroughPowerW": feedthrough_power_w,
                "feedthroughEnergy": feedthrough_energy,
                "gridSampleStartMs": grid_sample_start_ms,
                "gridSampleEndMs": grid_sample_end_ms,
                "dsmGridState": dsm_grid_state,
                "dsmState": dsm_state,
                "currentRunConfig": current_run_config,
                "branches": branches,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.branch import Branch
        from ..models.feedthrough_energy import FeedthroughEnergy
        from ..models.main_meter_energy import MainMeterEnergy

        d = dict(src_dict)
        main_relay_state = RelayState(d.pop("mainRelayState"))

        main_meter_energy = MainMeterEnergy.from_dict(d.pop("mainMeterEnergy"))

        instant_grid_power_w = d.pop("instantGridPowerW")

        feedthrough_power_w = d.pop("feedthroughPowerW")

        feedthrough_energy = FeedthroughEnergy.from_dict(d.pop("feedthroughEnergy"))

        grid_sample_start_ms = d.pop("gridSampleStartMs")

        grid_sample_end_ms = d.pop("gridSampleEndMs")

        dsm_grid_state = d.pop("dsmGridState")

        dsm_state = d.pop("dsmState")

        current_run_config = d.pop("currentRunConfig")

        branches = []
        _branches = d.pop("branches")
        for branches_item_data in _branches:
            branches_item = Branch.from_dict(branches_item_data)

            branches.append(branches_item)

        panel_state = cls(
            main_relay_state=main_relay_state,
            main_meter_energy=main_meter_energy,
            instant_grid_power_w=instant_grid_power_w,
            feedthrough_power_w=feedthrough_power_w,
            feedthrough_energy=feedthrough_energy,
            grid_sample_start_ms=grid_sample_start_ms,
            grid_sample_end_ms=grid_sample_end_ms,
            dsm_grid_state=dsm_grid_state,
            dsm_state=dsm_state,
            current_run_config=current_run_config,
            branches=branches,
        )

        panel_state.additional_properties = d
        return panel_state

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
