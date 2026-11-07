import os
import sys
from pathlib import Path

# Ensure ROOT and common directories are in sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "common") not in sys.path:
    sys.path.insert(0, str(ROOT / "common"))

# Ensure Qt runs in headless / offscreen mode during tests
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Keep a module-level reference to QApplication so it isn't garbage collected
_GLOBAL_TEST_APP = None

try:
    from PyQt6.QtWidgets import QApplication

    _GLOBAL_TEST_APP = QApplication.instance() or QApplication([])
except Exception:
    pass
