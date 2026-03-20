# Development

## Prerequisites

- Python 3.10+ (CI tests 3.13 and 3.14)
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
git clone https://github.com/SpanPanel/span-panel-api.git
cd span-panel-api
uv sync
```

## Testing

```bash
# Full test suite
uv run pytest

# Verbose with coverage
uv run pytest tests/ -v --cov=src/span_panel_api --cov-report=term-missing

# Check coverage meets threshold (85%)
python scripts/coverage.py --check --threshold 85

# Full coverage report
python scripts/coverage.py --full
```

## Linting and Formatting

Pre-commit hooks run automatically on commit. To run all hooks manually:

```bash
uv run pre-commit run --all-files
```

Individual tools:

```bash
# Ruff (lint + format)
uv run ruff check src/
uv run ruff format src/

# Type checking
uv run mypy src/

# Security scan
uv run bandit -r src/

# Dead code detection
uv run vulture src/span_panel_api/ --min-confidence 80
```

Or use the combined format script:

```bash
./scripts/format.sh
```

## Git Hooks

To install pre-commit hooks:

```bash
./setup-hooks.sh
```

This installs dependencies (if needed) and configures git pre-commit hooks.

## Contributing

1. Fork and clone the repository
2. Install dev dependencies: `uv sync`
3. Make changes and add tests
4. Ensure all checks pass: `uv run pytest && uv run mypy src/ && uv run ruff check src/`
5. Submit a pull request
