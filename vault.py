"""Encrypted password vault: the core logic.

No input(), print() or screen clearing in this file, so the same code can be
used by the menu app (cli.py) now and by a phone/desktop UI later.

File layout of a vault file (.cipher):
    4 bytes   MAGIC  b"PMV1"   (file type + format version)
    1 byte    log2 of the scrypt cost parameter N
    16 bytes  random salt
    12 bytes  random nonce (a new one on every save)
    rest      AES-256-GCM ciphertext of the JSON data (includes a 16 byte tag)
The first 21 bytes are also authenticated (AAD), so tampering is detected.
"""
import filecmp
import glob
import json
import os
import re
import secrets
import shutil
import string
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"PMV1"
SCRYPT_LOG_N = 17            # N = 131072 -> 128 MB of RAM (OWASP minimum), about 0.3 s
FORMAT_VERSION = 1
KEEP_BACKUPS = 5               # newest ordinary backups are always kept
KEEP_RISKY = 5                 # newest "before a risky action" backups
KEEP_DAILY_DAYS = 14           # then one per day for 14 days
KEEP_WEEKLY_DAYS = 70          # then one per week for about 8 more weeks
RISKY_LABELS = {"import": "Before an import", "delete": "Before deleting several",
                "password": "Before changing the master password",
                "restore": "Before restoring a backup", "sync": "Before using the sync folder's copy"}
PASSPHRASE_LEN = 20            # a long phrase counts as strong even without symbols or numbers
SALT_LEN = 16
NONCE_LEN = 12
AAD_LEN = len(MAGIC) + 1 + SALT_LEN
MIN_MASTER_LEN = 14
FIELDS = ("title", "url", "username", "password")
SYMBOLS = "!@#$%^&*()_+"


class VaultError(Exception):
    """Base class for every vault problem."""


class WrongPasswordError(VaultError):
    pass


class DuplicateError(VaultError):
    pass


class NotFoundError(VaultError):
    pass


def _derive_key(master_password, salt, log_n):
    kdf = Scrypt(salt=salt, length=32, n=2 ** log_n, r=8, p=1)
    return kdf.derive(master_password.encode("utf-8"))


def password_checks(password):
    """The simple requirements, as (label, met) pairs."""
    return [
        (f"{MIN_MASTER_LEN}+ characters", len(password) >= MIN_MASTER_LEN),
        ("lowercase letter", any(c.islower() for c in password)),
        ("uppercase letter", any(c.isupper() for c in password)),
        ("number", any(c.isdigit() for c in password)),
        ("symbol", any(not c.isalnum() and not c.isspace() for c in password)),
    ]


def password_strength(password):
    """"weak", "medium" or "strong" from simple rules (not a real cracking estimate).
    Strong = 14+ characters with a lowercase letter, an uppercase letter, a number and
    a symbol, or a long phrase of 20+ characters."""
    checks = password_checks(password)
    long_enough = checks[0][1]
    kinds = sum(ok for _, ok in checks[1:])
    if (long_enough and kinds == 4) or (
            len(password) >= PASSPHRASE_LEN and len(set(password)) >= 8):
        return "strong"
    if (long_enough and kinds >= 2) or (len(password) >= 10 and kinds >= 3):
        return "medium"
    return "weak"


HEALTH_FAIR_ABOVE = 0.10          # share of logins with a problem: above this is "Fair"
HEALTH_RISK_ABOVE = 0.30          # ... and above this is "At risk"


def vault_health(entries):
    """Overall safety of the logins in a vault, from two simple checks:
      - weak: the login's password rates "weak" (see password_strength)
      - reused: the exact same password is used on two or more logins (a threat: one
        leak opens every login that shares it)
    A login with either problem is "flagged", unless the person chose to ignore it
    (entry["health_ignored"]): ignored logins still count in the total, and still count as
    sharing a password with the others, but are never flagged themselves.
    Label: Good when nothing is reused and at most 10% of logins are weak; At risk when more
    than 30% are flagged; otherwise Fair.
    Returns {"label": "Good"/"Fair"/"At risk"/None (empty vault), "total": n, "flagged": n,
    "weak": [login, ...], "reused": [[login, ...], ...], "ignored": [login, ...]} where a login
    is {"id", "title", "username", "ignored": bool}. Passwords are never included."""
    brief = lambda e: {"id": e["id"], "title": e["title"], "username": e["username"],
                       "ignored": bool(e.get("health_ignored"))}
    entries = list(entries)
    by_password = {}
    for e in entries:
        by_password.setdefault(e["password"], []).append(e)
    reused = [[brief(e) for e in group] for group in by_password.values()
              if len(group) > 1 and any(not e.get("health_ignored") for e in group)]
    weak = [brief(e) for e in entries
            if password_strength(e["password"]) == "weak" and not e.get("health_ignored")]
    flagged = ({m["id"] for group in reused for m in group if not m["ignored"]}
               | {e["id"] for e in weak})
    ignored = sorted((brief(e) for e in entries if e.get("health_ignored")),
                     key=lambda e: e["title"].lower())
    total = len(entries)
    if not total:
        label = None
    elif len(flagged) / total > HEALTH_RISK_ABOVE:
        label = "At risk"
    elif reused or len(weak) / total > HEALTH_FAIR_ABOVE:
        label = "Fair"
    else:
        label = "Good"
    reused.sort(key=lambda g: (-len(g), g[0]["title"].lower()))
    weak.sort(key=lambda e: e["title"].lower())
    return {"label": label, "total": total, "flagged": len(flagged), "weak": weak,
            "reused": reused, "ignored": ignored}


def _check_master(master_password):
    if password_strength(master_password) != "strong":
        missing = [label for label, ok in password_checks(master_password) if not ok]
        raise VaultError("Master password needs: " + ", ".join(missing) + ". "
                         f"A phrase of {PASSPHRASE_LEN} or more characters also works.")


def copy_vault_file(source, dest):
    """Copy a vault file to a new place without ever overwriting anything.
    Returns True if it copied, False if an identical copy was already there."""
    source, dest = Path(source), Path(dest)
    if dest.exists():
        if filecmp.cmp(source, dest, shallow=False):
            return False
        raise VaultError("A different file already exists at that location")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".copying")
    try:
        shutil.copy2(source, partial)
        os.replace(partial, dest)        # the destination only appears once complete
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise
    return True


_BACKUP_RE = re.compile(r"\.(\d{8}-\d{6})(?:-(\d+))?(?:\.([a-z]+))?\.bak$")


def list_backups(vault_path):
    """Backup copies kept next to a vault, newest first:
    [{"path": Path, "when": datetime, "size": bytes, "reason": None or "import"/"delete"/...}]
    A reason means the copy was taken just before a risky action."""
    vault_path = Path(vault_path)
    found = []
    for p in vault_path.parent.glob(glob.escape(vault_path.name) + ".*.bak"):
        m = _BACKUP_RE.search(p.name)
        try:
            size, mtime = p.stat().st_size, p.stat().st_mtime
        except OSError:
            continue
        when = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else datetime.fromtimestamp(mtime)
        key = (m.group(1), int(m.group(2) or 1)) if m else ("", 0)
        found.append((key, {"path": p, "when": when, "size": size,
                            "reason": m.group(3) if m else None}))
    found.sort(key=lambda item: item[0], reverse=True)
    return [info for _, info in found]


def _new_backup_path(vault_path, reason=None):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = f".{reason}" if reason else ""
    taken = set()
    for p in vault_path.parent.glob(glob.escape(vault_path.name) + ".*.bak"):
        m = _BACKUP_RE.search(p.name)
        if m and m.group(1) == stamp:
            taken.add(int(m.group(2) or 1))
    n = max(taken) + 1 if taken else 1   # several in the same second get -2, -3... always after the last
    suffix = "" if n == 1 else f"-{n}"
    return vault_path.parent / f"{vault_path.name}.{stamp}{suffix}{tag}.bak"


def prune_backups(vault_path, protect=(), now=None):
    """Trim old backups. Ordinary ones: the newest KEEP_BACKUPS always stay, plus the newest
    of each day for KEEP_DAILY_DAYS and of each week for KEEP_WEEKLY_DAYS. "Before a risky
    action" ones: the newest KEEP_RISKY. Anything in `protect` is never removed."""
    now = now or datetime.now()
    backups = list_backups(vault_path)
    ordinary = [b for b in backups if not b["reason"]]
    risky = [b for b in backups if b["reason"]]
    keep = {b["path"] for b in ordinary[:KEEP_BACKUPS]} | {b["path"] for b in risky[:KEEP_RISKY]}
    days, weeks = set(), set()
    for b in ordinary:                                   # newest first
        age = (now - b["when"]).days
        day, week = b["when"].date(), b["when"].isocalendar()[:2]
        if age < KEEP_DAILY_DAYS and day not in days:
            days.add(day)
            keep.add(b["path"])
        if age < KEEP_WEEKLY_DAYS and week not in weeks:
            weeks.add(week)
            keep.add(b["path"])
    for b in backups:
        if b["path"] not in keep and b["path"] not in protect:
            try:
                b["path"].unlink()
            except OSError:
                pass


def _save_backup_copy(vault_path, protect=(), reason=None):
    """Copy the file as it is now into a new .bak, then trim old ones (see prune_backups).
    Ordinary copies are skipped when identical to the newest backup; "before a risky
    action" copies when identical to the newest one of their own kind."""
    vault_path = Path(vault_path)
    current = vault_path.read_bytes()
    backups = list_backups(vault_path)
    same_kind = [b for b in backups if bool(b["reason"]) == bool(reason)] if reason else backups
    if not same_kind or same_kind[0]["path"].read_bytes() != current:
        _new_backup_path(vault_path, reason).write_bytes(current)
    prune_backups(vault_path, protect)


def snapshot_file(path, protect=(), reason=None):
    """Keep a backup copy of a vault file next to it (same rules as the automatic backups)."""
    _save_backup_copy(Path(path), protect, reason)


def delete_backups(vault_path):
    """Remove every backup copy kept next to a vault. Returns how many were removed."""
    removed = 0
    for b in list_backups(vault_path):
        try:
            b["path"].unlink()
            removed += 1
        except OSError:
            pass
    return removed


def restore_backup(vault_path, backup_path, reason="restore"):
    """Replace the vault with a backup. What is being replaced is kept as a new backup
    first, so a restore can always be undone."""
    vault_path, backup_path = Path(vault_path), Path(backup_path)
    with open(backup_path, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise VaultError("That backup isn't a vault file")
    if vault_path.exists():
        _save_backup_copy(vault_path, protect={backup_path}, reason=reason)
    vault_path.parent.mkdir(parents=True, exist_ok=True)
    partial = vault_path.with_name(vault_path.name + ".restoring")
    try:
        shutil.copy2(backup_path, partial)
        os.replace(partial, vault_path)
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise


def generate_password(letters=12, digits=7, symbols=7):
    """Random password using the `secrets` module (safe for passwords)."""
    if min(letters, digits, symbols) < 0:
        raise ValueError("Counts can't be negative")
    if letters + digits + symbols == 0:
        raise ValueError("A password needs at least 1 character")
    chars = []
    if letters >= 2:                     # make sure both cases appear
        chars.append(secrets.choice(string.ascii_lowercase))
        chars.append(secrets.choice(string.ascii_uppercase))
        letters -= 2
    chars += [secrets.choice(string.ascii_letters) for _ in range(letters)]
    chars += [secrets.choice(string.digits) for _ in range(digits)]
    chars += [secrets.choice(SYMBOLS) for _ in range(symbols)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


class Vault:
    def __init__(self, path, key, salt, log_n, entries):
        self.path = Path(path)
        self._key = key
        self._salt = salt
        self._log_n = log_n
        self._entries = entries
        self._backed_up = False
        self.on_save = None          # optional callback, called after every successful save

    # ---------- create / open / save ----------
    @classmethod
    def create(cls, path, master_password):
        path = Path(path)
        if path.exists():
            raise VaultError("A vault file already exists at that location")
        _check_master(master_password)
        salt = secrets.token_bytes(SALT_LEN)
        key = _derive_key(master_password, salt, SCRYPT_LOG_N)
        vault = cls(path, key, salt, SCRYPT_LOG_N, [])
        vault._backed_up = True      # nothing to back up yet
        vault.save()
        return vault

    @classmethod
    def open(cls, path, master_password):
        raw = Path(path).read_bytes()
        if len(raw) < AAD_LEN + NONCE_LEN + 16 or raw[:4] != MAGIC:
            raise VaultError("This is not a vault file (or it is damaged)")
        log_n = raw[4]
        if not 10 <= log_n <= 20:        # guards against absurd memory use
            raise VaultError("Unsupported vault settings")
        aad = raw[:AAD_LEN]
        salt = raw[5:AAD_LEN]
        nonce = raw[AAD_LEN:AAD_LEN + NONCE_LEN]
        ciphertext = raw[AAD_LEN + NONCE_LEN:]
        key = _derive_key(master_password, salt, log_n)
        try:
            plain = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag:
            raise WrongPasswordError(
                "Wrong master password, or the vault file is damaged") from None
        damaged = VaultError("The vault decrypted but its contents are damaged")
        try:
            data = json.loads(plain.decode("utf-8"))
            version, entries = data["version"], data["entries"]
        except (ValueError, KeyError, TypeError):
            raise damaged from None
        if version != FORMAT_VERSION:
            raise VaultError("This vault was saved by a different version of the program")
        if not isinstance(entries, list) or not all(
                isinstance(e, dict) and all(k in e for k in ("id",) + FIELDS)
                for e in entries):
            raise damaged
        vault = cls(path, key, salt, log_n, entries)
        if log_n < SCRYPT_LOG_N:         # quietly move older vaults to the stronger setting
            vault._rekey(master_password, SCRYPT_LOG_N)
            try:
                vault.save()
            except OSError:
                pass                     # read-only place: it is retried on the next save
        return vault

    def save(self):
        """Encrypt everything and replace the file in one safe step."""
        self._backup_existing()
        plain = json.dumps({"version": FORMAT_VERSION, "entries": self._entries},
                           ensure_ascii=False).encode("utf-8")
        nonce = secrets.token_bytes(NONCE_LEN)
        aad = MAGIC + bytes([self._log_n]) + self._salt
        blob = aad + nonce + AESGCM(self._key).encrypt(nonce, plain, aad)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)   # a crash can't leave a half-written vault
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        if self.on_save:
            try:
                self.on_save()
            except Exception:
                pass                     # a callback problem must never break saving

    def check_password(self, master_password):
        """True if this is the vault's current master password."""
        key = _derive_key(master_password, self._salt, self._log_n)
        return secrets.compare_digest(key, self._key)

    def change_master_password(self, new_password):
        _check_master(new_password)
        self._risky_backup("password")
        self._rekey(new_password, SCRYPT_LOG_N)
        self.save()

    def _rekey(self, master_password, log_n):
        self._salt = secrets.token_bytes(SALT_LEN)
        self._log_n = log_n
        self._key = _derive_key(master_password, self._salt, log_n)

    def _risky_backup(self, reason):
        """Keep a copy of the file on disk (the state before this action) tagged with why."""
        if not self.path.exists():
            return
        try:
            _save_backup_copy(self.path, reason=reason)
        except OSError:
            pass                         # a failed backup must never block the action

    def _backup_existing(self):
        """Once per session, before the first save: keep a copy of the file as it was
        when this session started, so a bad import or delete can be undone.
        Copies are named vault.cipher.<date-time>.bak; the newest KEEP_BACKUPS are kept."""
        if self._backed_up or not self.path.exists():
            return
        self._backed_up = True
        try:
            _save_backup_copy(self.path)
        except OSError:
            pass                         # a failed backup must never block saving

    # ---------- reading ----------
    def __len__(self):
        return len(self._entries)

    def list_all(self):
        return sorted((dict(e) for e in self._entries), key=lambda e: e["title"].lower())

    def search(self, text=""):
        """Matches title, username or website. Empty text returns everything."""
        text = text.strip().lower()
        hits = [dict(e) for e in self._entries
                if not text
                or text in e["title"].lower()
                or text in e["username"].lower()
                or text in e["url"].lower()]
        return sorted(hits, key=lambda e: e["title"].lower())

    def has_login(self, title, username):
        return self._find(title, username) is not None

    # ---------- changing ----------
    def add(self, title, url, username, password, autosave=True):
        """autosave=False is for bulk imports: call save() once at the end."""
        title, url, username = title.strip(), url.strip(), username.strip()
        if not title or not password:
            raise ValueError("Title and password can't be empty")
        if self._find(title, username):
            raise DuplicateError("That login already exists")
        entry = {"id": uuid.uuid4().hex, "title": title, "url": url,
                 "username": username, "password": password}
        self._entries.append(entry)
        if autosave:
            self.save()
        return dict(entry)

    def add_many(self, rows):
        """Bulk add (used by imports). rows = (title, url, username, password) tuples.
        Saves once at the end. Returns (added, duplicates, invalid)."""
        added = duplicates = invalid = 0
        for title, url, username, password in rows:
            try:
                self.add(title, url, username, password, autosave=False)
                added += 1
            except DuplicateError:
                duplicates += 1
            except ValueError:
                invalid += 1
        if added:
            self._risky_backup("import")
            self.save()
        return added, duplicates, invalid

    def update(self, entry_id, **changes):
        entry = self._get(entry_id)
        unknown = set(changes) - set(FIELDS)
        if unknown:
            raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
        new = dict(entry)
        for field, value in changes.items():
            new[field] = value if field == "password" else value.strip()
        if not new["title"] or not new["password"]:
            raise ValueError("Title and password can't be empty")
        if self._find(new["title"], new["username"], ignore_id=entry_id):
            raise DuplicateError("That login already exists")
        password_changed = new["password"] != entry["password"]
        entry.update(new)
        if password_changed:
            entry.pop("health_ignored", None)    # a new password gets a fresh check
        self.save()
        return dict(entry)

    def set_health_ignored(self, entry_id, ignored=True):
        """Stop (or resume) flagging this login in the vault health check."""
        entry = self._get(entry_id)
        if ignored:
            entry["health_ignored"] = True
        else:
            entry.pop("health_ignored", None)
        self.save()

    def delete(self, entry_id):
        self._entries.remove(self._get(entry_id))
        self.save()

    def delete_many(self, entry_ids):
        """Delete several logins with a single save. Returns how many were removed."""
        ids = set(entry_ids)
        if ids - {e["id"] for e in self._entries}:
            raise NotFoundError("Some of those logins no longer exist")
        self._entries[:] = [e for e in self._entries if e["id"] not in ids]
        if ids:
            self._risky_backup("delete")
            self.save()
        return len(ids)

    # ---------- helpers ----------
    def _get(self, entry_id):
        for entry in self._entries:
            if entry["id"] == entry_id:
                return entry
        raise NotFoundError("Login not found")

    def _find(self, title, username, ignore_id=None):
        t, u = title.strip().lower(), username.strip().lower()
        for entry in self._entries:
            if (entry["id"] != ignore_id
                    and entry["title"].lower() == t
                    and entry["username"].lower() == u):
                return entry
        return None
