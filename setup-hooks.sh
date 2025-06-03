#!/bin/bash

# Check if we should force update (pass --update flag)
FORCE_UPDATE=false
if [[ "$1" == "--update" ]]; then
    FORCE_UPDATE=true
fi

# Ensure dependencies are installed first
if [[ ! -f ".deps-installed" ]] || [[ "pyproject.toml" -nt ".deps-installed" ]] || [[ "$FORCE_UPDATE" == "true" ]]; then
    echo "Installing/updating dependencies..."

    if [[ "$FORCE_UPDATE" == "true" ]]; then
        echo "Forcing update to latest versions..."
        poetry update
    else
        poetry install --with dev,generate
    fi

    if [[ $? -ne 0 ]]; then
        echo "Failed to install dependencies. Please check the output above."
        exit 1
    fi
    touch .deps-installed
fi

# Install pre-commit hooks
poetry run pre-commit install

echo "Git hooks installed successfully!"
