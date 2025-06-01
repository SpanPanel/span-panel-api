# WifiConnectOut


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**bssid** | **str** |  |
**ssid** | **str** |  |
**signal** | **int** |  |
**encrypted** | **bool** |  |
**connected** | **bool** |  |
**error** | **str** |  |

## Example

```python
from span_panel_api.models.wifi_connect_out import WifiConnectOut

# TODO update the JSON string below
json = "{}"
# create an instance of WifiConnectOut from a JSON string
wifi_connect_out_instance = WifiConnectOut.from_json(json)
# print the JSON string representation of the object
print(WifiConnectOut.to_json())

# convert the object into a dict
wifi_connect_out_dict = wifi_connect_out_instance.to_dict()
# create an instance of WifiConnectOut from a dict
wifi_connect_out_from_dict = WifiConnectOut.from_dict(wifi_connect_out_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
