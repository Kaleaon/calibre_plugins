# Developer & Agent Guidelines

This repository enforces static code quality checks, consistent formatting, and developer guardrails using [pre-commit](https://pre-commit.com/) and [Ruff](https://docs.astral.sh/ruff/).

---

## 1. Code Quality Tools & Configuration

- **Ruff Configuration (`pyproject.toml`)**:
  - Target Python version: `py310` (Calibre 6+ runtime compatibility).
  - Maximum line length: `120`.
  - Selected linter rule sets: `E` (pycodestyle errors), `F` (Pyflakes), `W` (pycodestyle warnings), `I` (isort import sorting), `UP` (pyupgrade).
  - Excluded paths: `third_party/`, `dist/`, `.git`, `.venv`, `build`.
- **Pre-Commit Hooks (`.pre-commit-config.yaml`)**:
  - `trailing-whitespace`: Removes trailing whitespace.
  - `end-of-file-fixer`: Ensures files end with a trailing newline.
  - `ruff`: Executes fast Python linting with automatic fixes where safe.
  - `ruff-format`: Formats Python files according to Ruff formatting rules.

---

## 2. Pre-Commit Setup & Usage

To set up pre-commit hooks locally:

```bash
# 1. Install pre-commit and ruff
pip install pre-commit ruff

# 2. Install pre-commit git hooks
pre-commit install

# 3. Manually run pre-commit against all files
pre-commit run --all-files
```

---

## 3. Static Check Commands

You can run Ruff directly without pre-commit:

```bash
# Check lint rules across the repository
ruff check .

# Automatically fix linting issues
ruff check --fix .

# Check code formatting without making changes
ruff format --check .

# Auto-format Python code
ruff format .
```

---

## 4. Test Suite & Plugin Builds

Run unit tests locally before committing changes:

```bash
# Run unit tests
python3 -m unittest discover -s tests -p "test_*.py" -v

# Build all plugin ZIP archives (outputs to dist/)
python3 build_plugins.py
python3 build.py
```

---

## 5. Guardrails & Best Practices

1. **Clean Git History**: Avoid bulk reformatting unrelated legacy plugins in a single pull request. Keep code changes focused on the relevant plugin or tool.
2. **Calibre Compatibility**: Ensure code remains compatible with Calibre 6+ runtime environments (Python 3.10+).
3. **Vendored Third-Party Code**: Do not format or alter files inside `third_party/` unless explicitly required for vendoring updates.
