import sys, os, asyncio, tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import flet as ft
import app
from vault import Vault, vault_health

STRONG = ["Xk9#mPq2Lw8vRt!z", "Yh7$nBc4Zs1dFg@j", "Qw3&eRt6Yu9iOp#a", "Zx5%cVb8Nm2lKj!h", "Ab1@cD2#eF3$gH4%",
          "Pl0=mKn9?jBh8*gV", "Tr4+yUi7!oPl2^aS", "Df6&gHj3@kLz9#xC", "Vb2$nMq5!wEr8%tY", "Ui1*oPa4#sDf7@gH"]
E = lambda i, p: {"id": f"id{i}", "title": f"Login {i}", "username": f"u{i}", "password": p}

# ---- the rating
assert vault_health([])["label"] is None and vault_health([])["total"] == 0
h = vault_health([E(i, STRONG[i]) for i in range(10)])
assert h["label"] == "Good" and h["flagged"] == 0 and not h["weak"] and not h["reused"]
h = vault_health([E(i, STRONG[i]) for i in range(9)] + [E(9, "abc")])               # 1 weak in 10: still Good (10%)
assert h["label"] == "Good" and h["flagged"] == 1 and [w["title"] for w in h["weak"]] == ["Login 9"]
h = vault_health([E(i, STRONG[i]) for i in range(8)] + [E(8, "abc"), E(9, "abd")])  # 2 weak in 10: Fair
assert h["label"] == "Fair" and h["flagged"] == 2
h = vault_health([E(i, STRONG[i]) for i in range(9)] + [E(9, STRONG[0])])           # one reused pair in 10: Fair, never Good
assert h["label"] == "Fair" and h["flagged"] == 2 and len(h["reused"]) == 1 and {x["id"] for x in h["reused"][0]} == {"id0", "id9"}
h = vault_health([E(i, STRONG[i]) for i in range(6)] + [E(6, "abc"), E(7, "abd"), E(8, "abe"), E(9, "abf")])   # 4 weak in 10: At risk
assert h["label"] == "At risk"
h = vault_health([E(0, STRONG[0]), E(1, STRONG[1]), E(2, STRONG[1])]); assert h["label"] == "At risk"             # 2 of 3 flagged
h = vault_health([E(i, STRONG[0]) for i in range(10)]); assert h["label"] == "At risk" and h["reused"][0].__len__() == 10
assert vault_health([E(0, STRONG[0]), E(1, STRONG[0].upper())])["reused"] == []                                  # case matters
assert all(set(x) == {"id", "title", "username", "ignored"} for g in h["reused"] for x in g)                                # no passwords in the result
assert "password" not in str(vault_health([E(0, "abc")]))

# ---- ignoring flags: ignored logins still count in the total and in reuse, but are never flagged
I = lambda e: dict(e, health_ignored=True)
h = vault_health([I(E(0, "abc"))] + [E(i, STRONG[i]) for i in range(1, 10)])
assert h["label"] == "Good" and h["flagged"] == 0 and not h["weak"] and [x["title"] for x in h["ignored"]] == ["Login 0"]
h = vault_health([I(E(0, "abc")), I(E(1, "abd")), E(2, "abe")] + [E(i, STRONG[i]) for i in range(3, 10)])
assert h["flagged"] == 1 and h["label"] == "Good" and h["total"] == 10                       # 1 weak of 10 left: still Good
h = vault_health([I(E(0, STRONG[0])), I(E(1, STRONG[0]))] + [E(i, STRONG[i]) for i in range(2, 10)])
assert h["label"] == "Good" and h["reused"] == [] and len(h["ignored"]) == 2                  # whole group ignored: nothing to flag
h = vault_health([I(E(0, STRONG[0])), E(1, STRONG[0])] + [E(i, STRONG[i]) for i in range(2, 10)])
assert h["label"] == "Fair" and h["flagged"] == 1 and len(h["reused"][0]) == 2                # the other login sharing it is still flagged
assert [m["ignored"] for m in sorted(h["reused"][0], key=lambda m: m["id"])] == [True, False]
print("health rating OK")

# ---- the screens
d = Path(tempfile.mkdtemp()); os.environ["APPDATA"] = str(d / "appdata"); PW = "Correct-horse-battery-1!"
def walk(c):
    yield c
    for attr in ("controls", "actions"):
        for ch in (getattr(c, attr, None) or []): yield from walk(ch)
    for attr in ("content", "title", "leading", "trailing", "subtitle"):
        ch = getattr(c, attr, None)
        if isinstance(ch, ft.Control): yield from walk(ch)
alltext = lambda root: " | ".join(str(c.value) for c in walk(root) if isinstance(c, ft.Text))
dlg = lambda page: page.show_dialog.call_args[0][0]

p = d / "h.cipher"; v = Vault.create(p, PW)
for i in range(10): v.add(f"Site {i}", "", f"user{i}", STRONG[i])
app.saved_vault_path = lambda: p; app.save_vault_path = MagicMock()
page = MagicMock(); page.pop_dialog.return_value = None
a = app.App(page); a.clip.set = AsyncMock()
helps = [c for c in walk(a.root.content) if getattr(c, "icon", None) == ft.Icons.HELP_OUTLINE]
assert helps, "help link on the lock screen"; helps[0].on_click(None)
text = alltext(dlg(page))
for must in ("What is a vault?", "How Cipher works", "Password rules", "How the vault health rating works", "AES-256",
             "5 minutes", "30 seconds", "14 characters", "20 or more", "Good", "Fair", "At risk", "forgotten master password"):
    assert must in text, must
a.pw.value = PW; asyncio.run(a.submit_lock(None))
assert a.health_btn.visible and "Good" in a.health_btn.content, a.health_btn.content
assert any(c is a.health_btn for c in walk(a.root.content))                                   # the chip is on the main screen
hdr_help = [c for c in walk(a.header) if getattr(c, "icon", None) == ft.Icons.HELP_OUTLINE]; assert hdr_help      # help icon in the header
menu = [c for c in walk(a.header) if isinstance(c, ft.PopupMenuButton)][0]
assert "Vault health" in [i.content for i in menu.items]

a.vault.add("Reused A", "", "a@x.com", "S3cret-Reuse-Pass#1"); a.vault.add("Reused B", "", "b@x.com", "S3cret-Reuse-Pass#1")
a.refresh_list()
assert "Fair" in a.health_btn.content, a.health_btn.content                                    # 2 of 12 flagged, reuse: Fair
a.health_btn.on_click(None); hd = dlg(page); t = alltext(hd)
assert "Fair" in t and "2 logins need attention out of 12" in t and "Same password on several logins" in t
assert "Reused A" in t and "Reused B" in t and "Weak passwords" not in t and "S3cret" not in t       # titles shown, never passwords
fix = [c for c in walk(hd) if getattr(c, "content", None) == "Change"]; assert len(fix) == 2
fix[0].on_click(None); ed = dlg(page)
assert "Edit login" in ed.title.value and any(getattr(c, "value", None) == "Reused A" for c in walk(ed))   # opens that login to edit

a.vault.update([e for e in a.vault.list_all() if e["title"] == "Reused B"][0]["id"], password="Another-Unique-Pass#9")
a.refresh_list(); assert "Good" in a.health_btn.content                                        # fixed: back to Good
for i, pw in enumerate(["abc", "abd", "abe"], 1): a.vault.add(f"Weak{i}", "", "w", pw)
a.refresh_list(); assert "Fair" in a.health_btn.content                                        # 3 weak of 15: Fair
for i, pw in enumerate(["abf", "abg", "abh"], 4): a.vault.add(f"Weak{i}", "", "w", pw)
a.refresh_list(); assert "At risk" in a.health_btn.content                                     # 6 weak of 18: At risk
a.health_btn.on_click(None); t = alltext(dlg(page)); assert "Weak passwords" in t and "Weak1" in t and "At risk" in t
helpbtn = [c for c in walk(dlg(page)) if getattr(c, "content", None) == "How is this worked out?"][0]; helpbtn.on_click(None)
ht = alltext(dlg(page)); assert "How the vault health rating works" in ht and "Good" in ht and "Ignore" in ht
assert "What is a vault?" not in ht and "Password rules" not in ht                              # just the rating, not the whole help
assert "Tap the ? icon" not in alltext(hd)                                                      # no leftover instruction beside the button
[c for c in walk(dlg(page)) if getattr(c, "content", None) == "All help"][0].on_click(None)
assert "What is a vault?" in alltext(dlg(page)) and "How the vault health rating works" in alltext(dlg(page))   # button for the full help

# ---- Ignore and Stop ignoring in the dialog
a.health_btn.on_click(None); hd = dlg(page)
ign = [c for c in walk(hd) if getattr(c, "content", None) == "Ignore"]; assert len(ign) >= 6          # one beside every flagged login
ign[0].on_click(None); hd = dlg(page); t = alltext(hd)
buttons = lambda root, label: [c for c in walk(root) if getattr(c, "content", None) == label]
assert "Ignored (1)" in t and len(buttons(hd, "Stop ignoring")) == 1 and sum(e.get("health_ignored", False) for e in a.vault.list_all()) == 1
ignored_entry = [e for e in a.vault.list_all() if e.get("health_ignored")][0]
reopened = Vault.open(p, PW); assert [e for e in reopened.list_all() if e.get("health_ignored")][0]["id"] == ignored_entry["id"]   # saved in the vault
for c in [c for c in walk(hd) if getattr(c, "content", None) == "Ignore"]: c.on_click(None); hd = dlg(page)   # ignore the rest, one by one
t = alltext(dlg(page)); assert "Good" in a.health_btn.content and "No weak or reused passwords" in t and "Ignored (6)" in t
assert not buttons(dlg(page), "Ignore")                                                          # nothing left to ignore
stop = buttons(dlg(page), "Stop ignoring"); assert len(stop) == 6
stop[0].on_click(None); assert "Ignored (5)" in alltext(dlg(page)) and "Good" in a.health_btn.content     # 1 weak of 18: still Good
for c in buttons(dlg(page), "Stop ignoring"): c.on_click(None)
assert "At risk" in a.health_btn.content and not any(e.get("health_ignored") for e in a.vault.list_all())
# changing a password switches its warnings back on
w = [e for e in a.vault.list_all() if e["title"] == "Weak1"][0]; a.vault.set_health_ignored(w["id"]); a.refresh_list()
assert [e for e in a.vault.list_all() if e["id"] == w["id"]][0]["health_ignored"] is True
a.vault.update(w["id"], title="Weak1 renamed"); assert [e for e in a.vault.list_all() if e["id"] == w["id"]][0].get("health_ignored")   # other edits keep it
a.vault.update(w["id"], password="abz"); assert not [e for e in a.vault.list_all() if e["id"] == w["id"]][0].get("health_ignored")
print("ignore flags OK")
for e in a.vault.list_all(): a.vault.delete(e["id"])
a.refresh_list(); assert not a.health_btn.visible                                              # empty vault: no rating shown
a.health_btn.on_click(None); assert "Add some logins" in alltext(dlg(page))
print("health and help screens OK")

page2 = MagicMock(); page2.pop_dialog.return_value = None
app.saved_vault_path = lambda: None
b = app.App(page2)
wh = [c for c in walk(b.root.content) if getattr(c, "icon", None) == ft.Icons.HELP_OUTLINE]; assert wh
wh[0].on_click(None); assert "What is a vault?" in alltext(dlg(page2))
print("help on the welcome screen OK")
