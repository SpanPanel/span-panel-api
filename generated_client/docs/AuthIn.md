# AuthIn


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**name** | **str** |  |
**description** | **str** |  | [optional]
**otp** | **str** |  | [optional]
**dashboard_password** | **str** |  | [optional]

## Example

```python
from span_panel_api.models.auth_in import AuthIn

# TODO update the JSON string below
json = "{}"
# create an instance of AuthIn from a JSON string
auth_in_instance = AuthIn.from_json(json)
# print the JSON string representation of the object
print(AuthIn.to_json())

# convert the object into a dict
auth_in_dict = auth_in_instance.to_dict()
# create an instance of AuthIn from a dict
auth_in_from_dict = AuthIn.from_dict(auth_in_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
