# AllowedEndpointGroups


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**delete** | **List[str]** |  |
**get** | **List[str]** |  |
**post** | **List[str]** |  |
**push** | **List[str]** |  |

## Example

```python
from span_panel_api.models.allowed_endpoint_groups import AllowedEndpointGroups

# TODO update the JSON string below
json = "{}"
# create an instance of AllowedEndpointGroups from a JSON string
allowed_endpoint_groups_instance = AllowedEndpointGroups.from_json(json)
# print the JSON string representation of the object
print(AllowedEndpointGroups.to_json())

# convert the object into a dict
allowed_endpoint_groups_dict = allowed_endpoint_groups_instance.to_dict()
# create an instance of AllowedEndpointGroups from a dict
allowed_endpoint_groups_from_dict = AllowedEndpointGroups.from_dict(allowed_endpoint_groups_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
