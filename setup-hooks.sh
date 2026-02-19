#!/bin/bash

# Check if we should force update (pass --update flag)
FORCE_UPDATE=false
if [[ "$1" == "--update" ]]; then
    FORCE_UPDATE=true
fi

# Detect a stale .deps-installed marker (e.g. venv was recreated after deps were last installed)
VENV_PYTHON="$(poetry env info --path 2>/dev/null)/bin/python"
if [[ -f ".deps-installed" ]] && ! "$VENV_PYTHON" -c "import pre_commit" 2>/dev/null; then
    echo "Virtual environment is missing installed packages; reinstalling..."
    rm -f .deps-installed
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
if ! poetry run pre-commit install; then
    echo "Failed to install pre-commit hooks. Please check the output above." >&2
    exit 1
fi

echo "Git hooks installed successfully!"
