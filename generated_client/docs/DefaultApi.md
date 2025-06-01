# span_panel_api.DefaultApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**delete_client_api_v1_auth_clients_name_delete**](DefaultApi.md#delete_client_api_v1_auth_clients_name_delete) | **DELETE** /api/v1/auth/clients/{name} | Delete Client
[**deprecated_spaces_endpoint_stub_api_v1_spaces_get**](DefaultApi.md#deprecated_spaces_endpoint_stub_api_v1_spaces_get) | **GET** /api/v1/spaces | Deprecated Spaces Endpoint Stub
[**deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_get**](DefaultApi.md#deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_get) | **GET** /api/v1/spaces/{spaces_id} | Deprecated Spaces Endpoint Stub
[**deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_post**](DefaultApi.md#deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_post) | **POST** /api/v1/spaces/{spaces_id} | Deprecated Spaces Endpoint Stub
[**generate_jwt_api_v1_auth_register_post**](DefaultApi.md#generate_jwt_api_v1_auth_register_post) | **POST** /api/v1/auth/register | Generate Jwt
[**get_all_clients_api_v1_auth_clients_get**](DefaultApi.md#get_all_clients_api_v1_auth_clients_get) | **GET** /api/v1/auth/clients | Get All Clients
[**get_circuit_state_api_v1_circuits_circuit_id_get**](DefaultApi.md#get_circuit_state_api_v1_circuits_circuit_id_get) | **GET** /api/v1/circuits/{circuitId} | Get Circuit State
[**get_circuits_api_v1_circuits_get**](DefaultApi.md#get_circuits_api_v1_circuits_get) | **GET** /api/v1/circuits | Get Circuits
[**get_client_api_v1_auth_clients_name_get**](DefaultApi.md#get_client_api_v1_auth_clients_name_get) | **GET** /api/v1/auth/clients/{name} | Get Client
[**get_islanding_state_api_v1_islanding_state_get**](DefaultApi.md#get_islanding_state_api_v1_islanding_state_get) | **GET** /api/v1/islanding-state | Get Islanding State
[**get_main_relay_state_api_v1_panel_grid_get**](DefaultApi.md#get_main_relay_state_api_v1_panel_grid_get) | **GET** /api/v1/panel/grid | Get Main Relay State
[**get_panel_meter_api_v1_panel_meter_get**](DefaultApi.md#get_panel_meter_api_v1_panel_meter_get) | **GET** /api/v1/panel/meter | Get Panel Meter
[**get_panel_power_api_v1_panel_power_get**](DefaultApi.md#get_panel_power_api_v1_panel_power_get) | **GET** /api/v1/panel/power | Get Panel Power
[**get_panel_state_api_v1_panel_get**](DefaultApi.md#get_panel_state_api_v1_panel_get) | **GET** /api/v1/panel | Get Panel State
[**get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get**](DefaultApi.md#get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get) | **GET** /api/v1/storage/nice-to-have-thresh | Get Storage Nice To Have Threshold
[**get_storage_soe_api_v1_storage_soe_get**](DefaultApi.md#get_storage_soe_api_v1_storage_soe_get) | **GET** /api/v1/storage/soe | Get Storage Soe
[**get_wifi_scan_api_v1_wifi_scan_get**](DefaultApi.md#get_wifi_scan_api_v1_wifi_scan_get) | **GET** /api/v1/wifi/scan | Get Wifi Scan
[**run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post**](DefaultApi.md#run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post) | **POST** /api/v1/panel/emergency-reconnect | Run Panel Emergency Reconnect
[**run_wifi_connect_api_v1_wifi_connect_post**](DefaultApi.md#run_wifi_connect_api_v1_wifi_connect_post) | **POST** /api/v1/wifi/connect | Run Wifi Connect
[**set_circuit_state_api_v1_circuits_circuit_id_post**](DefaultApi.md#set_circuit_state_api_v1_circuits_circuit_id_post) | **POST** /api/v1/circuits/{circuitId} | Set Circuit State
[**set_main_relay_state_api_v1_panel_grid_post**](DefaultApi.md#set_main_relay_state_api_v1_panel_grid_post) | **POST** /api/v1/panel/grid | Set Main Relay State
[**set_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_post**](DefaultApi.md#set_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_post) | **POST** /api/v1/storage/nice-to-have-thresh | Set Storage Nice To Have Threshold
[**set_storage_soe_api_v1_storage_soe_post**](DefaultApi.md#set_storage_soe_api_v1_storage_soe_post) | **POST** /api/v1/storage/soe | Set Storage Soe
[**system_status_api_v1_status_get**](DefaultApi.md#system_status_api_v1_status_get) | **GET** /api/v1/status | System Status


# **delete_client_api_v1_auth_clients_name_delete**
> Client delete_client_api_v1_auth_clients_name_delete(name)

Delete Client

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.client import Client
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    name = 'name_example' # str |

    try:
        # Delete Client
        api_response = api_instance.delete_client_api_v1_auth_clients_name_delete(name)
        print("The response of DefaultApi->delete_client_api_v1_auth_clients_name_delete:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->delete_client_api_v1_auth_clients_name_delete: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **name** | **str**|  |

### Return type

[**Client**](Client.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **deprecated_spaces_endpoint_stub_api_v1_spaces_get**
> object deprecated_spaces_endpoint_stub_api_v1_spaces_get(spaces_id=spaces_id)

Deprecated Spaces Endpoint Stub

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    spaces_id = '' # str |  (optional) (default to '')

    try:
        # Deprecated Spaces Endpoint Stub
        api_response = api_instance.deprecated_spaces_endpoint_stub_api_v1_spaces_get(spaces_id=spaces_id)
        print("The response of DefaultApi->deprecated_spaces_endpoint_stub_api_v1_spaces_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->deprecated_spaces_endpoint_stub_api_v1_spaces_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **spaces_id** | **str**|  | [optional] [default to &#39;&#39;]

### Return type

**object**

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_get**
> object deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_get(spaces_id)

Deprecated Spaces Endpoint Stub

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    spaces_id = 'spaces_id_example' # str |

    try:
        # Deprecated Spaces Endpoint Stub
        api_response = api_instance.deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_get(spaces_id)
        print("The response of DefaultApi->deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **spaces_id** | **str**|  |

### Return type

**object**

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_post**
> object deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_post(spaces_id)

Deprecated Spaces Endpoint Stub

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    spaces_id = 'spaces_id_example' # str |

    try:
        # Deprecated Spaces Endpoint Stub
        api_response = api_instance.deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_post(spaces_id)
        print("The response of DefaultApi->deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->deprecated_spaces_endpoint_stub_api_v1_spaces_spaces_id_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **spaces_id** | **str**|  |

### Return type

**object**

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **generate_jwt_api_v1_auth_register_post**
> AuthOut generate_jwt_api_v1_auth_register_post(auth_in)

Generate Jwt

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.auth_in import AuthIn
from span_panel_api.models.auth_out import AuthOut
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    auth_in = span_panel_api.AuthIn() # AuthIn |

    try:
        # Generate Jwt
        api_response = api_instance.generate_jwt_api_v1_auth_register_post(auth_in)
        print("The response of DefaultApi->generate_jwt_api_v1_auth_register_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->generate_jwt_api_v1_auth_register_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **auth_in** | [**AuthIn**](AuthIn.md)|  |

### Return type

[**AuthOut**](AuthOut.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_all_clients_api_v1_auth_clients_get**
> Clients get_all_clients_api_v1_auth_clients_get()

Get All Clients

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.clients import Clients
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get All Clients
        api_response = api_instance.get_all_clients_api_v1_auth_clients_get()
        print("The response of DefaultApi->get_all_clients_api_v1_auth_clients_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_all_clients_api_v1_auth_clients_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**Clients**](Clients.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_circuit_state_api_v1_circuits_circuit_id_get**
> Circuit get_circuit_state_api_v1_circuits_circuit_id_get(circuit_id)

Get Circuit State

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.circuit import Circuit
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    circuit_id = 'circuit_id_example' # str |

    try:
        # Get Circuit State
        api_response = api_instance.get_circuit_state_api_v1_circuits_circuit_id_get(circuit_id)
        print("The response of DefaultApi->get_circuit_state_api_v1_circuits_circuit_id_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_circuit_state_api_v1_circuits_circuit_id_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **circuit_id** | **str**|  |

### Return type

[**Circuit**](Circuit.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_circuits_api_v1_circuits_get**
> CircuitsOut get_circuits_api_v1_circuits_get()

Get Circuits

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.circuits_out import CircuitsOut
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Circuits
        api_response = api_instance.get_circuits_api_v1_circuits_get()
        print("The response of DefaultApi->get_circuits_api_v1_circuits_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_circuits_api_v1_circuits_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**CircuitsOut**](CircuitsOut.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_client_api_v1_auth_clients_name_get**
> Client get_client_api_v1_auth_clients_name_get(name)

Get Client

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.client import Client
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    name = 'name_example' # str |

    try:
        # Get Client
        api_response = api_instance.get_client_api_v1_auth_clients_name_get(name)
        print("The response of DefaultApi->get_client_api_v1_auth_clients_name_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_client_api_v1_auth_clients_name_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **name** | **str**|  |

### Return type

[**Client**](Client.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_islanding_state_api_v1_islanding_state_get**
> IslandingState get_islanding_state_api_v1_islanding_state_get()

Get Islanding State

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.islanding_state import IslandingState
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Islanding State
        api_response = api_instance.get_islanding_state_api_v1_islanding_state_get()
        print("The response of DefaultApi->get_islanding_state_api_v1_islanding_state_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_islanding_state_api_v1_islanding_state_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**IslandingState**](IslandingState.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_main_relay_state_api_v1_panel_grid_get**
> RelayStateOut get_main_relay_state_api_v1_panel_grid_get()

Get Main Relay State

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.relay_state_out import RelayStateOut
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Main Relay State
        api_response = api_instance.get_main_relay_state_api_v1_panel_grid_get()
        print("The response of DefaultApi->get_main_relay_state_api_v1_panel_grid_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_main_relay_state_api_v1_panel_grid_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**RelayStateOut**](RelayStateOut.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_panel_meter_api_v1_panel_meter_get**
> PanelMeter get_panel_meter_api_v1_panel_meter_get()

Get Panel Meter

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.panel_meter import PanelMeter
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Panel Meter
        api_response = api_instance.get_panel_meter_api_v1_panel_meter_get()
        print("The response of DefaultApi->get_panel_meter_api_v1_panel_meter_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_panel_meter_api_v1_panel_meter_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**PanelMeter**](PanelMeter.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_panel_power_api_v1_panel_power_get**
> PanelPower get_panel_power_api_v1_panel_power_get()

Get Panel Power

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.panel_power import PanelPower
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Panel Power
        api_response = api_instance.get_panel_power_api_v1_panel_power_get()
        print("The response of DefaultApi->get_panel_power_api_v1_panel_power_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_panel_power_api_v1_panel_power_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**PanelPower**](PanelPower.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_panel_state_api_v1_panel_get**
> PanelState get_panel_state_api_v1_panel_get()

Get Panel State

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.panel_state import PanelState
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Panel State
        api_response = api_instance.get_panel_state_api_v1_panel_get()
        print("The response of DefaultApi->get_panel_state_api_v1_panel_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_panel_state_api_v1_panel_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**PanelState**](PanelState.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get**
> NiceToHaveThreshold get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get()

Get Storage Nice To Have Threshold

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.nice_to_have_threshold import NiceToHaveThreshold
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Storage Nice To Have Threshold
        api_response = api_instance.get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get()
        print("The response of DefaultApi->get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**NiceToHaveThreshold**](NiceToHaveThreshold.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_storage_soe_api_v1_storage_soe_get**
> BatteryStorage get_storage_soe_api_v1_storage_soe_get()

Get Storage Soe

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.battery_storage import BatteryStorage
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Storage Soe
        api_response = api_instance.get_storage_soe_api_v1_storage_soe_get()
        print("The response of DefaultApi->get_storage_soe_api_v1_storage_soe_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_storage_soe_api_v1_storage_soe_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**BatteryStorage**](BatteryStorage.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_wifi_scan_api_v1_wifi_scan_get**
> WifiScanOut get_wifi_scan_api_v1_wifi_scan_get()

Get Wifi Scan

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.wifi_scan_out import WifiScanOut
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Get Wifi Scan
        api_response = api_instance.get_wifi_scan_api_v1_wifi_scan_get()
        print("The response of DefaultApi->get_wifi_scan_api_v1_wifi_scan_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->get_wifi_scan_api_v1_wifi_scan_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**WifiScanOut**](WifiScanOut.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post**
> object run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post()

Run Panel Emergency Reconnect

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # Run Panel Emergency Reconnect
        api_response = api_instance.run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post()
        print("The response of DefaultApi->run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->run_panel_emergency_reconnect_api_v1_panel_emergency_reconnect_post: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

**object**

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **run_wifi_connect_api_v1_wifi_connect_post**
> WifiConnectOut run_wifi_connect_api_v1_wifi_connect_post(wifi_connect_in)

Run Wifi Connect

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.wifi_connect_in import WifiConnectIn
from span_panel_api.models.wifi_connect_out import WifiConnectOut
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    wifi_connect_in = span_panel_api.WifiConnectIn() # WifiConnectIn |

    try:
        # Run Wifi Connect
        api_response = api_instance.run_wifi_connect_api_v1_wifi_connect_post(wifi_connect_in)
        print("The response of DefaultApi->run_wifi_connect_api_v1_wifi_connect_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->run_wifi_connect_api_v1_wifi_connect_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **wifi_connect_in** | [**WifiConnectIn**](WifiConnectIn.md)|  |

### Return type

[**WifiConnectOut**](WifiConnectOut.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **set_circuit_state_api_v1_circuits_circuit_id_post**
> Circuit set_circuit_state_api_v1_circuits_circuit_id_post(circuit_id, body_set_circuit_state_api_v1_circuits_circuit_id_post=body_set_circuit_state_api_v1_circuits_circuit_id_post)

Set Circuit State

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.body_set_circuit_state_api_v1_circuits_circuit_id_post import BodySetCircuitStateApiV1CircuitsCircuitIdPost
from span_panel_api.models.circuit import Circuit
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    circuit_id = 'circuit_id_example' # str |
    body_set_circuit_state_api_v1_circuits_circuit_id_post = span_panel_api.BodySetCircuitStateApiV1CircuitsCircuitIdPost() # BodySetCircuitStateApiV1CircuitsCircuitIdPost |  (optional)

    try:
        # Set Circuit State
        api_response = api_instance.set_circuit_state_api_v1_circuits_circuit_id_post(circuit_id, body_set_circuit_state_api_v1_circuits_circuit_id_post=body_set_circuit_state_api_v1_circuits_circuit_id_post)
        print("The response of DefaultApi->set_circuit_state_api_v1_circuits_circuit_id_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->set_circuit_state_api_v1_circuits_circuit_id_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **circuit_id** | **str**|  |
 **body_set_circuit_state_api_v1_circuits_circuit_id_post** | [**BodySetCircuitStateApiV1CircuitsCircuitIdPost**](BodySetCircuitStateApiV1CircuitsCircuitIdPost.md)|  | [optional]

### Return type

[**Circuit**](Circuit.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **set_main_relay_state_api_v1_panel_grid_post**
> object set_main_relay_state_api_v1_panel_grid_post(relay_state_in)

Set Main Relay State

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.relay_state_in import RelayStateIn
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    relay_state_in = span_panel_api.RelayStateIn() # RelayStateIn |

    try:
        # Set Main Relay State
        api_response = api_instance.set_main_relay_state_api_v1_panel_grid_post(relay_state_in)
        print("The response of DefaultApi->set_main_relay_state_api_v1_panel_grid_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->set_main_relay_state_api_v1_panel_grid_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **relay_state_in** | [**RelayStateIn**](RelayStateIn.md)|  |

### Return type

**object**

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **set_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_post**
> NiceToHaveThreshold set_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_post(nice_to_have_threshold)

Set Storage Nice To Have Threshold

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.nice_to_have_threshold import NiceToHaveThreshold
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    nice_to_have_threshold = span_panel_api.NiceToHaveThreshold() # NiceToHaveThreshold |

    try:
        # Set Storage Nice To Have Threshold
        api_response = api_instance.set_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_post(nice_to_have_threshold)
        print("The response of DefaultApi->set_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->set_storage_nice_to_have_threshold_api_v1_storage_nice_to_have_thresh_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **nice_to_have_threshold** | [**NiceToHaveThreshold**](NiceToHaveThreshold.md)|  |

### Return type

[**NiceToHaveThreshold**](NiceToHaveThreshold.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **set_storage_soe_api_v1_storage_soe_post**
> BatteryStorage set_storage_soe_api_v1_storage_soe_post(battery_storage)

Set Storage Soe

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.battery_storage import BatteryStorage
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)
    battery_storage = span_panel_api.BatteryStorage() # BatteryStorage |

    try:
        # Set Storage Soe
        api_response = api_instance.set_storage_soe_api_v1_storage_soe_post(battery_storage)
        print("The response of DefaultApi->set_storage_soe_api_v1_storage_soe_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->set_storage_soe_api_v1_storage_soe_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **battery_storage** | [**BatteryStorage**](BatteryStorage.md)|  |

### Return type

[**BatteryStorage**](BatteryStorage.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **system_status_api_v1_status_get**
> StatusOut system_status_api_v1_status_get()

System Status

### Example

* Bearer Authentication (HTTPBearer):

```python
import span_panel_api
from span_panel_api.models.status_out import StatusOut
from span_panel_api.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = span_panel_api.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: HTTPBearer
configuration = span_panel_api.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with span_panel_api.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = span_panel_api.DefaultApi(api_client)

    try:
        # System Status
        api_response = api_instance.system_status_api_v1_status_get()
        print("The response of DefaultApi->system_status_api_v1_status_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling DefaultApi->system_status_api_v1_status_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**StatusOut**](StatusOut.md)

### Authorization

[HTTPBearer](../README.md#HTTPBearer)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)
