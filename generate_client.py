#!/usr/bin/env python3
"""
Generate SPAN Panel OpenAPI Client

Simple script to generate the httpx-based OpenAPI client from the OpenAPI spec.
This creates the raw generated client in generated_client/ which is then used
by the wrapper in src/span_panel_api/
"""

from pathlib import Path
import subprocess  # nosec B404
import sys


def main() -> int:
    """Generate the OpenAPI client."""
    print("🚀 Generating SPAN Panel OpenAPI Client")
    print("=" * 50)

    # Check that we have the OpenAPI spec
    openapi_spec = Path("openapi.json")
    if not openapi_spec.exists():
        print(f"❌ OpenAPI spec not found: {openapi_spec}")
        return 1

    # Clean up any existing generated client
    generated_dir = Path("src/span_panel_api/generated_client")
    if generated_dir.exists():
        print(f"🧹 Removing existing generated client: {generated_dir}")
        subprocess.run(["rm", "-rf", str(generated_dir)], check=True)  # nosec B603, B607

    # Generate the client
    print("⚙️  Running openapi-python-client...")
    cmd = [
        "poetry",
        "run",
        "openapi-python-client",
        "generate",
        "--path",
        str(openapi_spec),
        "--meta",
        "none",
        "--overwrite",
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603

    # Check what was created regardless of exit code (might fail on ruff but files still generated)
    created_dirs = [d for d in Path(".").iterdir() if d.is_dir() and d.name.startswith(("span", "generated"))]

    if created_dirs:
        created_dir = created_dirs[0]
        print(f"📁 Client generated in: {created_dir}")

        # Move to our package structure
        target_dir = Path("src/span_panel_api/generated_client")
        if created_dir.name != target_dir.name or created_dir != target_dir:
            print(f"📝 Moving {created_dir} to {target_dir}")
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            created_dir.rename(target_dir)

        if result.returncode == 0:
            print("✅ Generation completed successfully!")
        else:
            print("⚠️  Generation completed with warnings (likely ruff formatting issues)")
            if "ruff failed" in result.stderr:
                print("   The files were generated but ruff found some formatting issues.")
                print("   This is expected due to OpenAPI naming conflicts and can be ignored.")

        print("✅ Client ready in src/span_panel_api/generated_client/")
        return 0
    else:
        print("❌ Generation failed - no client directory was created")
        if result.stdout:
            print("STDOUT:", result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
