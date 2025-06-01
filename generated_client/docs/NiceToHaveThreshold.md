# NiceToHaveThreshold


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**nice_to_have_threshold_low_soe** | [**StateOfEnergy**](StateOfEnergy.md) |  | [optional]
**nice_to_have_threshold_high_soe** | [**StateOfEnergy**](StateOfEnergy.md) |  | [optional]

## Example

```python
from span_panel_api.models.nice_to_have_threshold import NiceToHaveThreshold

# TODO update the JSON string below
json = "{}"
# create an instance of NiceToHaveThreshold from a JSON string
nice_to_have_threshold_instance = NiceToHaveThreshold.from_json(json)
# print the JSON string representation of the object
print(NiceToHaveThreshold.to_json())

# convert the object into a dict
nice_to_have_threshold_dict = nice_to_have_threshold_instance.to_dict()
# create an instance of NiceToHaveThreshold from a dict
nice_to_have_threshold_from_dict = NiceToHaveThreshold.from_dict(nice_to_have_threshold_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
