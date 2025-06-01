# SoftwareStatus


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**firmware_version** | **str** |  |
**update_status** | **str** |  |
**env** | **str** |  |

## Example

```python
from span_panel_api.models.software_status import SoftwareStatus

# TODO update the JSON string below
json = "{}"
# create an instance of SoftwareStatus from a JSON string
software_status_instance = SoftwareStatus.from_json(json)
# print the JSON string representation of the object
print(SoftwareStatus.to_json())

# convert the object into a dict
software_status_dict = software_status_instance.to_dict()
# create an instance of SoftwareStatus from a dict
software_status_from_dict = SoftwareStatus.from_dict(software_status_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
