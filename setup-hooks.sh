#!/bin/bash
#
# Prepare this checkout for development: install dependencies and make sure the
# git pre-commit hook is in place.
#
# Usage: ./setup-hooks.sh [--update | --check-hook]
#
#   --update      Upgrade the lockfile before syncing.
#   --check-hook  Only check the shared pre-commit hook, and exit non-zero when it
#                 is missing or not bound to the primary checkout.
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

usage() {
    echo "Usage: $0 [--update | --check-hook]" >&2
    exit 2
}

mode=setup
case "${1:-}" in
    "") ;;
    --update) mode=update ;;
    --check-hook) mode=check-hook ;;
    *) usage ;;
esac
[[ $# -le 1 ]] || usage

git_dir=$(git rev-parse --absolute-git-dir) || exit 1
common_dir=$(git rev-parse --path-format=absolute --git-common-dir) || exit 1
primary_root=$(dirname "$common_dir")

# Whether the shared hook exists and runs with the primary checkout's environment.
# Silent when it does; otherwise says what is wrong and how to fix it.
check_shared_hook() {
    local hook install_python

    hook=$(git rev-parse --path-format=absolute --git-path hooks/pre-commit)
    if [[ ! -f "$hook" ]]; then
        echo "No pre-commit hook is installed for this repository yet, so commits skip every"
        echo "check. Install it from the primary checkout, where it outlives every worktree:"
        echo "    (cd \"$primary_root\" && ./setup-hooks.sh)"
        return 1
    fi

    install_python=$(sed -n "s/^INSTALL_PYTHON=//p" "$hook")
    install_python=${install_python#\'}
    install_python=${install_python%\'}

    if [[ "$install_python" != "$primary_root"/.venv/* ]]; then
        echo "The shared pre-commit hook runs with ${install_python:-an unknown interpreter}, not the"
        echo "primary checkout's environment, so commits will fail once that environment is gone."
        echo "Rebind it from the primary checkout:"
        echo "    (cd \"$primary_root\" && ./setup-hooks.sh)"
        return 1
    fi

    if [[ ! -x "$install_python" ]]; then
        echo "The shared pre-commit hook runs with $install_python, which no longer exists, so"
        echo "commits will fail. Reinstall it from the primary checkout:"
        echo "    (cd \"$primary_root\" && ./setup-hooks.sh)"
        return 1
    fi
}

if [[ "$mode" == "check-hook" ]]; then
    check_shared_hook || exit 1
    echo "Shared pre-commit hook is bound to the primary checkout."
    exit 0
fi

if [[ "$mode" == "update" ]]; then
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

if [[ "$git_dir" == "$common_dir" ]]; then
    if ! uv run pre-commit install; then
        echo "Failed to install the pre-commit hook. Please check the output above."
        exit 1
    fi
    if ! check_shared_hook; then
        echo "Failed to bind the pre-commit hook to this checkout's environment."
        exit 1
    fi
    echo "Git hooks installed successfully!"
    exit 0
fi

# A linked worktree from here on.
if check_shared_hook; then
    echo "Shared pre-commit hook is bound to the primary checkout; leaving it alone."
fi
