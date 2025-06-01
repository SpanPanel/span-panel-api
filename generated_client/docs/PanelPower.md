# PanelPower


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**instant_grid_power_w** | **float** |  |
**feedthrough_power_w** | **float** |  |

## Example

```python
from span_panel_api.models.panel_power import PanelPower

# TODO update the JSON string below
json = "{}"
# create an instance of PanelPower from a JSON string
panel_power_instance = PanelPower.from_json(json)
# print the JSON string representation of the object
print(PanelPower.to_json())

# convert the object into a dict
panel_power_dict = panel_power_instance.to_dict()
# create an instance of PanelPower from a dict
panel_power_from_dict = PanelPower.from_dict(panel_power_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
