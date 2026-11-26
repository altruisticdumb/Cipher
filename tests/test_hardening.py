import sys, io, json, secrets, tempfile, builtins, contextlib
from pathlib import Path
from unittest import mock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import vault as V
from vault import Vault, VaultError, WrongPasswordError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
PW = "correct horse battery staple"
d = Path(tempfile.mkdtemp())

# 1. new vaults use the stronger setting
p = d / "a.enc"; v = Vault.create(p, PW); assert p.read_bytes()[4] == 17
print("new vault log_n = 17 OK")

# 2. an old (log_n 15) vault is quietly upgraded on open, data kept, old copy backed up
V.SCRYPT_LOG_N = 15
q = d / "old.enc"; o = Vault.create(q, PW); o.add("Gmail", "g.com", "me", "pw1"); assert q.read_bytes()[4] == 15
V.SCRYPT_LOG_N = 17
old_bytes = q.read_bytes()
u = Vault.open(q, PW)
assert q.read_bytes()[4] == 17 and len(u) == 1 and Vault.open(q, PW).search("gmail")[0]["password"] == "pw1"
assert any(b.read_bytes() == old_bytes for b in d.glob("old.enc.*.bak"))
print("auto-upgrade + backup OK")

# 3. a bad bulk delete can be undone from the backup; unchanged sessions don't make extra backups
r = d / "b.enc"; w = Vault.create(r, PW)
for i in range(3): w.add(f"Site{i}", "", "u", "pw")
w2 = Vault.open(r, PW); w2.delete_many([e["id"] for e in w2.list_all()]); assert len(Vault.open(r, PW)) == 0
baks = sorted(d.glob("b.enc.*.bak")); assert len(baks) == 1
restored = d / "restored.enc"; restored.write_bytes(baks[0].read_bytes()); assert len(Vault.open(restored, PW)) == 3
n = len(list(d.glob("b.enc.*.bak"))); Vault.open(r, PW); assert len(list(d.glob("b.enc.*.bak"))) == n   # open alone: no new backup
print("undo bulk delete OK")

# 4. only the newest 5 backups are kept
s = d / "c.enc"; Vault.create(s, PW)
import time
for i in range(8):
    x = Vault.open(s, PW); x.add(f"T{i}", "", "u", "pw")
    # make each backup name unique without sleeping
    for b in d.glob("c.enc.*.bak"):
        if "-x" not in b.name and not b.name.endswith(f"-{i}.bak"): pass
    time.sleep(1.05)
assert len(list(d.glob("c.enc.*.bak"))) == 5
print("rotation OK")

# 5. damaged contents / wrong format version give clean VaultErrors
def forge(path, payload: bytes):
    base = Vault.create(path, PW)
    aad = V.MAGIC + bytes([base._log_n]) + base._salt
    nonce = secrets.token_bytes(12)
    path.write_bytes(aad + nonce + AESGCM(base._key).encrypt(nonce, payload, aad))
for i, payload in enumerate([b"not json", b'{"version": 1}', b'{"version": 2, "entries": []}', b'{"version": 1, "entries": "x"}',
                             b'{"version": 1, "entries": [{"title": "x"}]}', b"[1,2]", b"\xff\xfe"]):
    f = d / f"f{i}.enc"; forge(f, payload)
    try: Vault.open(f, PW); raise SystemExit(f"payload {i} was accepted")
    except WrongPasswordError: raise SystemExit("wrong exception")
    except VaultError: pass
good = d / "good.enc"; forge(good, b'{"version": 1, "entries": []}'); assert len(Vault.open(good, PW)) == 0
print("damaged/version checks OK")

# 6. master password minimum is 14
for bad in ("short", "thirteen-char"):
    try: Vault.create(d / "m.enc", bad); raise SystemExit("accepted " + bad)
    except VaultError: pass
Vault.create(d / "m.enc", "Fourteen-chars-1!")
print("min length OK")

# 7. CLI: clean exit on a non-vault file, masked passwords by default
import cli
junk = d / "junk.enc"; junk.write_bytes(b"hello this is not a vault, just text")
with mock.patch.object(cli, "getpass", lambda *_: "whatever-password"):
    try: cli.open_or_create(junk); raise SystemExit("no exit")
    except SystemExit as e: assert "Can't open the vault" in str(e), e
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    cli.show({"title": "T", "url": "u", "username": "n", "password": "SECRET"})
    cli.show({"title": "T", "url": "u", "username": "n", "password": "SECRET"}, reveal=True)
out = buf.getvalue(); assert out.count("SECRET") == 1 and "************" in out
print("cli OK")
