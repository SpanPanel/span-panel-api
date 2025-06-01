# WifiConnectIn


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**ssid** | **str** |  |
**psk** | **str** |  |

## Example

```python
from span_panel_api.models.wifi_connect_in import WifiConnectIn

# TODO update the JSON string below
json = "{}"
# create an instance of WifiConnectIn from a JSON string
wifi_connect_in_instance = WifiConnectIn.from_json(json)
# print the JSON string representation of the object
print(WifiConnectIn.to_json())

# convert the object into a dict
wifi_connect_in_dict = wifi_connect_in_instance.to_dict()
# create an instance of WifiConnectIn from a dict
wifi_connect_in_from_dict = WifiConnectIn.from_dict(wifi_connect_in_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
