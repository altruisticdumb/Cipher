# Cipher

*Locked. Local. Yours.*

Cipher is a free, offline password manager for Windows. Your logins live in one encrypted
file (a *vault*) on your own device. There is no account, no server, and Cipher never
connects to the internet itself.

> **Please read:** Cipher is a personal project. It has **not had an independent security
> review**. It uses standard, well-known encryption (see below), but if you rely on it, keep
> a backup of your vault file somewhere safe, and keep your master password somewhere safe
> too: **a forgotten master password cannot be recovered by anyone.**

## Features

- Encrypted vault in a single `.cipher` file, unlocked with a master password
- Add, edit, search and delete logins; copy passwords with automatic clipboard clearing
- Auto-lock after a few minutes without use
- Password generator and live strength rating
- **Vault health:** flags weak passwords and passwords reused across logins, with a
  Good / Fair / At risk rating (you can ignore flags you know are fine)
- Import from a CSV export of another password manager
- **Sync to cloud:** copies your vault into a folder you choose (a cloud drive folder, a NAS
  or a USB drive), with automatic updates and a visible status
- **Backups:** automatic copies of your vault, including one just before risky actions such
  as an import or a bulk delete, with one-click restore
- Built-in help that explains all of this in plain words
- Windows installer that registers `.cipher` files, so double-clicking a vault opens Cipher

## Install (Windows)

Download `CipherSetup-<version>.exe` from the **Releases** page and run it. You can install
for all users or only for yourself. Uninstalling never deletes your vaults.

## Run from source

Needs Python 3.12 or newer.

```
py -m pip install -r requirements.txt
py app.py
```

There is also a command-line version: `py cli.py`.

## Build the installer

1. Install [Inno Setup](https://jrsoftware.org/isdl.php) (version 6 or newer).
2. Run `build_installer.bat`. It packs the app with `flet pack` and compiles
   `installer\cipher.iss`. The setup file appears in `installer\output\`.

## Run the tests

```
py tests\run_all.py
```

## How it protects your logins

- Your vault is encrypted with AES-256, using a key derived from your master password.
- Everything happens on your device. Passwords are never sent anywhere by Cipher.
- Vaults and their backups are encrypted files; **never publish them anyway**, and never
  commit them to a repository. The included `.gitignore` keeps them out.

## Project layout

| File | What it does |
| --- | --- |
| `vault.py` | Encryption, storage, backups and the vault health check |
| `app.py` | The desktop app (built with [Flet](https://flet.dev)) |
| `sync.py` | Safe copying of the vault to a sync folder |
| `config.py` | Settings and file locations |
| `import_csv.py` | CSV import |
| `cli.py` | Command-line version |
| `installer/` | Inno Setup script and icon |
| `tests/` | Automated tests |

## License

[MIT](LICENSE). Provided as is, with no warranty.
