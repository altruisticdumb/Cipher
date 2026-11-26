import sys, tempfile, builtins, io, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..")); sys.dont_write_bytecode = True
import cli, import_csv
from vault import Vault
d = Path(tempfile.mkdtemp())

def run(csv_text, answers, vault_name="v.enc"):
    c = d / "x.csv"; c.write_text(csv_text, encoding="utf-8")
    ins = iter(answers); pws = iter(["Master-pass-123!"] * 4)
    with mock.patch.object(builtins, "input", lambda *_: next(ins)), \
         mock.patch.object(cli, "getpass", lambda *_: next(pws)), \
         mock.patch.object(sys, "argv", ["import_csv.py", str(c), str(d / vault_name)]):
        import_csv.main()

run('name,url,login,password\nGmail,https://mail.google.com,dan@x.com,pw1\nGitHub,github.com,danny,"p,w""2"\n,example.org,bob,pw3\nNoPass,site.com,u,\nGmail,https://mail.google.com,dan@x.com,pw1\n',
    ["y", "y"])  # continue, create vault
v = Vault.open(d / "v.enc", "Master-pass-123!")
got = {e["title"]: e for e in v.list_all()}
assert set(got) == {"Gmail", "GitHub", "example.org"}, got.keys()
assert got["GitHub"]["password"] == 'p,w"2'
print("import OK")

try: run("foo,bar\n1,2\n", [], "v2.enc")
except SystemExit as e: print("unknown headers ->", str(e)[:60])

# speed with 500 rows
rows = "name,url,username,password\n" + "".join(f"Site{i},s{i}.com,u{i},pw{i}\n" for i in range(500))
t = time.time(); run(rows, ["y", "y"], "big.enc"); print("500 rows:", round(time.time() - t, 2), "s")
assert len(Vault.open(d / "big.enc", "Master-pass-123!")) == 500
