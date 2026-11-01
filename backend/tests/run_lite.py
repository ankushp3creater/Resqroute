"""Stdlib-only test runner (no pytest needed): python -m backend.tests.run_lite"""
import importlib, sys, traceback

MODULES = ["backend.tests.test_core", "backend.tests.test_agent", "backend.tests.test_workflow"]

def main() -> int:
    passed = failed = 0
    for name in MODULES:
        mod = importlib.import_module(name)
        for fn in sorted(n for n in dir(mod) if n.startswith("test_")):
            try:
                getattr(mod, fn)()
                passed += 1
                print(f"PASS {name.split('.')[-1]}::{fn}")
            except Exception:
                failed += 1
                print(f"FAIL {name.split('.')[-1]}::{fn}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
