# CircuitsOut


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**circuits** | [**Dict[str, Circuit]**](Circuit.md) |  |

## Example

```python
from span_panel_api.models.circuits_out import CircuitsOut

# TODO update the JSON string below
json = "{}"
# create an instance of CircuitsOut from a JSON string
circuits_out_instance = CircuitsOut.from_json(json)
# print the JSON string representation of the object
print(CircuitsOut.to_json())

# convert the object into a dict
circuits_out_dict = circuits_out_instance.to_dict()
# create an instance of CircuitsOut from a dict
circuits_out_from_dict = CircuitsOut.from_dict(circuits_out_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
