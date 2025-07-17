#!/usr/bin/env python3
"""Live Bearer Token Authentication Test Script.

This script tests Bearer token authentication with a real SPAN panel to verify:
- Bearer token formatting is correct
- Valid tokens authenticate successfully
- Invalid tokens are properly rejected
- Client connects to real panel (not simulation)

Usage:
    poetry run python scripts/test_live_auth.py <panel_ip> <jwt_token>

Example:
    poetry run python scripts/test_live_auth.py 192.168.1.100 eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
"""

import asyncio
import sys

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError, SpanPanelTimeoutError


async def test_bearer_auth(panel_ip: str, jwt_token: str) -> None:  # pragma: no cover
    """Test Bearer token authentication with a real panel."""
    print(f"🔍 Testing Bearer token authentication with panel: {panel_ip}")
    print(f"🎫 Token: {jwt_token[:20]}..." if len(jwt_token) > 20 else f"🎫 Token: {jwt_token}")
    print()

    async with SpanPanelClient(host=panel_ip, timeout=10.0) as client:
        try:
            # Debug: Check client configuration
            print("🔧 Client configuration:")
            print(f"   Host: {client._host}")
            print(f"   Simulation mode: {getattr(client, '_simulation_mode', 'Not set')}")
            print(f"   Has simulation engine: {client._simulation_engine is not None}")
            if client._simulation_engine is not None:
                print("   ⚠️  SIMULATION ENGINE IS ACTIVE!")
                # Check if simulation engine has a config
                if hasattr(client._simulation_engine, "_config") and client._simulation_engine._config:
                    print(f"   📋 Simulation config loaded: {type(client._simulation_engine._config)}")
                    if hasattr(client._simulation_engine._config, "panel") and hasattr(
                        client._simulation_engine._config.panel, "serial_number"
                    ):
                        print(f"   🔢 Simulation serial: {client._simulation_engine._config.panel.serial_number}")
            print()

            # Set the Bearer token
            print("📝 Setting Bearer token...")
            client.set_access_token(jwt_token)

            # Verify the client is set up correctly
            auth_client = client._get_client_for_endpoint(requires_auth=True)
            httpx_client = auth_client.get_async_httpx_client()
            auth_header = httpx_client.headers.get("Authorization")

            print(
                f"✅ Authorization header set: {auth_header[:30]}..."
                if len(auth_header) > 30
                else f"✅ Authorization header set: {auth_header}"
            )
            print()

            # Test 0: Get panel info to verify we're connecting to real panel
            print("📟 Getting panel information...")
            try:
                status = await client.get_status()
                print("✅ Panel info retrieved successfully!")
                print(f"   📟 Serial Number: {status.system.serial}")
                print(f"   🏭 Manufacturer: {status.system.manufacturer}")
                print(f"   📱 Model: {status.system.model}")
                print(f"   🚪 Door State: {status.system.door_state}")
                print(f"   ⏱️  Uptime: {status.system.uptime}s")
                print()
            except SpanPanelAuthError as e:
                print(f"❌ Authentication failed: {e}")
                print("   This suggests the token is invalid, expired, or insufficient permissions")
                return
            except Exception as e:
                print(f"❌ Unexpected error: {type(e).__name__}: {e}")
                return

            # Test 1: Get panel state (requires auth)
            print("🏠 Testing get_panel_state() (requires authentication)...")
            try:
                panel_state = await client.get_panel_state()
                print("✅ Panel state retrieved successfully!")
                print(f"   🔌 Main relay: {panel_state.main_relay_state}")
                print(f"   ⚡ Grid power: {panel_state.instant_grid_power_w}W")
                print(f"   🏭 Run config: {panel_state.current_run_config}")
                print(f"   🌐 DSM state: {panel_state.dsm_state}")
                print()
            except SpanPanelAuthError as e:
                print(f"❌ Authentication failed: {e}")
                print("   This suggests the token is invalid, expired, or insufficient permissions")
                return
            except Exception as e:
                print(f"❌ Unexpected error: {type(e).__name__}: {e}")
                return

            # Test 2: Get circuits (requires auth)
            print("🔌 Testing get_circuits() (requires authentication)...")
            try:
                circuits = await client.get_circuits()
                circuit_count = (
                    len(circuits.circuits.additional_properties)
                    if hasattr(circuits.circuits, "additional_properties")
                    else 0
                )
                print("✅ Circuits retrieved successfully!")
                print(f"   📈 Circuit count: {circuit_count}")

                # Show first few circuits
                if hasattr(circuits.circuits, "additional_properties"):
                    for i, (circuit_id, circuit) in enumerate(circuits.circuits.additional_properties.items()):
                        if i >= 3:  # Only show first 3
                            break
                        power = getattr(circuit, "instant_power_w", "N/A")
                        name = getattr(circuit, "name", "Unknown")
                        print(f"   ⚡ {circuit_id}: {name} ({power}W)")
                print()
            except SpanPanelAuthError as e:
                print(f"❌ Authentication failed: {e}")
                return
            except Exception as e:
                print(f"❌ Unexpected error: {type(e).__name__}: {e}")
                return

            # Test 3: Test invalid token
            print("🚫 Testing with invalid token...")

            # Store original token
            original_token = (
                client._client._auth.token
                if hasattr(client._client, "_auth") and hasattr(client._client._auth, "token")
                else None
            )

            try:
                # Use multiple invalid token formats to test
                invalid_tokens = ["invalid-token-12345", "completely-bogus-token", "Bearer invalid", "", "expired.jwt.token"]

                for i, invalid_token in enumerate(invalid_tokens):
                    print(
                        f"   Testing invalid token {i + 1}/{len(invalid_tokens)}: '{invalid_token[:20]}{'...' if len(invalid_token) > 20 else ''}'"
                    )

                    try:
                        # Create a FRESH client instance for each invalid token test to avoid connection reuse
                        print("      🔄 Creating fresh client instance...")
                        async with SpanPanelClient(host=panel_ip, timeout=10.0) as fresh_client:
                            fresh_client.set_access_token(invalid_token)

                            # Debug: Check what headers are actually being sent
                            auth_client = fresh_client._get_client_for_endpoint(requires_auth=True)
                            httpx_client = auth_client.get_async_httpx_client()
                            actual_header = httpx_client.headers.get("Authorization", "None")
                            print(f"      🔍 Sending header: {actual_header[:50]}{'...' if len(actual_header) > 50 else ''}")

                            result = await fresh_client.get_panel_state()
                            print(f"   ❌ ERROR: Invalid token was accepted for token {i + 1}")
                            print(f"      Token: {invalid_token[:50]}{'...' if len(invalid_token) > 50 else ''}")
                            print(f"      Response received: {type(result)}")
                            print("      ⚠️  This suggests the panel may be in development/bypass mode")
                    except SpanPanelAuthError as e:
                        print(f"   ✅ Invalid token {i + 1} correctly rejected: {e}")
                        break  # Found working auth rejection, no need to test more
                    except Exception as e:
                        print(f"   ⚠️  Unexpected error type for invalid token {i + 1}: {type(e).__name__}: {e}")

            except Exception as e:
                print(f"   ❌ Error during invalid token testing: {type(e).__name__}: {e}")

            # Restore original token if we had one
            if original_token:
                client.set_access_token(original_token)

            # Test 4: Test with NO authentication at all
            print("❓ Testing with NO authentication (no token)...")
            try:
                # Create a new client without any authentication
                unauthenticated_client = SpanPanelClient(panel_ip)
                result = await unauthenticated_client.get_panel_state()
                print("   ❌ ERROR: Panel allows access without any authentication!")
                print("   ℹ️  Your panel appears to be in development/bypass mode")
            except SpanPanelAuthError as e:
                print(f"   ✅ No auth correctly rejected: {e}")
            except Exception as e:
                print(f"   ⚠️  Unexpected error: {type(e).__name__}: {e}")

            # Test 5: Raw HTTP test like browser would make
            print()
            print("🌐 Testing raw HTTP request (like browser)...")
            try:
                import httpx

                async with httpx.AsyncClient() as http_client:
                    # Try with a bad token like browser would - use an endpoint that requires auth
                    headers = {"Authorization": "Bearer invalid-browser-token"}
                    url = f"http://{panel_ip}/api/v1/panel"

                    print(f"   🔍 Making direct request to: {url}")
                    print("   🔍 With header: Authorization: Bearer invalid-browser-token")

                    response = await http_client.get(url, headers=headers, timeout=10.0)

                    print(f"   📊 HTTP Status: {response.status_code}")
                    if response.status_code == 401:
                        print("   ✅ Raw HTTP correctly rejected invalid token")
                    elif response.status_code == 403:
                        print("   ✅ Raw HTTP correctly rejected invalid token (forbidden)")
                    elif response.status_code == 200:
                        print("   ❌ Raw HTTP accepted invalid token!")
                        print("   ⚠️  This confirms the panel is in development/bypass mode")
                    else:
                        print(f"   ⚠️  Unexpected HTTP status: {response.status_code}")

            except Exception as e:
                print(f"   ❌ Raw HTTP test failed: {type(e).__name__}: {e}")

            print()
            print("🎉 Bearer token authentication test completed!")

        except SpanPanelConnectionError as e:
            print(f"❌ Connection failed: {e}")
            print("   Check that the panel IP is correct and reachable")
        except SpanPanelTimeoutError as e:
            print(f"❌ Request timed out: {e}")
            print("   The panel may be slow to respond or unreachable")
        except SpanPanelAuthError as e:
            print(f"❌ Authentication error: {e}")
            print("   The token may be invalid or expired")
        except Exception as e:
            print(f"❌ Unexpected error: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()


def main() -> None:
    """Main entry point."""
    if len(sys.argv) != 3:
        print("❌ Error: Missing required arguments")
        print()
        print("📋 SPAN Panel Live Bearer Token Authentication Test")
        print("=" * 60)
        print()
        print("This script tests Bearer token authentication with a real SPAN panel.")
        print("It verifies that:")
        print("  • Bearer tokens are properly formatted")
        print("  • Valid tokens authenticate successfully")
        print("  • Invalid tokens are properly rejected")
        print("  • The client connects to the real panel (not simulation)")
        print()
        print("🔧 Usage:")
        print("  poetry run python scripts/test_live_auth.py <panel_ip> <jwt_token>")
        print()
        print("📝 Examples:")
        print("  poetry run python scripts/test_live_auth.py 192.168.1.100 eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")
        print("  poetry run python scripts/test_live_auth.py span-panel.local eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")
        print()
        print("ℹ️  To get a JWT token:")
        print("  1. Access your SPAN panel web interface")
        print("  2. Open browser developer tools (F12)")
        print("  3. Go to Network tab")
        print("  4. Log in to the panel")
        print("  5. Look for API requests with Authorization: Bearer <token>")
        print("  6. Copy the token (without 'Bearer ' prefix)")
        print()
        print("⚠️  Security note: JWT tokens are sensitive credentials!")
        print("   • Don't share tokens in logs or commits")
        print("   • Tokens expire and will need to be refreshed")
        sys.exit(1)

    panel_ip = sys.argv[1]
    jwt_token = sys.argv[2]

    # Basic validation
    if not panel_ip or not jwt_token:
        print("❌ Error: Both panel IP and JWT token are required")
        sys.exit(1)

    try:
        asyncio.run(test_bearer_auth(panel_ip, jwt_token))
    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
