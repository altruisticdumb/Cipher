import sys, os, asyncio, tempfile, time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, AsyncMock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import app, config, sync
from sync import decide, sync_once, Unreachable, SyncError, file_hash
from vault import Vault, list_backups

d = Path(tempfile.mkdtemp()); os.environ["APPDATA"] = str(d / "appdata"); PW = "Correct-horse-battery-1!"
run = asyncio.run
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
app.save_vault_path = MagicMock()

# ---- the decision table
assert decide("a", None, None) == "push" and decide("a", "a", None) == "synced" and decide("a", "b", None) == "conflict"
assert decide("b", "a", "a") == "push" and decide("a", "b", "a") == "conflict" and decide("c", "b", "a") == "conflict"
print("decide OK")

# ---- sync_once on real folders
loc = d / "pc" / "vault.cipher"; loc.parent.mkdir(); loc.write_bytes(b"v1")
nas = d / "nas"; nas.mkdir(); copy = nas / "vault.cipher"
st, base = sync_once(loc, nas); assert st == "synced" and copy.read_bytes() == b"v1" and not list(nas.glob("*.uploading"))
assert sync_once(loc, nas, base) == ("synced", base)
loc.write_bytes(b"v2"); st, h2 = sync_once(loc, nas, base); assert copy.read_bytes() == b"v2" and h2 != base
copy.write_bytes(b"other"); assert sync_once(loc, nas, h2) == ("conflict", h2) and copy.read_bytes() == b"other"      # never overwrites
nas2 = d / "nas2"; nas2.mkdir(); (nas2 / "vault.cipher").write_bytes(b"zzz")
assert sync_once(loc, nas2)[0] == "conflict" and (nas2 / "vault.cipher").read_bytes() == b"zzz"                        # first sync, different file there
assert sync_once(loc, nas2, None, force=True)[0] == "synced" and (nas2 / "vault.cipher").read_bytes() == b"v2"
try: sync_once(loc, d / "missing"); raise SystemExit("no error")
except Unreachable: pass
try: sync_once(d / "no-local", nas); raise SystemExit("no error")
except Unreachable: raise SystemExit("wrong error")
except SyncError: pass
copy.write_bytes(b"keepme"); loc.write_bytes(b"v3")
with mock.patch.object(sync.os, "replace", side_effect=OSError("share dropped")):      # connection drops mid-upload
    try: sync_once(loc, nas, file_hash(copy)); raise SystemExit("no error")
    except Unreachable: pass
assert copy.read_bytes() == b"keepme" and not list(nas.glob("*.uploading"))
real = sync.file_hash
with mock.patch.object(sync, "file_hash", lambda p: "bad" if str(p).endswith(".uploading") else real(p)):   # a bad write is caught
    try: sync_once(loc, nas, real(copy)); raise SystemExit("no error")
    except SyncError: pass
assert copy.read_bytes() == b"keepme" and not list(nas.glob("*.uploading"))
print("sync_once OK")

# ---- settings
v1, v2 = d / "a" / "v.cipher", d / "b" / "v.cipher"
assert config.get_sync_settings(v1) == {}
config.set_sync_settings(v1, folder="Z:/x", last_hash="h", last_synced="2026-11-23T10:00:00"); config.set_sync_settings(v1, last_hash="h2")
assert config.get_sync_settings(v1) == {"folder": "Z:/x", "last_hash": "h2", "last_synced": "2026-11-23T10:00:00"} and config.get_sync_settings(v2) == {}
config.save_vault_path(v1); assert config.saved_vault_path() == v1 and config.get_sync_settings(v1)["folder"] == "Z:/x"   # not clobbered
config.clear_sync_settings(v1); assert config.get_sync_settings(v1) == {} and config.saved_vault_path() == v1
hv = Vault.create(d / "hook.cipher", PW); calls = []; hv.on_save = lambda: calls.append(1); hv.add("A", "", "u", "p"); assert calls == [1]
hv.on_save = lambda: 1 / 0; hv.add("B", "", "u", "p"); assert len(Vault.open(d / "hook.cipher", PW)) == 2
print("settings + save hook OK")

# ---- the app
vp = d / "pc2" / "vault.cipher"; Vault.create(vp, PW); nas3 = d / "nas3"; nas3.mkdir()
a, page = new_app(vp); assert a.sync_status == "off" and not a.sync_enabled()
a.pw.value = PW; run(a.submit_lock(None))
assert a.sync_bar.visible is False and a.vault.on_save == a.on_vault_saved
menu = next(c for c in a.header.content.controls if isinstance(c, ft.PopupMenuButton))
next(i for i in menu.items if i.content == "Sync to cloud").on_click(None)
assert "Sync to cloud" in texts(dlg(page)) and btn(dlg(page), "Choose folder")
a.picker.get_directory_path = AsyncMock(return_value=str(vp.parent)); run(a.choose_sync_folder(None))
assert "That's the vault's own folder" in texts(dlg(page)) and not a.sync_enabled()
a.picker.get_directory_path = AsyncMock(return_value=str(nas3)); run(a.choose_sync_folder(None))
assert a.sync_status == "synced" and (nas3 / "vault.cipher").read_bytes() == vp.read_bytes() and a.sync_bar.visible
assert config.get_sync_settings(vp)["folder"] == str(nas3) and any(x.startswith("Synced") for x in texts(a.sync_bar))
print("turn on OK")

a.vault.add("Gmail", "g.com", "me", "pw")                    # every save is synced
assert a.sync_dirty and a.sync_status == "pending" and a.sync_due == 0.0 and "Saved on this PC. Syncing..." in [x for x in (texts(a.sync_bar) if (a.render_sync_bar() or True) else [])]
run(a.sync_tick()); assert a.sync_status == "synced" and (nas3 / "vault.cipher").read_bytes() == vp.read_bytes() and not a.sync_dirty
with mock.patch.object(app, "sync_once", side_effect=AssertionError("synced again too soon")): run(a.sync_tick())
print("auto sync OK")

config.set_sync_settings(vp, folder=str(d / "gone"))          # the NAS goes away
a.vault.add("Bank", "b.com", "me", "pw2"); run(a.sync_tick())
assert a.sync_status == "unreachable" and a.sync_dirty and a.sync_due > time.monotonic()
assert any("Can't reach the sync folder" in (x or "") for x in texts(a.sync_bar))
with mock.patch.object(app, "sync_once", side_effect=AssertionError("retried too soon")): run(a.sync_tick())
config.set_sync_settings(vp, folder=str(nas3)); a.sync_due = 0; run(a.sync_tick())      # and comes back
assert a.sync_status == "synced" and (nas3 / "vault.cipher").read_bytes() == vp.read_bytes()
print("unreachable + recovery OK")

(nas3 / "vault.cipher").write_bytes(b"changed elsewhere"); a.vault.add("Z", "", "u", "p"); a.sync_due = 0; run(a.sync_tick())
assert a.sync_status == "conflict" and (nas3 / "vault.cipher").read_bytes() == b"changed elsewhere"
assert any("different version" in (x or "") for x in texts(a.sync_bar))
with mock.patch.object(app, "sync_once", side_effect=AssertionError("kept syncing during a conflict")): run(a.sync_tick())
btn(a.sync_bar, "Resolve").on_click(None); cd = dlg(page)
assert "Two versions of your vault" in texts(cd) and any("last synced" in (x or "") for x in texts(cd))
run(btn(cd, "Keep this PC's version").on_click(None))
assert a.sync_status == "synced" and (nas3 / "vault.cipher").read_bytes() == vp.read_bytes()
assert any(b["path"].read_bytes() == b"changed elsewhere" for b in list_backups(nas3 / "vault.cipher"))   # the folder's version was kept
print("conflict: keep this PC OK")

alt = d / "alt" / "vault.cipher"; Vault.create(alt, PW).add("FromElsewhere", "", "u", "p")
(nas3 / "vault.cipher").write_bytes(alt.read_bytes()); a.vault.add("Local2", "", "u", "p"); a.sync_due = 0; run(a.sync_tick())
assert a.sync_status == "conflict"; old_local = vp.read_bytes()
a.show_conflict(); run(btn(dlg(page), "Use the sync folder's version").on_click(None))
assert vp.read_bytes() == alt.read_bytes() and a.vault is None
assert any("Now using the version from the sync folder" in (x or "") for x in texts(a.root.content))
assert any(b["path"].read_bytes() == old_local for b in list_backups(vp)) and config.get_sync_settings(vp)["last_hash"] == file_hash(vp)
a.pw.value = PW; run(a.submit_lock(None)); run(a.sync_tick())
assert a.sync_status == "synced" and len(a.vault) == 1
print("conflict: use the folder's version OK")

a.vault.add("LastMinute", "", "u", "p"); a.lock("x"); run(a.sync_tick())                 # locking right after a change still syncs it
assert a.vault is None and (nas3 / "vault.cipher").read_bytes() == vp.read_bytes()
print("sync while locked OK")

# first sync into a folder that already holds a different vault -> conflict dialog, nothing overwritten
nas4 = d / "nas4"; nas4.mkdir(); (nas4 / "vault.cipher").write_bytes(alt.read_bytes())
vq = d / "pc3" / "vault.cipher"; Vault.create(vq, PW)
b, page2 = new_app(vq); b.pw.value = PW; run(b.submit_lock(None))
b.picker.get_directory_path = AsyncMock(return_value=str(nas4)); run(b.choose_sync_folder(None))
assert b.sync_status == "conflict" and any("already contains a different vault" in (x or "") for x in texts(dlg(page2)))
assert (nas4 / "vault.cipher").read_bytes() == alt.read_bytes()
print("first-sync conflict OK")

# settings dialog, turn off, other vaults start fresh
a.pw.value = PW; run(a.submit_lock(None)); a.show_sync(); sd = dlg(page)
assert f"Folder: {nas3}" in texts(sd) and "Status: Up to date" in texts(sd) and any(x.startswith("Last synced:") for x in texts(sd))
btn(sd, "Turn off").on_click(None)
assert not a.sync_enabled() and a.sync_status == "off" and a.sync_bar.visible is False and (nas3 / "vault.cipher").exists()
vr = d / "pc4" / "vault.cipher"; Vault.create(vr, PW)                  # a vault that never had sync
a.lock("x"); a.switch_vault(vr); a.pw.value = PW; run(a.submit_lock(None))
assert a.sync_for == str(vr) and a.sync_status == "off" and a.sync_bar.visible is False
print("settings dialog + turn off OK")
