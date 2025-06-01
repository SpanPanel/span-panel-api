# BodySetCircuitStateApiV1CircuitsCircuitIdPost


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**relay_state_in** | [**RelayStateIn**](RelayStateIn.md) |  | [optional]
**priority_in** | [**PriorityIn**](PriorityIn.md) |  | [optional]
**circuit_name_in** | [**CircuitNameIn**](CircuitNameIn.md) |  | [optional]
**user_controllable_in** | [**BooleanIn**](BooleanIn.md) |  | [optional]
**sheddable_in** | [**BooleanIn**](BooleanIn.md) |  | [optional]
**never_backup_in** | [**BooleanIn**](BooleanIn.md) |  | [optional]

## Example

```python
from span_panel_api.models.body_set_circuit_state_api_v1_circuits_circuit_id_post import BodySetCircuitStateApiV1CircuitsCircuitIdPost

# TODO update the JSON string below
json = "{}"
# create an instance of BodySetCircuitStateApiV1CircuitsCircuitIdPost from a JSON string
body_set_circuit_state_api_v1_circuits_circuit_id_post_instance = BodySetCircuitStateApiV1CircuitsCircuitIdPost.from_json(json)
# print the JSON string representation of the object
print(BodySetCircuitStateApiV1CircuitsCircuitIdPost.to_json())

# convert the object into a dict
body_set_circuit_state_api_v1_circuits_circuit_id_post_dict = body_set_circuit_state_api_v1_circuits_circuit_id_post_instance.to_dict()
# create an instance of BodySetCircuitStateApiV1CircuitsCircuitIdPost from a dict
body_set_circuit_state_api_v1_circuits_circuit_id_post_from_dict = BodySetCircuitStateApiV1CircuitsCircuitIdPost.from_dict(body_set_circuit_state_api_v1_circuits_circuit_id_post_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)
