# Developer Guide (`AGENTS.md`)

This guide provides instructions and reference commands for automated agents and human developers working on the `calibre_plugins` codebase.

---

## 1. Overview & Setup

### Environment Requirements
- **Python**: 3.12+
- **Calibre Dependencies & Qt**: GUI tests and dialog tests rely on `PyQt6`.

### Installing Dependencies

Install system dependencies required by Qt on Linux (Ubuntu/Debian):
```bash
sudo apt-get update
sudo apt-get install -y libegl1 libgl1-mesa-dev libxkbcommon-x11-0 libdbus-1-3 libxcb-cursor0
```

Install Python dependencies:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Set up pre-commit git hooks:
```bash
pre-commit install
```

---

## 2. Environment Variables

To match the continuous integration (CI) pipeline in `.github/workflows/ci.yml`, export or set these environment variables when running build scripts and test suites:

- `QT_QPA_PLATFORM=offscreen`: Allows Qt applications and dialog tests to execute in headless mode without requiring an X11 or Wayland display server.
- `PYTHONPATH=.`: Ensures Python resolves workspace root packages and local plugin modules correctly.

---

## 3. Building Plugin Packages (Strict Mode)

To build all 9 Calibre plugin zip packages into the `dist/` directory in strict packaging mode:

```bash
python build.py
python build_plugins.py --strict
```

The `--strict` flag ensures the build fails if vendored dependencies (such as `fanficfare`) or shared modules are missing.

---

## 4. Running Tests

### Standard Test Execution with `pytest`
```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=. pytest
```

### Test Suite Execution with `unittest` (CI Alignment)
To run the exact unit test suite command executed in CI:
```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py" -v
```

### Edge Case: Environments Without Qt / PyQt6
- Tests in `tests/test_qt_dialogs.py` and `tests/test_media_dialogs.py` test Calibre dialog windows under `PyQt6`.
- If `PyQt6` or required system Qt libraries (`libegl1`, etc.) are missing, the test runner will automatically skip GUI/dialog tests without failing the rest of the test suite.

---

## 5. Code Quality & Formatting

### Linting with Ruff
To run the Ruff linter across the codebase:
```bash
ruff check .
```

To automatically fix safe lint issues:
```bash
ruff check . --fix
```

### Code Formatting with Ruff
To check code formatting:
```bash
ruff format --check .
```

To apply code formatting across the repository:
```bash
ruff format .
```

### Pre-commit Hooks
To run pre-commit hooks manually against all files:
```bash
pre-commit run --all-files
```
