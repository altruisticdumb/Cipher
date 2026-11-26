import sys, asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace
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
texts = lambda root: [c.value for c in walk(root) if isinstance(c, ft.Text)]

a.pw.value = a.pw2.value = "Master-pass-123!"; run(a.submit_lock(None))
# --- import: happy path
csvp = d / "tk.csv"
csvp.write_text('name,url,login,password\nGmail,mail.google.com,dan@x.com,pw1\nGitHub,github.com,danny,"p,w""2"\nBank,bank.com,me,pw3\nGitLab,gitlab.com,me,pw4\nNoPass,x.com,u,\n', encoding="utf-8")
a.picker.pick_files = AsyncMock(return_value=[SimpleNamespace(path=str(csvp))])
run(a.start_import(None)); d1 = dlg()
assert "4 logins found" in texts(d1) and any("1 row skipped" in t for t in texts(d1)), texts(d1)
btn(d1, "Import").on_click(None)
assert len(a.vault) == 4 and a.count.value == "4 logins"
res = dlg(); assert any("Added 4 logins" in t for t in texts(res))
btn(res, "Delete the CSV file").on_click(None); assert not csvp.exists()
print("import OK")
# --- import: duplicates on second run, no password column, unreadable file, cancel
csvp.write_text('name,url,login,password\nGmail,mail.google.com,dan@x.com,pw1\nNew,n.com,u,pw9\n')
run(a.start_import(None)); btn(dlg(), "Import").on_click(None)
assert len(a.vault) == 5 and any("Skipped 1 login that already existed" in t for t in texts(dlg()))
csvp.write_text("foo,bar\n1,2\n"); run(a.start_import(None))
assert any("foo, bar" in t for t in texts(dlg()))
csvp.write_bytes(b"\xff\xfe\x00\x80\x81"); run(a.start_import(None))
assert any("read" in (t or "") for t in texts(dlg()))
a.picker.pick_files = AsyncMock(return_value=[]); n = page.show_dialog.call_count; run(a.start_import(None)); assert page.show_dialog.call_count == n
print("import edge cases OK")
# --- select several
a.enter_select()
assert a.select_mode and isinstance(a.list.controls[0].content.leading, ft.Checkbox) and a.del_btn.disabled
ids = {e["title"]: e["id"] for e in a.vault.list_all()}
a.toggle(ids["Bank"]); assert a.sel_text.value == "1 selected" and not a.del_btn.disabled
a.toggle(ids["Bank"]); assert a.sel_text.value == "0 selected" and a.del_btn.disabled
box = a.checks[ids["Gmail"]]; box.value = True; box.on_change(None); assert a.sel_text.value == "1 selected"
a.select_all(); assert a.sel_text.value == "5 selected" and all(c.value for c in a.checks.values())
# search narrows what Delete acts on
a.search.value = "git"; a.on_search(); assert a.sel_text.value == "2 selected"   # GitHub + GitLab
a.confirm_bulk_delete(); d2 = dlg(); assert "Delete 2 logins?" in texts(d2)
btn(d2, "Delete").on_click(None)
left = sorted(e["title"] for e in a.vault.list_all()); assert left == ["Bank", "Gmail", "New"], left
assert not a.select_mode and isinstance(a.header.content.controls[1], ft.Text)
assert len(Vault.open(a.vault_path, "Master-pass-123!")) == 3
print("multi-select delete OK")
# cancel select, lock resets select mode
a.enter_select(); a.toggle(ids["Bank"]); a.exit_select(); assert not a.select_mode and not a.selected and len(a.vault) == 3
a.enter_select(); a.lock("Locked"); assert not a.select_mode and a.selected == set()
print("cancel/lock OK")
