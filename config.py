"""Settings shared by cli.py and app.py.

The app remembers the last vault you opened in settings.json (in your user
profile; it holds only the vault's file path, never any secret).
DEFAULT_VAULT_PATH is the suggested place for a vault: the app's setup screen
starts from it, and cli.py uses it until a vault has been chosen.
"""
import json
import os
import sys
from pathlib import Path

APP_NAME = "Cipher"
VAULT_EXTENSION = ".cipher"          # Cipher's own file type (not .enc, which other software uses)
DEFAULT_VAULT_PATH = Path.home() / "Cipher" / f"vault{VAULT_EXTENSION}"


def settings_file():
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "settings.json"


def _read_settings():
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_settings(data):
    try:
        target = settings_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass                             # remembering settings is a convenience only


def saved_vault_path():
    """The vault remembered from last time, or None on a first run."""
    saved = _read_settings().get("vault_path")
    return Path(saved) if isinstance(saved, str) and saved else None


def load_vault_path():
    return saved_vault_path() or DEFAULT_VAULT_PATH


def save_vault_path(path):
    data = _read_settings()
    data["vault_path"] = str(path)
    _write_settings(data)


def _vault_key(vault_path):
    return os.path.normcase(str(Path(vault_path)))


def get_sync_settings(vault_path):
    """Sync settings for one vault: folder, last_hash, last_synced (empty dict if off)."""
    entry = _read_settings().get("sync", {}).get(_vault_key(vault_path), {})
    return entry if isinstance(entry, dict) else {}


def set_sync_settings(vault_path, **fields):
    data = _read_settings()
    sync = data.setdefault("sync", {})
    entry = sync.setdefault(_vault_key(vault_path), {})
    entry.update(fields)
    _write_settings(data)


def clear_sync_settings(vault_path):
    data = _read_settings()
    data.get("sync", {}).pop(_vault_key(vault_path), None)
    _write_settings(data)


def in_install_folder(path, install_dir=None):
    """True if `path` is inside the folder the app itself is installed in. Vaults must never
    live there: an update or uninstall could take them with it."""
    if install_dir is None:
        if not getattr(sys, "frozen", False):
            return False                 # running from source: no install folder
        install_dir = Path(sys.executable).parent
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(install_dir))
        return True
    except ValueError:
        return False
