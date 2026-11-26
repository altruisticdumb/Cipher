"""Keeps a copy of the vault in another folder: a NAS share, or a Google Drive /
OneDrive / Dropbox folder. The copy is the same encrypted file, so that folder never
sees a password. Everything here works on files only; nothing needs unlocking.

How it decides (three-way compare of file fingerprints):
    L = the vault on this PC
    R = the copy in the sync folder (None if it isn't there)
    B = what this PC last uploaded or confirmed to be in sync (None if never)
"""
import hashlib
import os
from pathlib import Path

from vault import snapshot_file


class SyncError(Exception):
    """Something went wrong while syncing."""


class Unreachable(SyncError):
    """The sync folder can't be used right now (NAS off, drive disconnected...)."""


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    """Fingerprint of a file, or None if it doesn't exist."""
    try:
        return sha256_of(Path(path).read_bytes())
    except FileNotFoundError:
        return None


def remote_path(local_path, folder):
    return Path(folder) / Path(local_path).name


def decide(local_hash, remote_hash, base_hash):
    """"push" (safe to upload), "synced" (already identical) or "conflict"."""
    if remote_hash is None:
        return "push"
    if local_hash == remote_hash:
        return "synced"
    if base_hash is None:
        return "conflict"            # first time: can't tell which one is newer
    if remote_hash == base_hash:
        return "push"                # only this PC changed since the last sync
    return "conflict"                # the folder's copy changed (maybe this PC too)


def _upload(data, expected_hash, remote):
    """Write to a temporary name, check it, then swap it in, so the real copy is
    never half-written, even if the connection drops."""
    partial = remote.with_name(remote.name + ".uploading")
    try:
        with open(partial, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if file_hash(partial) != expected_hash:
            raise SyncError("The uploaded copy didn't match, so it was not used")
        if remote.exists():              # keep the version being replaced (same tiered rules)
            try:
                snapshot_file(remote)
            except OSError:
                pass                     # a failed backup must never block syncing
        os.replace(partial, remote)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise


def sync_once(local_path, folder, base_hash=None, force=False):
    """One sync. Returns (status, hash): status is "synced" or "conflict", and hash is
    the new base fingerprint. Never overwrites a different copy unless force=True.
    Raises Unreachable if the folder can't be used, SyncError for other problems."""
    local, folder = Path(local_path), Path(folder)
    try:
        data = local.read_bytes()
    except OSError as err:
        raise SyncError(f"Can't read the vault on this PC: {err}") from None
    local_hash = sha256_of(data)
    remote = remote_path(local, folder)
    try:
        if not folder.is_dir():
            raise Unreachable("The sync folder isn't available")
        action = "push" if force else decide(local_hash, file_hash(remote), base_hash)
        if action == "conflict":
            return "conflict", base_hash
        if action == "push":
            _upload(data, local_hash, remote)
    except OSError as err:
        raise Unreachable(str(err)) from None
    return "synced", local_hash
