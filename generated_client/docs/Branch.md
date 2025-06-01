# Branch


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**id** | **int** |  |
**relay_state** | [**RelayState**](RelayState.md) |  |
**instant_power_w** | **float** |  |
**imported_active_energy_wh** | **float** |  |
**exported_active_energy_wh** | **float** |  |
**measure_start_ts_ms** | **int** |  |
**measure_duration_ms** | **int** |  |
**is_measure_valid** | **bool** |  |

## Example

```python
from span_panel_api.models.branch import Branch

# TODO update the JSON string below
json = "{}"
# create an instance of Branch from a JSON string
branch_instance = Branch.from_json(json)
# print the JSON string representation of the object
print(Branch.to_json())

# convert the object into a dict
branch_dict = branch_instance.to_dict()
# create an instance of Branch from a dict
branch_from_dict = Branch.from_dict(branch_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
