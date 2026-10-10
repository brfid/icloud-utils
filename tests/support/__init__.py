"""Shared disposable fixtures; source imports are confined to test processes."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
PYTHON_SOURCE = ROOT / 'python'
if str(PYTHON_SOURCE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SOURCE))
