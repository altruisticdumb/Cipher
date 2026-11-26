"""Runs every test file and prints one line per file (use this after each change)."""
import subprocess, sys
from pathlib import Path

here = Path(__file__).resolve().parent
files = sorted(here.glob("test_*.py"))
failed = 0
for f in files:
    r = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, cwd=here.parent)
    print(("PASS " if r.returncode == 0 else "FAIL ") + f.name)
    if r.returncode != 0:
        failed += 1
        print((r.stdout + r.stderr)[-1500:])
print(f"{len(files) - failed}/{len(files)} test files passed")
sys.exit(1 if failed else 0)
