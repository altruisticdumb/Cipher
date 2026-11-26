import sys, asyncio, tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import app
from vault import (Vault, VaultError, password_strength, password_checks, list_backups,
                   restore_backup, KEEP_BACKUPS)

d = Path(tempfile.mkdtemp()); PW = "Correct-horse-battery-1!"
app.save_vault_path = MagicMock()
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
def new_app(path):
    app.saved_vault_path = lambda: path
    page = MagicMock(); page.pop_dialog.return_value = None
    a = app.App(page); a.clip.set = AsyncMock(); return a, page

# ---- strength rules
cases = {"": "weak", "short": "weak", "abcdefghijklmn": "weak", "aaaaaaaaaaaaaaaaaaaaaaaa": "weak", "Ab1!Ab1!": "weak",
         "Ab1!Ab1!Ab": "medium", "Abcdefghijklmn1": "medium", "Abcdefghijklm1!": "strong", "correct horse battery": "strong"}
for pw, level in cases.items(): assert password_strength(pw) == level, (pw, password_strength(pw), level)
assert [ok for _, ok in password_checks("Abcdefghijklmn1")] == [True, True, True, True, False]
print("strength rules OK")

# ---- the vault enforces them on creation and on changing the master password
try: Vault.create(d / "w.cipher", "Abcdefghijklmn1"); raise SystemExit("weak password accepted")
except VaultError as e: assert "symbol" in str(e) and "20" in str(e), e
v = Vault.create(d / "s.cipher", "Abcdefghijklm1!")
try: v.change_master_password("abcdefghijklmn"); raise SystemExit("weak change accepted")
except VaultError as e: assert "uppercase letter" in str(e)
v.change_master_password("A much longer phrase 2!"); Vault.open(d / "s.cipher", "A much longer phrase 2!")
print("vault enforces OK")

# ---- lock screen: live meter, Create only when strong
a, page = new_app(d / "missing" / "vault.cipher")
assert a.creating_vault and a.lock_btn.disabled is True and a.meter.control.visible is False and a.pw.on_change is not None
a.pw.value = "abc"; a.on_pw_change()
assert a.meter.label.value == "Weak" and a.meter.bar.color == ft.Colors.RED_400 and a.lock_btn.disabled is True and a.meter.control.visible
t = texts(a.meter.control); assert "\u2713 lowercase letter" in t and "\u2717 uppercase letter" in t and "\u2717 14+ characters" in t
a.pw.value = "Abcdefghijklmn1"; a.on_pw_change(); assert a.meter.label.value == "Medium" and a.lock_btn.disabled is True
a.pw.value = "Abcdefghijklm1!"; a.on_pw_change(); assert a.meter.label.value == "Strong" and a.lock_btn.disabled is False and a.meter.bar.value == 1.0
a.pw.value = "correct horse battery"; a.on_pw_change(); assert a.lock_btn.disabled is False and "A long phrase counts as strong" in texts(a.meter.control)
a.pw.value = ""; a.on_pw_change(); assert a.meter.control.visible is False and a.lock_btn.disabled is True
a.pw.value = "Abcdefghijklm1!"; a.pw2.value = "different"; run(a.submit_lock(None))
assert a.lock_btn.disabled is True and a.meter.control.visible is False and "match" in a.lock_msg.value      # must start again
a.pw.value = a.pw2.value = "weak-but-long-enou"; run(a.submit_lock(None)); assert a.vault is None and "uppercase letter" in a.lock_msg.value
a.pw.value = a.pw2.value = "Abcdefghijklm1!"; run(a.submit_lock(None)); assert a.vault is not None
a.lock("x"); assert not a.creating_vault and a.pw.on_change is None and a.lock_btn.disabled is False and a.meter.control not in list(walk(a.root.content))
print("lock screen meter OK")

# ---- add / edit dialog: meter for the login's password (no restriction)
a.pw.value = "Abcdefghijklm1!"; run(a.submit_lock(None))
a.show_edit(None); dd = dlg(page)
pwf = next(c for c in walk(dd) if isinstance(c, ft.TextField) and c.label == "Password")
assert not any(x in texts(dd) for x in ("Weak", "Medium", "Strong"))
pwf.value = "abc"; pwf.on_change(None); assert "Weak" in texts(dd)
btn(dd, "Generate").on_click(None); assert "Strong" in texts(dd)
for lbl, val in (("Title", "T"), ("Username / email", "u")): next(c for c in walk(dd) if isinstance(c, ft.TextField) and c.label == lbl).value = val
pwf.value = "abc"; btn(dd, "Save").on_click(None); assert len(a.vault) == 1       # a weak site password is allowed
e = a.vault.list_all()[0]; a.show_edit(e); assert "Weak" in texts(dlg(page))
print("login password meter OK")

# ---- backups (vault level)
p = d / "b.cipher"; Vault.create(p, PW)
for i in range(3): Vault.open(p, PW).add(f"S{i}", "", "u", "pw")
bl = list_backups(p); assert len(bl) == 3
assert [len(Vault.open(b["path"], PW)) for b in bl] == [2, 1, 0]                 # newest first, even within the same second
restore_backup(p, bl[1]["path"]); assert len(Vault.open(p, PW)) == 1
assert 3 in [len(Vault.open(b["path"], PW)) for b in list_backups(p)]            # what was replaced is kept: restore is undoable
for i in range(6): Vault.open(p, PW).add(f"X{i}", "", "u", "pw")
assert len([b for b in list_backups(p) if not b["reason"]]) == KEEP_BACKUPS      # ordinary ones are trimmed to the newest 5
oldest = list_backups(p)[-1]; restore_backup(p, oldest["path"]); assert oldest["path"].exists()      # the one restored from is never trimmed
junk = p.with_name(p.name + ".20260101-000000.bak"); junk.write_bytes(b"not a vault at all"); before = p.read_bytes()
try: restore_backup(p, junk); raise SystemExit("junk restored")
except VaultError: pass
assert p.read_bytes() == before
good = list_backups(p)[0]; goodbytes = good["path"].read_bytes()
p.write_bytes(b"damaged!!"); restore_backup(p, good["path"]); assert p.read_bytes() == goodbytes
assert any(b["path"].read_bytes() == b"damaged!!" for b in list_backups(p))     # even a damaged file is kept first
p.unlink(); restore_backup(p, good["path"]); assert p.exists() and Vault.open(p, PW)
print("backups OK")

# ---- backups (app): menu, list, restore, and from the lock screen
q = d / "g.cipher"; Vault.create(q, PW)
for i in range(3): Vault.open(q, PW).add(f"G{i}", "", "u", "pw")
a, page = new_app(q)
assert any(getattr(c, "content", None) == "Restore backup" for c in walk(a.root.content))
a.pw.value = PW; run(a.submit_lock(None))
menu = next(c for c in a.header.content.controls if isinstance(c, ft.PopupMenuButton))
assert [i.content for i in menu.items] == ["Vault health", "Import from CSV", "Select several", "Sync to cloud", "Backups and restore", "Change master password", "Show vault in folder"]
next(i for i in menu.items if i.content == "Backups and restore").on_click(None)
bd = dlg(page); assert "Backups" in texts(bd)
rows = [c for c in walk(bd) if isinstance(c, ft.ListTile)]; assert len(rows) == len(list_backups(q)) == 3
chosen = list_backups(q)[1]; chosen_bytes = chosen["path"].read_bytes()
[c for c in walk(bd) if isinstance(c, ft.TextButton) and c.content == "Restore"][1].on_click(None)
rd = dlg(page); assert "Restore this backup?" in texts(rd)
btn(rd, "Restore").on_click(None)
assert a.vault is None and "Backup restored" in " ".join(x or "" for x in texts(a.root.content)) and q.read_bytes() == chosen_bytes
a.pw.value = PW; run(a.submit_lock(None)); assert len(a.vault) == 1
print("restore from the app OK")
a.lock("x"); q.unlink(); a.show_lock()                    # vault file gone: restore from the lock screen
assert a.vault_panel.visible and any(getattr(c, "content", None) == "Restore backup" for c in walk(a.root.content))
btn(a.root.content, "Restore backup").on_click(None); [c for c in walk(dlg(page)) if isinstance(c, ft.TextButton) and c.content == "Restore"][0].on_click(None)
btn(dlg(page), "Restore").on_click(None)
assert q.exists() and not a.creating_vault and a.lock_btn.content == "Unlock"
a.pw.value = PW; run(a.submit_lock(None)); assert a.vault is not None
print("restore from lock screen OK")
r = d / "none.cipher"; Vault.create(r, PW); a, page = new_app(r)
assert not any(getattr(c, "content", None) == "Restore backup" for c in walk(a.root.content))
a.show_backups(); assert "No backups yet" in texts(dlg(page))
print("no backups OK")

# ---- change master password in the app
from vault import WrongPasswordError
q = d / "m.cipher"; mv = Vault.create(q, PW)
assert mv.check_password(PW) and not mv.check_password("Wrong-password-1!")
mv.add("A", "", "u", "pw1")
a, page = new_app(q); a.pw.value = PW; run(a.submit_lock(None))
menu = next(c for c in a.header.content.controls if isinstance(c, ft.PopupMenuButton))
assert "Change master password" in [i.content for i in menu.items]
next(i for i in menu.items if i.content == "Change master password").on_click(None)
dd = dlg(page); assert "Change master password" in texts(dd)
f = lambda label: next(c for c in walk(dd) if isinstance(c, ft.TextField) and c.label == label)
save = btn(dd, "Save"); assert save.disabled is True
f("New master password").value = "weak"; f("New master password").on_change(None); assert "Weak" in texts(dd) and save.disabled
f("New master password").value = "New-master-pass-2!"; f("New master password").on_change(None); assert "Strong" in texts(dd) and save.disabled is False
status = lambda: next(c for c in walk(dd) if isinstance(c, ft.Text) and c.size == 13).value
f("Current master password").value = PW; f("Repeat the new master password").value = "different"
run(save.on_click(None)); assert "don't match" in status()
f("Repeat the new master password").value = "New-master-pass-2!"; f("Current master password").value = "Wrong-password-1!"
run(save.on_click(None)); assert "wrong" in status() and save.disabled is False
Vault.open(q, PW)                                                              # still the old password
f("Current master password").value = "New-master-pass-2!"                      # same as the current one is refused too
f("New master password").value = f("Repeat the new master password").value = PW
f("Current master password").value = PW; run(save.on_click(None)); assert "already your master password" in status()
f("New master password").value = f("Repeat the new master password").value = "New-master-pass-2!"; f("New master password").on_change(None)
run(save.on_click(None))
assert a.vault is not None and "Master password changed" in texts(dlg(page))
try: Vault.open(q, PW); raise SystemExit("old password still works")
except WrongPasswordError: pass
assert len(Vault.open(q, "New-master-pass-2!")) == 1
a.vault.add("B", "", "u", "pw2"); assert len(Vault.open(q, "New-master-pass-2!")) == 2     # the open session keeps working with the new key
print("change master password OK")

# ---- show the vault in the file manager
import os
from unittest import mock
vf = d / "show.cipher"; Vault.create(vf, PW)
a, page = new_app(vf)
a.vault_panel.visible = True
shown = btn(a.root.content, "Show in folder")
with mock.patch.object(app, "open_in_file_manager") as om:
    shown.on_click(None); om.assert_called_once_with(vf)
    a.vault_path = d / "nowhere" / "x.cipher"; om.reset_mock(); a.open_vault_folder()
    assert not om.called and "Folder not found" in texts(dlg(page))
    a.vault_path = vf; om.side_effect = OSError("no file manager"); a.open_vault_folder()
    assert "Couldn't open the folder" in texts(dlg(page))
a.pw.value = PW; run(a.submit_lock(None))
menu = next(c for c in a.header.content.controls if isinstance(c, ft.PopupMenuButton))
with mock.patch.object(app, "open_in_file_manager") as om:
    next(i for i in menu.items if i.content == "Show vault in folder").on_click(None); om.assert_called_once_with(vf)
# the commands it builds on each system
with mock.patch.object(app.sys, "platform", "win32"), mock.patch.object(app.subprocess, "Popen") as po:
    app.open_in_file_manager(vf); assert po.call_args[0][0] == f'explorer /select,"{vf}"'
with mock.patch.object(app.sys, "platform", "win32"), mock.patch.object(app.subprocess, "Popen") as po, mock.patch.object(app.os, "startfile", create=True) as sf:
    app.open_in_file_manager(d / "gone.cipher"); sf.assert_called_once_with(d); assert not po.called
with mock.patch.object(app.sys, "platform", "darwin"), mock.patch.object(app.subprocess, "Popen") as po:
    app.open_in_file_manager(vf); assert po.call_args[0][0] == ["open", "-R", str(vf)]
with mock.patch.object(app.sys, "platform", "linux"), mock.patch.object(app.subprocess, "Popen") as po:
    app.open_in_file_manager(vf); assert po.call_args[0][0] == ["xdg-open", str(d)]
print("show in folder OK")
