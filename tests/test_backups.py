import sys, os, asyncio, tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import app, config, sync
from vault import (Vault, list_backups, prune_backups, delete_backups, restore_backup, snapshot_file,
                   KEEP_BACKUPS, KEEP_RISKY)

d = Path(tempfile.mkdtemp()); os.environ["APPDATA"] = str(d / "appdata"); PW = "Correct-horse-battery-1!"
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

def fake(vp, when, reason=None, data=b"PMV1x"):
    tag = f".{reason}" if reason else ""
    p = vp.with_name(f"{vp.name}.{when:%Y%m%d-%H%M%S}{tag}.bak"); p.write_bytes(data + str(when).encode()); return p

# ---- tiered pruning: 14 days daily, then weekly up to 70 days, then gone
vp = d / "t.cipher"; vp.write_bytes(b"PMV1main"); now = datetime(2026, 11, 24, 12, 0)
for days in range(0, 90):
    fake(vp, now - timedelta(days=days)); fake(vp, now - timedelta(days=days, hours=3))   # two per day
prune_backups(vp, now=now)
ages = [(now - b["when"]).days for b in list_backups(vp)]
assert all(a < 70 for a in ages) and sum(a < 14 for a in ages) >= 14 and len(ages) < 14 + 10 + KEEP_BACKUPS, ages
assert len({b["when"].date() for b in list_backups(vp) if (now - b["when"]).days < 14}) == 14      # one per day kept
old = [b for b in list_backups(vp) if (now - b["when"]).days >= 14]
assert len({b["when"].isocalendar()[:2] for b in old}) == len(old)                                  # one per week
print("tiered pruning OK")

# ---- risky-action copies are kept separately (newest 5) and don't push ordinary ones out
p = d / "r.cipher"; v = Vault.create(p, PW)
for i in range(3): Vault.open(p, PW).add(f"S{i}", "", "u", "pw")
ordinary = [b["path"] for b in list_backups(p)]; assert len(ordinary) == 3
v = Vault.open(p, PW)
v.add_many([("I1", "", "u", "p1"), ("I2", "", "u", "p2")])
assert [b["reason"] for b in list_backups(p) if b["reason"]] == ["import"]
v.delete_many([e["id"] for e in v.list_all()[:2]])
v.change_master_password("A-different-master-pass-9!")
reasons = sorted(b["reason"] for b in list_backups(p) if b["reason"]); assert reasons == ["delete", "import", "password"], reasons
imp = next(b for b in list_backups(p) if b["reason"] == "import")
assert len(Vault.open(imp["path"], PW)) == 3                 # the state just before the import
for i in range(8):
    w = Vault.open(p, "A-different-master-pass-9!"); w.add_many([(f"B{i}", "", "u", "pw")])
    w.delete_many([w.list_all()[-1]["id"]])
bl = list_backups(p)
assert len([b for b in bl if b["reason"]]) == KEEP_RISKY and all(o.exists() for o in ordinary[:0])
assert len([b for b in bl if not b["reason"]]) >= 1
snapshot_file(p, reason="sync"); assert any(b["reason"] == "sync" for b in list_backups(p))
before = len(list_backups(p)); snapshot_file(p, reason="sync"); assert len(list_backups(p)) == before   # identical copy not repeated
replaced = p.read_bytes(); restore_backup(p, list_backups(p)[-1]["path"]); assert any(b["path"].read_bytes() == replaced for b in list_backups(p))   # what was replaced is kept
w = Vault.open(p, PW); w.add("Z", "", "u", "p"); replaced = p.read_bytes(); restore_backup(p, [b for b in list_backups(p) if not b["reason"]][-1]["path"])
assert any(b["reason"] == "restore" and b["path"].read_bytes() == replaced for b in list_backups(p))
print("risky backups OK")

# ---- sync folder keeps replaced versions, and the same pruning applies there
nas = d / "nas"; nas.mkdir(); lp = d / "n" / "vault.cipher"; V = Vault.create(lp, PW)
st, h = sync.sync_once(lp, nas); assert st == "synced" and not list_backups(nas / "vault.cipher")   # first upload: nothing to keep
V.add("A", "", "u", "p"); st, h = sync.sync_once(lp, nas, h); assert st == "synced"
first = list_backups(nas / "vault.cipher"); assert len(first) == 1 and len(Vault.open(first[0]["path"], PW)) == 0
V.add("B", "", "u", "p"); st, h = sync.sync_once(lp, nas, h)
assert len(list_backups(nas / "vault.cipher")) == 2 and (nas / "vault.cipher").read_bytes() == lp.read_bytes()
st, h2 = sync.sync_once(lp, nas, h); assert len(list_backups(nas / "vault.cipher")) == 2         # no change: no new copy
print("sync folder backups OK")

# ---- delete all backups + the dialog
page = MagicMock(); page.pop_dialog.return_value = None
app.saved_vault_path = lambda: lp; app.save_vault_path = MagicMock()
a = app.App(page); a.clip.set = AsyncMock(); a.pw.value = PW; asyncio.run(a.submit_lock(None))
config.set_sync_settings(lp, folder=str(nas))
V2 = a.vault; V2.add_many([("Imp", "", "u", "p")])
a.show_backups(); bd = dlg(page)
assert any("Delete all backups" in (getattr(c, "text", None) or getattr(c, "content", None) or "") for c in walk(bd) if isinstance(getattr(c, "text", None) or getattr(c, "content", None), str))
assert any("Before an import" in (x or "") for x in texts(bd))
a.confirm_delete_backups(); cd = dlg(page); assert "Delete all backups?" in texts(cd)
assert any("sync folder" in (x or "") for x in texts(cd))
btn(cd, "Delete all backups").on_click(None)
assert not list_backups(lp) and not list_backups(nas / "vault.cipher") and lp.exists() and (nas / "vault.cipher").exists()
assert delete_backups(lp) == 0
print("delete all backups OK")
