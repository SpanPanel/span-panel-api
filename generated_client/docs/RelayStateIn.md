# RelayStateIn


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**relay_state** | [**RelayState**](RelayState.md) |  |

## Example

```python
from span_panel_api.models.relay_state_in import RelayStateIn

# TODO update the JSON string below
json = "{}"
# create an instance of RelayStateIn from a JSON string
relay_state_in_instance = RelayStateIn.from_json(json)
# print the JSON string representation of the object
print(RelayStateIn.to_json())

# convert the object into a dict
relay_state_in_dict = relay_state_in_instance.to_dict()
# create an instance of RelayStateIn from a dict
relay_state_in_from_dict = RelayStateIn.from_dict(relay_state_in_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
