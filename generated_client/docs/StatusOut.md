# StatusOut


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**software** | [**SoftwareStatus**](SoftwareStatus.md) |  |
**system** | [**SystemStatus**](SystemStatus.md) |  |
**network** | [**NetworkStatus**](NetworkStatus.md) |  |

## Example

```python
from span_panel_api.models.status_out import StatusOut

# TODO update the JSON string below
json = "{}"
# create an instance of StatusOut from a JSON string
status_out_instance = StatusOut.from_json(json)
# print the JSON string representation of the object
print(StatusOut.to_json())

# convert the object into a dict
status_out_dict = status_out_instance.to_dict()
# create an instance of StatusOut from a dict
status_out_from_dict = StatusOut.from_dict(status_out_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
