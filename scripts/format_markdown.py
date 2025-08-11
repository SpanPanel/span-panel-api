#!/usr/bin/env python3
"""Script to format markdown files using Prettier."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Run Prettier with auto-fix on markdown files."""
    try:
        # Run Prettier with fix enabled on markdown files
        result = subprocess.run(
            ["npx", "prettier", "--write", "--config", ".prettierrc.json", "**/*.md"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        if result.returncode == 0:
            print("✅ Markdown formatting complete!")
            if result.stdout:
                print("Formatted files:")
                print(result.stdout)
        else:
            print("❌ Markdown formatting failed:")
            print(result.stderr)
            sys.exit(1)

    except FileNotFoundError:
        print("❌ Prettier not found. Please install it with: npm install -g prettier")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error running markdown formatter: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
