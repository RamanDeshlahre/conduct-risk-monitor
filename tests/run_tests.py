"""Minimal runner for environments without pytest. With pytest installed, just run `pytest`."""
import inspect
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import test_analytics, test_parsers  # noqa: E402

failed = 0
for module in (test_parsers, test_analytics):
    for name, fn in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS {module.__name__.split('.')[-1]}::{name}")
            except Exception:
                failed += 1
                print(f"FAIL {module.__name__.split('.')[-1]}::{name}")
                traceback.print_exc()
print(f"\n{'All tests passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
