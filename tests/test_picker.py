import sys, os, asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import config, app
from vault import Vault

d = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(d / "appdata")
run = lambda c: asyncio.run(c)
def walk(c):
    yield c
    for attr in ("controls", "actions"):
        for ch in (getattr(c, attr, None) or []): yield from walk(ch)
    for attr in ("content", "title", "leading", "trailing", "subtitle"):
        ch = getattr(c, attr, None)
        if isinstance(ch, ft.Control): yield from walk(ch)
texts = lambda root: [c.value for c in walk(root) if isinstance(c, ft.Text)]
icons = lambda root: [c.icon for c in walk(root) if isinstance(c, ft.Icon)]

# --- config: remember the vault path
assert config.saved_vault_path() is None and config.load_vault_path() == config.DEFAULT_VAULT_PATH
config.save_vault_path(d / "x" / "my.enc"); assert config.load_vault_path() == d / "x" / "my.enc"
config.settings_file().write_text("{not json"); assert config.saved_vault_path() is None and config.load_vault_path() == config.DEFAULT_VAULT_PATH
config.settings_file().write_text('["list"]'); assert config.load_vault_path() == config.DEFAULT_VAULT_PATH
os.remove(config.settings_file())
print("settings OK")

# --- lock screen branding + vault info
first = d / "first.enc"
page = MagicMock(); page.pop_dialog.return_value = None
app.saved_vault_path = lambda: first
saved = MagicMock(); app.save_vault_path = saved
a = app.App(page); a.clip.set = AsyncMock()
t = texts(a.root.content)
assert "Cipher" in t and "Locked. Local. Yours." in t, t
assert ft.Icons.SHIELD_OUTLINED in icons(a.root.content) and ft.Icons.LOCK_OUTLINE not in icons(a.root.content)
assert "first.enc" in t and any("New vault" == getattr(c, "content", None) for c in walk(a.root.content))
assert a.vault_panel.visible is True        # no vault found here: location shown so you notice
assert not any("Password Vault" == x for x in t)
print("lock screen OK")
# panel layout: browse button sits at the end of the path, copy is a labelled button
assert any("No vault found at the location below" in (x or "") for x in texts(a.root.content))
ibtns = [c for c in walk(a.vault_panel) if isinstance(c, ft.IconButton)]
assert [b.icon for b in ibtns] == [ft.Icons.FOLDER_OPEN] and ibtns[0].tooltip == "Choose another vault"
copy_btn = next(c for c in walk(a.vault_panel) if isinstance(c, ft.TextButton) and c.content == "Copy path")
copy_btn.on_click(None); assert page.run_task.call_args[0][0] == a.copy and page.run_task.call_args[0][1] == str(first)
print("panel layout OK")

# --- create the first vault, path gets remembered
a.pw.value = a.pw2.value = "Master-pass-12345!"; run(a.submit_lock(None))
assert a.vault is not None and first.exists() and saved.call_args[0][0] == first
assert "Cipher" in texts(a.header.content) and a.page is page
print("create + remember OK")
a.lock("Locked"); assert a.vault_panel.visible is False      # normal unlock screen: location hidden
a.toggle_vault_panel(); assert a.vault_panel.visible is True
a.toggle_vault_panel(); assert a.vault_panel.visible is False
print("location hidden/toggle OK")

# --- open another vault
other = d / "other.enc"; Vault.create(other, "Other-password-1234!").add("A", "", "u", "p")
a.lock("Locked")
a.picker.pick_files = AsyncMock(return_value=[SimpleNamespace(path=str(other))])
run(a.choose_vault(None))
assert a.vault_path == other and saved.call_args[0][0] == other and "other.enc" in texts(a.root.content) and a.vault_panel.visible is False
a.pw.value = "Other-password-1234!"; run(a.submit_lock(None)); assert len(a.vault) == 1
a.lock("Locked")
# not a vault -> alert, nothing changes
junk = d / "notes.txt"; junk.write_text("just some text")
a.picker.pick_files = AsyncMock(return_value=[SimpleNamespace(path=str(junk))])
run(a.choose_vault(None)); assert a.vault_path == other
assert any("doesn't look like a vault" in x for x in texts(page.show_dialog.call_args[0][0]))
for cancelled in ([], None):
    a.picker.pick_files = AsyncMock(return_value=cancelled); run(a.choose_vault(None)); assert a.vault_path == other
print("open another OK")
# switching while the location panel is open keeps it open
a.toggle_vault_panel(); assert a.vault_panel.visible
a.picker.pick_files = AsyncMock(return_value=[SimpleNamespace(path=str(first))])
run(a.choose_vault(None)); assert a.vault_path == first and a.vault_panel.visible is True
a.picker.pick_files = AsyncMock(return_value=[SimpleNamespace(path=str(other))])
run(a.choose_vault(None)); assert a.vault_path == other
print("panel stays open OK")

# --- new vault (pick a folder; the file name is automatic)
name = app.DEFAULT_VAULT_PATH.name
a.picker.get_directory_path = AsyncMock(return_value=str(d / "freshdir"))
run(a.new_vault(None))
fresh = d / "freshdir" / name
assert a.vault_path == fresh and a.pw2.visible and a.lock_btn.content == "Create vault"
a.pw.value = a.pw2.value = "Fresh-password-1234!"; run(a.submit_lock(None)); assert fresh.exists() and len(a.vault) == 0
a.lock("Locked")
(d / "junkdir").mkdir(); (d / "junkdir" / name).write_text("just some text")     # a non-vault file with that name: refuse
a.picker.get_directory_path = AsyncMock(return_value=str(d / "junkdir"))
run(a.new_vault(None)); assert a.vault_path == fresh and (d / "junkdir" / name).read_text() == "just some text"
Vault.create(d / "vdir" / name, "Vd-password-12345!")                              # an existing vault: open it, never overwrite
a.picker.get_directory_path = AsyncMock(return_value=str(d / "vdir"))
before = (d / "vdir" / name).read_bytes(); run(a.new_vault(None))
assert a.vault_path == d / "vdir" / name and (d / "vdir" / name).read_bytes() == before and not a.pw2.visible
a.picker.get_directory_path = AsyncMock(return_value=None); run(a.new_vault(None)); assert a.vault_path == d / "vdir" / name
print("new vault OK")

