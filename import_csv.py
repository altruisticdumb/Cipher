"""Import logins from a CSV export (McAfee True Key, Chrome, Bitwarden, ...)
into the encrypted vault.  Prints column names and counts, never passwords.

Usage:
    python import_csv.py EXPORT.csv VAULT.cipher

If VAULT.cipher doesn't exist it is created (you choose the master password).
Delete the CSV as soon as the import is done: it holds every password in
plain text.
"""
import csv
import sys
from pathlib import Path
from urllib.parse import urlparse

from cli import open_or_create
from vault import DuplicateError

# Possible column names for each field (compared in lower case).
ALIASES = {
    "title": ("name", "title", "site", "account", "label"),
    "url": ("url", "website", "web site", "login_uri", "link", "address"),
    "username": ("username", "login", "user", "user name", "login_username", "email"),
    "password": ("password", "login_password", "pass"),
}


def map_columns(headers):
    lowered = {h.strip().lower(): h for h in headers if h}
    mapping = {}
    for field, names in ALIASES.items():
        for name in names:
            if name in lowered:
                mapping[field] = lowered[name]
                break
    return mapping


def read_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def to_entries(rows, mapping):
    """Return (list of (title, url, username, password), number_skipped)."""
    def value(row, field):
        return (row.get(mapping.get(field, "")) or "").strip()

    entries, skipped = [], 0
    for row in rows:
        url, username = value(row, "url"), value(row, "username")
        password = row.get(mapping["password"]) or ""
        title = value(row, "title")
        if not title and url:            # no name: use the website's host name
            title = urlparse(url if "//" in url else "//" + url).hostname or url
        if not password or not title:
            skipped += 1
            continue
        entries.append((title, url, username, password))
    return entries, skipped


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    csv_path, vault_path = sys.argv[1], Path(sys.argv[2])
    headers, rows = read_rows(csv_path)
    mapping = map_columns(headers)
    print("Columns in the file:", ", ".join(headers))
    if "password" not in mapping:
        sys.exit("Couldn't find a password column. Tell Claude the column NAMES "
                 "(only the names, not any data) and it will adjust the script.")
    print("Matched:", ", ".join(f"{field} <- {col}" for field, col in mapping.items()))
    missing = [f for f in ("title", "url", "username") if f not in mapping]
    if missing:
        print("Not found (will be left empty):", ", ".join(missing))
    entries, skipped = to_entries(rows, mapping)
    print(f"{len(rows)} rows in the file -> {len(entries)} importable, "
          f"{skipped} skipped (no password, or no title/website).")
    if input("Continue (y/n)? ").strip().lower() != "y":
        sys.exit(0)

    vault = open_or_create(vault_path)
    added = duplicates = 0
    for title, url, username, password in entries:
        try:
            vault.add(title, url, username, password, autosave=False)
            added += 1
        except DuplicateError:
            duplicates += 1
    vault.save()
    print(f"Imported {added} login(s). Skipped {duplicates} duplicate(s).")
    print("Now check the vault with cli.py, then DELETE the CSV file.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
