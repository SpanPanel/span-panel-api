#!/bin/bash

# Exit on error
set -e

# First run ruff formatter to handle most formatting
# Use specific include paths rather than '.' to avoid .venv completely
# Explicitly avoid generate_client.py by not including it in the paths
poetry run ruff format src/ tests/ scripts/ \
  --exclude=src/span_panel_api/generated_client/**,scripts/coverage.py

# Then run black for end-of-file and blank line formatting only
# Use a more specific approach to exclude .venv and generate_client.py
# First specify included files instead of using '.' to avoid .venv completely
poetry run black src/ tests/ scripts/ \
  --exclude="src/span_panel_api/generated_client|scripts/coverage\.py|generate_client\.py"

# Finally run ruff check to ensure all linting passes
# Explicitly avoid generate_client.py by not including it in the paths
poetry run ruff check src/ tests/ scripts/ \
  --exclude=src/span_panel_api/generated_client/**,scripts/coverage.py

echo "✅ Formatting complete!"
