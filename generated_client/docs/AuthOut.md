# AuthOut


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**access_token** | **str** |  |
**token_type** | **str** |  |
**iat_ms** | **int** |  |

## Example

```python
from span_panel_api.models.auth_out import AuthOut

# TODO update the JSON string below
json = "{}"
# create an instance of AuthOut from a JSON string
auth_out_instance = AuthOut.from_json(json)
# print the JSON string representation of the object
print(AuthOut.to_json())

# convert the object into a dict
auth_out_dict = auth_out_instance.to_dict()
# create an instance of AuthOut from a dict
auth_out_from_dict = AuthOut.from_dict(auth_out_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
