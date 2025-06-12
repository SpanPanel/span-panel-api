#!/usr/bin/env python3
"""
Coverage utility script for on-demand coverage reporting.

Usage:
    python scripts/coverage.py           # Quick summary
    python scripts/coverage.py --full    # Full detailed report
    python scripts/coverage.py --check   # Just check if coverage is above threshold
"""

import argparse
from pathlib import Path
import subprocess  # nosec B404 # Safe use with hardcoded commands
import sys


def run_coverage(full_report: bool = False, check_only: bool = False, threshold: int = 80) -> bool:
    """Run pytest with coverage and provide smart output."""

    if check_only:
        # Just run tests and get coverage percentage
        cmd = [
            "poetry",
            "run",
            "pytest",
            "tests/",
            "--cov=src/span_panel_api",
            "--cov-config=pyproject.toml",
            "--cov-report=term:skip-covered",
            "-q",  # Quiet mode
        ]
    elif full_report:
        # Full verbose report
        cmd = [
            "poetry",
            "run",
            "pytest",
            "tests/",
            "--cov=src/span_panel_api",
            "--cov-config=pyproject.toml",
            "--cov-report=term-missing",
            "-v",
        ]
    else:
        # Quick summary
        cmd = [
            "poetry",
            "run",
            "pytest",
            "tests/",
            "--cov=src/span_panel_api",
            "--cov-config=pyproject.toml",
            "--cov-report=term:skip-covered",
            "-q",
        ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent
        )  # nosec B603 # Safe use with hardcoded command list

        if check_only:
            # Extract just the coverage percentage
            lines = result.stdout.split("\n")
            for line in lines:
                if "TOTAL" in line and "%" in line:
                    parts = line.split()
                    for part in parts:
                        if "%" in part:
                            coverage = int(part.replace("%", ""))
                            if coverage < threshold:
                                print(f"❌ Coverage: {coverage}% (below {threshold}% threshold)")
                                print("💡 Run 'python scripts/coverage.py --full' to see what needs testing")
                                return False
                            else:
                                print(f"✅ Coverage: {coverage}% (above {threshold}% threshold)")
                                return True
        else:
            # Show the output
            if result.stdout:
                print(result.stdout)

            # Parse coverage and give hints
            lines = result.stdout.split("\n")
            for line in lines:
                if "TOTAL" in line and "%" in line:
                    parts = line.split()
                    for part in parts:
                        if "%" in part:
                            coverage = int(part.replace("%", ""))
                            if coverage < threshold:
                                print(f"\n💡 Coverage is {coverage}% (below {threshold}%)")
                                if not full_report:
                                    print("   Run 'python scripts/coverage.py --full' for detailed missing lines")
                            break

        return result.returncode == 0

    except subprocess.CalledProcessError as e:
        print(f"Error running coverage: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run coverage analysis")
    parser.add_argument("--full", action="store_true", help="Show full detailed coverage report")
    parser.add_argument("--check", action="store_true", help="Just check if coverage meets threshold")
    parser.add_argument("--threshold", type=int, default=80, help="Coverage threshold percentage (default: 80)")

    args = parser.parse_args()

    success = run_coverage(full_report=args.full, check_only=args.check, threshold=args.threshold)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
