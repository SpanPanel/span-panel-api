#!/bin/bash
#
# Prepare this checkout for development: install dependencies and make sure the
# git pre-commit hook is in place. Pass --update to upgrade the lockfile first.
#
# Safe to run from the primary checkout and from a linked worktree, which differ
# in the one respect that matters here. Git hooks live in the repository's common
# directory and every worktree shares them, and `pre-commit install` writes the
# interpreter that runs it into the hook. Installing from a worktree therefore
# binds every checkout's commits to that worktree's virtual environment -- which
# is deleted with the worktree, after which every commit fails with "`pre-commit`
# not found". So only the primary checkout installs the hook. A worktree checks
# that the shared hook is bound to the primary checkout and says how to fix it if
# not, and leaves it alone either way.

FORCE_UPDATE=false
if [[ "${1:-}" == "--update" ]]; then
    FORCE_UPDATE=true
fi

if [[ "$FORCE_UPDATE" == "true" ]]; then
    echo "Forcing update to latest versions..."
    if ! uv lock --upgrade; then
        echo "Failed to upgrade the lockfile. Please check the output above."
        exit 1
    fi
fi

# Every run syncs. uv is idempotent and clones packages from its cache, so an
# environment that is already current costs little. This used to be gated on a
# stamp file that had been committed to the repository, which meant a fresh
# worktree checked the stamp out and whether it got an environment at all came
# down to the order in which checkout happened to write files.
echo "Installing/updating dependencies..."
if ! uv sync; then
    echo "Failed to install dependencies. Please check the output above."
    exit 1
fi

git_dir=$(git rev-parse --absolute-git-dir)
common_dir=$(git rev-parse --path-format=absolute --git-common-dir)

if [[ "$git_dir" == "$common_dir" ]]; then
    if ! uv run pre-commit install; then
        echo "Failed to install the pre-commit hook. Please check the output above."
        exit 1
    fi
    echo "Git hooks installed successfully!"
    exit 0
fi

# A linked worktree from here on.
primary_root=$(dirname "$common_dir")
hook=$(git rev-parse --path-format=absolute --git-path hooks/pre-commit)

if [[ ! -f "$hook" ]]; then
    echo "No pre-commit hook is installed for this repository yet. Install it from the"
    echo "primary checkout, where it outlives every worktree:"
    echo "    (cd \"$primary_root\" && ./setup-hooks.sh)"
    exit 0
fi

install_python=$(sed -n "s/^INSTALL_PYTHON=//p" "$hook")
install_python=${install_python#\'}
install_python=${install_python%\'}

if [[ "$install_python" != "$primary_root"/.venv/* ]]; then
    echo "The shared pre-commit hook runs with ${install_python:-an unknown interpreter}, not the"
    echo "primary checkout's environment, so commits will fail once that environment is gone."
    echo "Rebind it from the primary checkout:"
    echo "    (cd \"$primary_root\" && ./setup-hooks.sh)"
    exit 0
fi

if [[ ! -x "$install_python" ]]; then
    echo "The shared pre-commit hook runs with $install_python, which no longer exists, so"
    echo "commits will fail. Reinstall it from the primary checkout:"
    echo "    (cd \"$primary_root\" && ./setup-hooks.sh)"
    exit 0
fi

echo "Shared pre-commit hook is bound to the primary checkout; leaving it alone."
