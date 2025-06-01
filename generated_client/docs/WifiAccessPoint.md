# WifiAccessPoint


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**bssid** | **str** |  |
**ssid** | **str** |  |
**signal** | **int** |  |
**frequency** | **str** |  |
**encrypted** | **bool** |  |
**connected** | **bool** |  |
**error** | **str** |  | [optional] [default to '']

## Example

```python
from span_panel_api.models.wifi_access_point import WifiAccessPoint

# TODO update the JSON string below
json = "{}"
# create an instance of WifiAccessPoint from a JSON string
wifi_access_point_instance = WifiAccessPoint.from_json(json)
# print the JSON string representation of the object
print(WifiAccessPoint.to_json())

# convert the object into a dict
wifi_access_point_dict = wifi_access_point_instance.to_dict()
# create an instance of WifiAccessPoint from a dict
wifi_access_point_from_dict = WifiAccessPoint.from_dict(wifi_access_point_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
