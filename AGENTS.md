# Developer Guide (`AGENTS.md`)

This guide provides instructions and reference commands for automated agents and human developers working on the `calibre_plugins` codebase.

---

## 1. Overview & Setup

### Environment Requirements
- **Python**: 3.12+
- **Go**: 1.27.3+ (required for building and testing the Calibre Link Tailscale sidecar in `calibre_link/sidecar/`)
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

To build all 10 suite plugin zip packages into the `dist/` directory in strict packaging mode (the eleventh tool, Calibre Link, is built separately from `calibre_link/` via its Go sidecar and its release ZIP is committed to `dist/` prebuilt):

```bash
python build.py
python build_plugins.py --strict
```

The `--strict` flag ensures the build fails if vendored dependencies (such as `fanficfare`) or shared modules are missing.

### Building Go Sidecar Binaries (Calibre Link)

The Calibre Link plugin bundles Go-compiled Tailscale sidecar binaries (`calibre-tsnet-windows-amd64.exe` and `calibre-tsnet-linux-amd64`). To compile the sidecar binaries and package `CalibreLink.zip`:

On Linux/macOS:
```bash
calibre_link/sidecar/build.sh
python calibre_link/build_zip.py
```

On Windows (PowerShell):
```powershell
New-Item -ItemType Directory -Force -Path calibre_link/tailscale
$env:CGO_ENABLED = "0"; $env:GOOS = "windows"; $env:GOARCH = "amd64"
go build -trimpath -ldflags "-s -w" -o calibre_link/tailscale/calibre-tsnet-windows-amd64.exe ./calibre_link/sidecar
$env:GOOS = "linux"
go build -trimpath -ldflags "-s -w" -o calibre_link/tailscale/calibre-tsnet-linux-amd64 ./calibre_link/sidecar
python calibre_link/build_zip.py
```

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

### Go Sidecar Unit Tests & Vet Checks
To run the Go sidecar unit test suite and vet checks in `calibre_link/sidecar/`:
```bash
cd calibre_link/sidecar
go test -v ./...
go vet ./...
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
