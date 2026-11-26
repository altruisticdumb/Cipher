import sys, asyncio, tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import app
from vault import Vault

d = Path(tempfile.mkdtemp()); app.saved_vault_path = lambda: d / "v.enc"; app.save_vault_path = MagicMock()
page = MagicMock(); page.pop_dialog.return_value = None
a = app.App(page); a.clip.set = AsyncMock()
run = lambda c: asyncio.run(c)

def walk(c):
    yield c
    for attr in ("controls", "actions"):
        for ch in (getattr(c, attr, None) or []): yield from walk(ch)
    for attr in ("content", "title", "leading", "trailing", "subtitle"):
        ch = getattr(c, attr, None)
        if isinstance(ch, ft.Control): yield from walk(ch)
dlg = lambda: page.show_dialog.call_args[0][0]
btn = lambda root, label: next(c for c in walk(root) if getattr(c, "content", None) == label and hasattr(c, "on_click"))
field = lambda root, label: next(c for c in walk(root) if isinstance(c, ft.TextField) and c.label == label)

# create screen
a.pw.value = a.pw2.value = "short"; run(a.submit_lock(None))
assert a.vault is None and "14" in a.lock_msg.value
a.pw.value, a.pw2.value = "Master-pass-123!", "different"; run(a.submit_lock(None))
assert "match" in a.lock_msg.value
a.pw.value = a.pw2.value = "Master-pass-123!"; run(a.submit_lock(None))
assert a.vault is not None and isinstance(a.root.content, ft.Column)
print("create/unlock OK")

# add
a.show_edit(None); d1 = dlg()
field(d1, "Title").value = "GitHub"; field(d1, "Website").value = "github.com"
field(d1, "Username / email").value = "danny"
btn(d1, "Generate").on_click(None); pwf = field(d1, "Password")
assert len(pwf.value) == 20 and pwf.password is False
btn(d1, "Save").on_click(None)
assert len(a.vault) == 1 and len(a.list.controls) == 1 and a.count.value == "1 logins"
a.show_edit(None); d2 = dlg()
field(d2, "Title").value = "github"; field(d2, "Username / email").value = "DANNY"; field(d2, "Password").value = "x"
btn(d2, "Save").on_click(None)
assert any(isinstance(c, ft.Text) and "already exists" in (c.value or "") for c in walk(d2)) and len(a.vault) == 1
a.show_edit(None); d3 = dlg(); field(d3, "Title").value = "My Bank"; field(d3, "Username / email").value = "me"; field(d3, "Password").value = "p w"
btn(d3, "Save").on_click(None); assert len(a.vault) == 2
print("add/generate/duplicate OK")

# search
a.search.value = "git"; a.on_search(); assert len(a.list.controls) == 1 and a.count.value == "1 of 2 logins"
a.search.value = "zzz"; a.on_search(); assert "No logins" in a.list.controls[0].content.value
a.search.value = ""; a.on_search(); assert len(a.list.controls) == 2
print("search OK")

# copy + clipboard clearing
app.CLEAR_CLIPBOARD_AFTER = 0.01
run(a.copy("secret", "Password", True))
assert [c.args[0] for c in a.clip.set.await_args_list] == ["secret", ""]
a.clip.set.reset_mock(); run(a.copy("user", "Username", False))
assert [c.args[0] for c in a.clip.set.await_args_list] == ["user"]
a.list.controls[0].content.trailing.on_click(None); assert page.run_task.call_args[0][0] == a.copy
print("clipboard OK")

# details, reveal, edit
entry = a.vault.search("git")[0]; a.show_details(entry); d4 = dlg()
txt = next(c for c in walk(d4) if isinstance(c, ft.Text) and c.value == app.MASK)
eye = next(c for c in walk(d4) if isinstance(c, ft.IconButton) and c.icon == ft.Icons.VISIBILITY)
eye.on_click(None); assert txt.value == entry["password"]; eye.on_click(None); assert txt.value == app.MASK
btn(d4, "Edit").on_click(None); d5 = dlg()
assert field(d5, "Title").value == "GitHub"
field(d5, "Username / email").value = "danny2"; btn(d5, "Save").on_click(None)
assert a.vault.search("git")[0]["username"] == "danny2"
print("details/edit OK")

# delete
entry = a.vault.search("git")[0]; a.show_details(entry); btn(dlg(), "Delete").on_click(None)
btn(dlg(), "Delete").on_click(None); assert len(a.vault) == 1
print("delete OK")

# lock / wrong password / autolock
a.clip_token = "abc"; a.lock("Locked")
assert a.vault is None and a.list.controls == [] and a.lock_msg.value == "Locked"
page.run_task.assert_any_call(a.clip.set, "")
a.pw.value = "wrongwrongwrong"; run(a.submit_lock(None)); assert a.vault is None and "Wrong" in a.lock_msg.value
a.pw.value = "Master-pass-123!"; run(a.submit_lock(None)); assert len(a.vault) == 1
a.last_activity -= 1000; a.check_autolock(); assert a.vault is None and "inactivity" in a.lock_msg.value
assert Vault.open(a.vault_path, "Master-pass-123!")
print("lock/autolock OK")
