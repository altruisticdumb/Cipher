import sys, tempfile, builtins
from pathlib import Path
from unittest import mock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
sys.dont_write_bytecode = True
from vault import *

d = Path(tempfile.mkdtemp())
p = d / "v.enc"
v = Vault.create(p, "correct horse battery")
e = v.add("GitHub", "github.com", "danny", "pw1")
assert b"GitHub" not in p.read_bytes() and b"pw1" not in p.read_bytes()
try: v.add("github", "x", "DANNY", "pw2"); assert 0
except DuplicateError: pass
v.add("Bank", "bank.com", "danny", "pw3")
v.update(e["id"], password="new!pw")
assert Vault.open(p, "correct horse battery").search("git")[0]["password"] == "new!pw"
try: Vault.open(p, "wrong password!!"); assert 0
except WrongPasswordError: pass
raw = bytearray(p.read_bytes()); raw[-1] ^= 1
t = d / "t.enc"; t.write_bytes(bytes(raw))
try: Vault.open(t, "correct horse battery"); assert 0
except WrongPasswordError: pass
v.delete(e["id"]); assert len(Vault.open(p, "correct horse battery")) == 1
v.change_master_password("Another long phrase 1!")
Vault.open(p, "Another long phrase 1!")
try: Vault.create(d / "w.enc", "short"); assert 0
except VaultError: pass
assert not list(d.glob("*.tmp"))
# spaces in fields now fine
v.add("My Bank", "u", "name with space", "pass word"); assert len(Vault.open(p, "Another long phrase 1!")) == 2
# generator edge cases (old one hung on these)
for a in [(0, 5, 5), (1, 3, 3), (2, 0, 0), (12, 7, 7)]:
    pw = generate_password(*a); assert len(pw) == sum(a)
try: generate_password(0, 0, 0); assert 0
except ValueError: pass
print("vault OK")

# scripted CLI run
import cli
cli.clear = lambda: None
ins = iter(["y","1","Bank","bank.com","danny","2","n","1","","2","n","","3","bank","n","","4","","1","","","","n","","5","bank","1","y","","7"])
pws = iter(["Master-pass-123!"]*2)
c = d / "cli.enc"
with mock.patch.object(builtins, "input", lambda *_: next(ins)), mock.patch.object(cli, "getpass", lambda *_: next(pws)), mock.patch.object(sys, "argv", ["cli.py", str(c)]):
    cli.main()
assert len(Vault.open(c, "Master-pass-123!")) == 0
print("cli OK")
