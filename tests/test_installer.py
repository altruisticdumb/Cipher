import sys, os, tempfile, re
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import app, config
from config import in_install_folder
from vault import Vault

d = Path(tempfile.mkdtemp()); os.environ["APPDATA"] = str(d / "appdata"); PW = "Correct-horse-battery-1!"
root = Path(__file__).resolve().parent.parent

# ---- Windows passes the clicked file as the first argument
assert app.startup_vault_arg(["Cipher.exe", r"C:\x\my vault.cipher"]) == r"C:\x\my vault.cipher"
assert app.startup_vault_arg([r"C:\X\V.CIPHER"]) == r"C:\X\V.CIPHER" and app.startup_vault_arg([]) is None
assert app.startup_vault_arg(["--something", "notes.txt"]) is None

def make(open_path, saved=None):
    app.saved_vault_path = lambda: saved; app.save_vault_path = MagicMock()
    page = MagicMock(); page.pop_dialog.return_value = None
    return app.App(page, open_path=open_path), page

v1 = d / "one.cipher"; Vault.create(v1, PW); v2 = d / "two.cipher"; Vault.create(v2, PW)
a, page = make(str(v2), saved=v1)
assert a.vault_path == v2 and app.save_vault_path.call_args[0][0] == v2           # the clicked vault wins and is remembered
assert not page.show_dialog.call_args_list or "Couldn't open" not in str(page.show_dialog.call_args)
a, page = make(None, saved=v1); assert a.vault_path == v1                          # no argument: as before
a, page = make(None, saved=None); assert a.vault_path is None                      # first run: welcome screen
bad = d / "notes.cipher"; bad.write_bytes(b"just some text")
a, page = make(str(bad), saved=v1)
assert a.vault_path == v1 and "Couldn't open notes.cipher" in str(page.show_dialog.call_args[0][0].title.value)
a, page = make(str(d / "missing.cipher"), saved=v1)
assert a.vault_path == v1 and page.show_dialog.called                              # a missing file is reported, not crashed on
a, page = make(str(v2), saved=None); assert a.vault_path == v2                     # works on a first run too
print("open a .cipher file OK")

# ---- vaults never live in the program folder
inst = d / "Programs" / "Cipher"
assert in_install_folder(inst / "vault.cipher", inst) and in_install_folder(inst / "sub" / "v.cipher", inst)
assert not in_install_folder(d / "Programs" / "CipherOther" / "v.cipher", inst) and not in_install_folder(d / "Cipher" / "v.cipher", inst)
assert not in_install_folder(inst / ".." / "elsewhere.cipher", inst) and not in_install_folder(d / "x.cipher")   # from source: no install folder
sys.frozen = True; sys.executable = str(inst / "Cipher.exe")
try:
    assert in_install_folder(inst / "vault.cipher")
    a, page = make(None, saved=v1); a.setup = {"kind": "new", "folder": inst}; a.location_continue()
    assert "Not inside the Cipher program folder" in page.show_dialog.call_args[0][0].title.value and a.vault_path == v1
finally:
    del sys.frozen
print("install folder guard OK")

# ---- the installer script keeps its promises (it can't be compiled here, so check what it says)
iss = (root / "installer" / "cipher.iss").read_text(encoding="utf-8")
assert "PrivilegesRequired=admin" in iss and "PrivilegesRequiredOverridesAllowed=dialog" in iss and "ChangesAssociations=yes" in iss   # all users or just me
assert re.search(r"Subkey: \"Software\\Classes\\\.cipher\"", iss) and r'shell\open\command' in iss and '"%1"' in iss
reg = [l for l in iss.splitlines() if l.startswith("Root:")]
assert reg and all(l.startswith("Root: HKA;") for l in reg)                              # follows the install mode, never hard-wired
assert "HKCR" not in iss and "{autopf}\\Cipher" in iss
assert "[UninstallDelete]" not in iss.replace("; No [UninstallDelete] section", "")       # never deletes anything extra
assert not re.search(r"^\s*(Source|DestDir)[^\n]*\.cipher\b", iss, re.M) and "Cipher\\vault" not in iss
assert config.VAULT_EXTENSION == ".cipher" and (root / "installer" / "cipher.ico").stat().st_size > 1000
bat = (root / "build_installer.bat").read_text(encoding="utf-8")
assert " -D " in bat and "dist\\Cipher\\Cipher.exe" in bat and "dist\\Cipher\\*" in iss and "recursesubdirs" in iss   # folder build, whole folder installed
assert "patch_flet_pack.py" in bat and "flet.exe\" pack app.py --name Cipher" in bat and "ISCC" in bat
print("installer script OK")

raw = (root / "build_installer.bat").read_bytes()
assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")                     # Windows line endings, or goto labels can fail
assert b"cmd /k" in raw and b"enabledelayedexpansion" in raw and b'!%%R!' in raw                   # stays open on errors; brackets in paths are safe
assert b"Inno Setup*" in raw and b"Inno Setup 6" not in raw.replace(b"6 or newer", b"") and b"%ProgramFiles(x86)%\\Inno" not in raw   # any Inno Setup version
assert raw.count(b"pause") >= 2 and b"usebackq" in raw                                  # window stays open; quotes inside for /f are safe
print("build script OK")

blocked = [l for l in raw.decode().split("\r\n") if l.startswith("    ") and l.strip().lower().startswith("echo")]
assert blocked and not [l for l in blocked if "(" in l or ")" in l]                        # a bracket in an echo inside ( ) ends the block early
print("batch echo lines OK")
