"""Fixes a one-line bug in Flet 1.1.2's `flet pack` on Windows:

    ValueError: binary mode doesn't take an encoding argument

It changes  open(exe_path, "ab", encoding="utf-8")  to  open(exe_path, "ab")
in flet_cli's win_utils.py. Run it once:   py patch_flet_pack.py
(Reinstalling flet-cli puts the original file back.)
"""
import importlib.util
from pathlib import Path

BAD = 'open(exe_path, "ab", encoding="utf-8")'
GOOD = 'open(exe_path, "ab")'

spec = importlib.util.find_spec("flet_cli")
if spec is None or not spec.submodule_search_locations:
    raise SystemExit("flet-cli is not installed for this Python.")
target = Path(spec.submodule_search_locations[0]) / "__pyinstaller" / "win_utils.py"
if not target.exists():
    raise SystemExit(f"Couldn't find {target}")

text = target.read_text(encoding="utf-8")
if BAD in text:
    target.write_text(text.replace(BAD, GOOD), encoding="utf-8")
    print("Patched:", target)
elif GOOD in text:
    print("Already patched:", target)
else:
    print("The file looks different from what I expected, so I changed nothing:", target)
