# PanelState


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**main_relay_state** | [**RelayState**](RelayState.md) |  |
**main_meter_energy** | [**MainMeterEnergy**](MainMeterEnergy.md) |  |
**instant_grid_power_w** | **float** |  |
**feedthrough_power_w** | **float** |  |
**feedthrough_energy** | [**FeedthroughEnergy**](FeedthroughEnergy.md) |  |
**grid_sample_start_ms** | **int** |  |
**grid_sample_end_ms** | **int** |  |
**dsm_grid_state** | **str** |  |
**dsm_state** | **str** |  |
**current_run_config** | **str** |  |
**branches** | [**List[Branch]**](Branch.md) |  |

## Example

```python
from span_panel_api.models.panel_state import PanelState

# TODO update the JSON string below
json = "{}"
# create an instance of PanelState from a JSON string
panel_state_instance = PanelState.from_json(json)
# print the JSON string representation of the object
print(PanelState.to_json())

# convert the object into a dict
panel_state_dict = panel_state_instance.to_dict()
# create an instance of PanelState from a dict
panel_state_from_dict = PanelState.from_dict(panel_state_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
