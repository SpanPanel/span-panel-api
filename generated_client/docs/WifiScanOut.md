# WifiScanOut


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**access_points** | [**List[WifiAccessPoint]**](WifiAccessPoint.md) |  |

## Example

```python
from span_panel_api.models.wifi_scan_out import WifiScanOut

# TODO update the JSON string below
json = "{}"
# create an instance of WifiScanOut from a JSON string
wifi_scan_out_instance = WifiScanOut.from_json(json)
# print the JSON string representation of the object
print(WifiScanOut.to_json())

# convert the object into a dict
wifi_scan_out_dict = wifi_scan_out_instance.to_dict()
# create an instance of WifiScanOut from a dict
wifi_scan_out_from_dict = WifiScanOut.from_dict(wifi_scan_out_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
