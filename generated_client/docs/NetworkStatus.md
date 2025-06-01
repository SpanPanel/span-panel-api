# NetworkStatus


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**eth0_link** | **bool** |  |
**wlan_link** | **bool** |  |
**wwan_link** | **bool** |  |

## Example

```python
from span_panel_api.models.network_status import NetworkStatus

# TODO update the JSON string below
json = "{}"
# create an instance of NetworkStatus from a JSON string
network_status_instance = NetworkStatus.from_json(json)
# print the JSON string representation of the object
print(NetworkStatus.to_json())

# convert the object into a dict
network_status_dict = network_status_instance.to_dict()
# create an instance of NetworkStatus from a dict
network_status_from_dict = NetworkStatus.from_dict(network_status_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
