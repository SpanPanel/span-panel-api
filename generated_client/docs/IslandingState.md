# IslandingState


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**islanding_state** | **str** |  |

## Example

```python
from span_panel_api.models.islanding_state import IslandingState

# TODO update the JSON string below
json = "{}"
# create an instance of IslandingState from a JSON string
islanding_state_instance = IslandingState.from_json(json)
# print the JSON string representation of the object
print(IslandingState.to_json())

# convert the object into a dict
islanding_state_dict = islanding_state_instance.to_dict()
# create an instance of IslandingState from a dict
islanding_state_from_dict = IslandingState.from_dict(islanding_state_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
