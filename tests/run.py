"""Offline standard-library tests; no credentials or network calls."""

from pathlib import Path
import sys
import unittest

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "tools"), str(root / "tests")]
suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
