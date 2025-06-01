# MainMeterEnergy


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**produced_energy_wh** | **float** |  |
**consumed_energy_wh** | **float** |  |

## Example

```python
from span_panel_api.models.main_meter_energy import MainMeterEnergy

# TODO update the JSON string below
json = "{}"
# create an instance of MainMeterEnergy from a JSON string
main_meter_energy_instance = MainMeterEnergy.from_json(json)
# print the JSON string representation of the object
print(MainMeterEnergy.to_json())

# convert the object into a dict
main_meter_energy_dict = main_meter_energy_instance.to_dict()
# create an instance of MainMeterEnergy from a dict
main_meter_energy_from_dict = MainMeterEnergy.from_dict(main_meter_energy_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
