# FeedthroughEnergy


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**produced_energy_wh** | **float** |  |
**consumed_energy_wh** | **float** |  |

## Example

```python
from span_panel_api.models.feedthrough_energy import FeedthroughEnergy

# TODO update the JSON string below
json = "{}"
# create an instance of FeedthroughEnergy from a JSON string
feedthrough_energy_instance = FeedthroughEnergy.from_json(json)
# print the JSON string representation of the object
print(FeedthroughEnergy.to_json())

# convert the object into a dict
feedthrough_energy_dict = feedthrough_energy_instance.to_dict()
# create an instance of FeedthroughEnergy from a dict
feedthrough_energy_from_dict = FeedthroughEnergy.from_dict(feedthrough_energy_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
