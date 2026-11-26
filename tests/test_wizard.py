import sys, asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import app
from vault import Vault

d = Path(tempfile.mkdtemp())
DEF = d / "home" / "Cipher" / "vault.cipher"
app.DEFAULT_VAULT_PATH = DEF
saved = MagicMock(); app.save_vault_path = saved
run = lambda c: asyncio.run(c)
def walk(c):
    yield c
    for attr in ("controls", "actions"):
        for ch in (getattr(c, attr, None) or []): yield from walk(ch)
    for attr in ("content", "title", "leading", "trailing", "subtitle"):
        ch = getattr(c, attr, None)
        if isinstance(ch, ft.Control): yield from walk(ch)
texts = lambda root: [c.value for c in walk(root) if isinstance(c, ft.Text)]
btn = lambda root, label: next(c for c in walk(root) if getattr(c, "content", None) == label and hasattr(c, "on_click"))
dlg = lambda page: page.show_dialog.call_args[0][0]
def new_app(saved_path=None):
    app.saved_vault_path = lambda: saved_path
    page = MagicMock(); page.pop_dialog.return_value = None
    a = app.App(page); a.clip.set = AsyncMock(); return a, page
pick = lambda a, p: setattr(a.picker, "pick_files", AsyncMock(return_value=[SimpleNamespace(path=str(p))]))

# ---- welcome
a, page = new_app()
labels = {getattr(c, "content", None) for c in walk(a.root.content)}
assert {"I have a Cipher vault", "Import from another app (CSV)", "Start new"} <= labels
assert not any(isinstance(c, ft.TextField) for c in walk(a.root.content))
print("welcome OK")

# ---- start new
btn(a.root.content, "Start new").on_click(None)
assert str(DEF.parent) in texts(a.root.content) and "Cipher will create your vault in this folder." in texts(a.root.content)
btn(a.root.content, "Continue").on_click(None)
assert a.vault_path == DEF and a.pw2.visible and a.vault_panel.visible is False and not saved.called
assert any("Choose a master password for your new vault" in (x or "") for x in texts(a.root.content))
a.pw.value = a.pw2.value = "short"; run(a.submit_lock(None)); assert a.vault is None and not DEF.exists()
a.pw.value = a.pw2.value = "Brand-new-password-1!"; run(a.submit_lock(None))
assert DEF.exists() and a.vault is not None and saved.call_args[0][0] == DEF
print("start new OK")

# ---- change place, back, name already taken
a, page = new_app(); btn(a.root.content, "Start new").on_click(None)
a.picker.get_directory_path = AsyncMock(return_value=str(d / "custom"))
run(a.change_dest(None)); assert str(d / "custom") in texts(a.root.content)
a.picker.get_directory_path = AsyncMock(return_value=None); run(a.change_dest(None)); assert str(d / "custom") in texts(a.root.content)
btn(a.root.content, "Back").on_click(None); assert "Welcome. What do you have?" in texts(a.root.content)
btn(a.root.content, "Start new").on_click(None); btn(a.root.content, "Continue").on_click(None)   # DEF exists now
assert a.vault_path is None and "That folder already has a vault" in texts(dlg(page))
print("location step OK")

# ---- existing vault: copy, verify, offer to delete the old one
DEF.unlink()
srcdir = d / "usb"; src = srcdir / "vault.enc"
sv = Vault.create(src, "Src-password-12345!"); sv.add("Gmail", "g.com", "me", "pw1")
Vault.open(src, "Src-password-12345!").add("Bank", "", "me", "pw2")      # leaves a .bak next to it
assert len(list(srcdir.glob("vault.enc.*.bak"))) == 1
orig = src.read_bytes()
# default: use it where it is (no copy), also works for an old .enc vault
a, page = new_app(); pick(a, src); run(a.wizard_existing(None))
assert str(srcdir) in texts(a.root.content) and any("right where it is" in (x or "") for x in texts(a.root.content))
assert not any("Copying from:" in (x or "") for x in texts(a.root.content))
btn(a.root.content, "Continue").on_click(None)
assert a.vault_path == src and a.pending_old is None and not DEF.exists() and a.lock_btn.content == "Unlock"
a.pw.value = "Src-password-12345!"; run(a.submit_lock(None)); assert len(a.vault) == 2 and src.read_bytes() == orig
print("use where it is (default) OK")
# change the folder -> it becomes a copy
a, page = new_app(); pick(a, src); run(a.wizard_existing(None))
a.picker.get_directory_path = AsyncMock(return_value=str(DEF.parent)); run(a.change_dest(None))
assert any("Copying from:" in (x or "") for x in texts(a.root.content)) and any("copy your vault into this folder" in (x or "") for x in texts(a.root.content))
btn(a.root.content, "Copy and continue").on_click(None)
assert DEF.read_bytes() == orig and src.read_bytes() == orig and DEF.suffix == ".cipher" and src.suffix == ".enc"
print("own extension OK")
assert a.vault_path == DEF and a.pending_old == src and a.lock_btn.content == "Unlock" and not a.pw2.visible
assert "Vault copied. Enter its master password." in texts(a.root.content)
a.pw.value = "wrong-password-xx"; run(a.submit_lock(None))
assert a.vault is None and a.pending_old == src and src.exists()
a.pw.value = "Src-password-12345!"; run(a.submit_lock(None)); assert len(a.vault) == 2
d1 = dlg(page); assert "Delete the old vault file?" in texts(d1) and any("1 backup file" in (x or "") for x in texts(d1))
btn(d1, "Keep it").on_click(None); assert src.exists() and a.pending_old is None
print("copy + keep OK")

# an identical copy already there (e.g. an interrupted earlier attempt) is reused; then delete the old one
a, page = new_app(); pick(a, src); run(a.wizard_existing(None))
a.picker.get_directory_path = AsyncMock(return_value=str(DEF.parent)); run(a.change_dest(None)); btn(a.root.content, "Copy and continue").on_click(None)
assert a.vault_path == DEF and a.pending_old == src
a.pw.value = "Src-password-12345!"; run(a.submit_lock(None))
btn(dlg(page), "Delete it").on_click(None)
assert not src.exists() and not list(srcdir.glob("*.bak")) and DEF.exists()
print("reuse + delete old OK")

# a different file already at the destination is never overwritten
src2 = d / "usb2" / "vault.enc"; Vault.create(src2, "Src2-password-123!")
before = DEF.read_bytes()
a, page = new_app(); pick(a, src2); run(a.wizard_existing(None))
a.picker.get_directory_path = AsyncMock(return_value=str(DEF.parent)); run(a.change_dest(None)); btn(a.root.content, "Copy and continue").on_click(None)
assert a.vault_path is None and "Couldn't copy the vault" in texts(dlg(page)) and DEF.read_bytes() == before
# change back to its own folder -> use it where it is
a.picker.get_directory_path = AsyncMock(return_value=str(src2.parent)); run(a.change_dest(None))
btn(a.root.content, "Continue").on_click(None)
assert a.vault_path == src2 and a.pending_old is None and DEF.read_bytes() == before and a.lock_btn.content == "Unlock"
print("no overwrite / use in place OK")

# vault already at the default place -> straight to the lock screen
a, page = new_app(); pick(a, DEF); run(a.wizard_existing(None))
assert a.vault_path == DEF and "Where should Cipher keep your vault?" not in texts(a.root.content) and a.lock_btn.content == "Unlock"
# not a vault / cancelled
junk = d / "notes.txt"; junk.write_text("hello")
a, page = new_app(); pick(a, junk); run(a.wizard_existing(None))
assert a.vault_path is None and "Welcome. What do you have?" in texts(a.root.content) and "Not a Cipher vault" in texts(dlg(page))
a.picker.pick_files = AsyncMock(return_value=[]); run(a.wizard_existing(None)); assert a.vault_path is None
print("existing-vault edge cases OK")

# ---- CSV from another app
CSVDEF = d / "home2" / "Cipher" / "vault.cipher"; app.DEFAULT_VAULT_PATH = CSVDEF
csvp = d / "export.csv"
csvp.write_text('name,url,login,password\nGmail,mail.google.com,me@x.com,pw1\nGitHub,github.com,me,pw2\nBank,bank.com,me,pw3\nNoPass,x.com,u,\n')
a, page = new_app(); pick(a, csvp); run(a.wizard_csv(None)); p = dlg(page)
assert "Import these logins?" in texts(p) and "3 logins found" in texts(p)
lv = next(c for c in walk(p) if isinstance(c, ft.ListView))
assert [x.value for x in lv.controls[0].controls] == ["Gmail", "mail.google.com", "me@x.com - " + app.MASK]   # Title / Website / username - password
assert not any("pw1" in (x or "") for x in texts(p)) and any("1 row skipped" in (x or "") for x in texts(p))
show = btn(p, "Show passwords"); show.on_click(None)
assert [x.value for x in lv.controls[0].controls][2] == "me@x.com - pw1" and "me - pw2" in texts(p) and show.content == "Hide passwords"
show.on_click(None); assert [x.value for x in lv.controls[0].controls][2] == "me@x.com - " + app.MASK and show.content == "Show passwords"
btn(p, "Cancel").on_click(None); assert a.pending_import is None and "Welcome. What do you have?" in texts(a.root.content)
run(a.wizard_csv(None)); btn(dlg(page), "Continue").on_click(None)
assert a.pending_import is not None and str(CSVDEF.parent) in texts(a.root.content)
btn(a.root.content, "Back").on_click(None); assert a.pending_import is None            # starting over clears it
run(a.wizard_csv(None)); btn(dlg(page), "Continue").on_click(None); btn(a.root.content, "Continue").on_click(None)
assert a.vault_path == CSVDEF and a.pw2.visible and any("3 logins will be imported" in (x or "") for x in texts(a.root.content))
a.pw.value = a.pw2.value = "short"; run(a.submit_lock(None)); assert a.vault is None and a.pending_import is not None
a.pw.value = a.pw2.value = "Csv-flow-password-1!"; run(a.submit_lock(None))
assert len(a.vault) == 3 and a.pending_import is None and Vault.open(CSVDEF, "Csv-flow-password-1!").search("github")
res = dlg(page); assert any("Added 3 logins" in (x or "") for x in texts(res))
btn(res, "Delete the CSV file").on_click(None); assert not csvp.exists()
print("csv flow OK")
csvp.write_text("foo,bar\n1,2\n"); a, page = new_app(); pick(a, csvp); run(a.wizard_csv(None))
assert "No password column found" in texts(dlg(page)) and a.pending_import is None and "Welcome. What do you have?" in texts(a.root.content)
print("csv edge cases OK")

# ---- changing vault from the lock-screen panel cancels pending setup actions
a, page = new_app(CSVDEF); a.pending_old = Path("x"); a.pending_import = (Path("y"), [])
a.switch_vault(CSVDEF); assert a.pending_old is None and a.pending_import is None
print("switch clears pending OK")

import config
assert config.VAULT_EXTENSION == ".cipher" and config.DEFAULT_VAULT_PATH.name == "vault.cipher"
print("config extension OK")
