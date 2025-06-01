# BatteryStorage


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**soe** | [**StateOfEnergy**](StateOfEnergy.md) |  |

## Example

```python
from span_panel_api.models.battery_storage import BatteryStorage

# TODO update the JSON string below
json = "{}"
# create an instance of BatteryStorage from a JSON string
battery_storage_instance = BatteryStorage.from_json(json)
# print the JSON string representation of the object
print(BatteryStorage.to_json())

# convert the object into a dict
battery_storage_dict = battery_storage_instance.to_dict()
# create an instance of BatteryStorage from a dict
battery_storage_from_dict = BatteryStorage.from_dict(battery_storage_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
