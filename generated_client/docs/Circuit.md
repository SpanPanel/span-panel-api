# Circuit


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**id** | **str** |  |
**name** | **str** |  | [optional]
**relay_state** | [**RelayState**](RelayState.md) |  |
**instant_power_w** | **float** |  |
**instant_power_update_time_s** | **int** |  |
**produced_energy_wh** | **float** |  |
**consumed_energy_wh** | **float** |  |
**energy_accum_update_time_s** | **int** |  |
**tabs** | **List[int]** |  | [optional]
**priority** | [**Priority**](Priority.md) |  |
**is_user_controllable** | **bool** |  |
**is_sheddable** | **bool** |  |
**is_never_backup** | **bool** |  |

## Example

```python
from span_panel_api.models.circuit import Circuit

# TODO update the JSON string below
json = "{}"
# create an instance of Circuit from a JSON string
circuit_instance = Circuit.from_json(json)
# print the JSON string representation of the object
print(Circuit.to_json())

# convert the object into a dict
circuit_dict = circuit_instance.to_dict()
# create an instance of Circuit from a dict
circuit_from_dict = Circuit.from_dict(circuit_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
