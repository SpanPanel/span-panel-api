#!/usr/bin/env python3
"""Script to format markdown files using markdownlint-cli2."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Run markdownlint-cli2 with auto-fix on markdown files."""
    try:
        # Run markdownlint-cli2 with fix enabled
        result = subprocess.run(
            ["npx", "markdownlint-cli2", "--config", ".markdownlint-cli2.jsonc", "--fix", "**/*.md"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        if result.returncode == 0:
            print("✅ Markdown formatting complete!")
        else:
            print("❌ Markdown formatting failed:")
            print(result.stderr)
            sys.exit(1)

    except FileNotFoundError:
        print("❌ markdownlint-cli2 not found. Please install it with: npm install -g markdownlint-cli2")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error running markdown formatter: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
