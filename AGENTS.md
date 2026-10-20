# Developer Guidelines

## Code Quality & Linting

Before pushing code changes or submitting pull requests, ensure that static code analysis and formatting checks pass locally:

```bash
ruff check .
ruff format --check .
```

To automatically format Python files locally:

```bash
ruff format .
```

## Running Tests

Execute the unit test suite using `pytest` with `PYTHONPATH=.` and `QT_QPA_PLATFORM=offscreen` set in your environment:

```bash
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python -m pytest tests/
```

All CI workflow checks in `.github/workflows/ci.yml` enforce these exact commands and fail on any non-zero exit code.
