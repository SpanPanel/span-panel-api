# PanelMeter


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**main_meter** | [**MainMeterEnergy**](MainMeterEnergy.md) |  |
**feedthrough** | [**FeedthroughEnergy**](FeedthroughEnergy.md) |  |

## Example

```python
from span_panel_api.models.panel_meter import PanelMeter

# TODO update the JSON string below
json = "{}"
# create an instance of PanelMeter from a JSON string
panel_meter_instance = PanelMeter.from_json(json)
# print the JSON string representation of the object
print(PanelMeter.to_json())

# convert the object into a dict
panel_meter_dict = panel_meter_instance.to_dict()
# create an instance of PanelMeter from a dict
panel_meter_from_dict = PanelMeter.from_dict(panel_meter_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
