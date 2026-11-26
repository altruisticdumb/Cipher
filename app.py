"""Desktop UI for the encrypted vault, built with Flet.

Run:  py app.py
Needs:  py -m pip install "flet[all]"

All the real work (encryption, storing, searching) is in vault.py.
This file only draws screens and calls the vault.
"""
import asyncio
import csv
import glob
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import flet as ft

from config import (APP_NAME, DEFAULT_VAULT_PATH, VAULT_EXTENSION, in_install_folder, clear_sync_settings, get_sync_settings,
                    save_vault_path, saved_vault_path, set_sync_settings)
from import_csv import map_columns, read_rows, to_entries
from sync import SyncError, Unreachable, file_hash, remote_path, sync_once
from vault import (MAGIC, MIN_MASTER_LEN, PASSPHRASE_LEN, RISKY_LABELS, Vault, VaultError, copy_vault_file, generate_password,
                   delete_backups, list_backups, password_checks, password_strength, restore_backup, vault_health,
                   snapshot_file)

CLEAR_CLIPBOARD_AFTER = 30       # seconds
AUTOLOCK_AFTER = 300             # seconds of inactivity (5 minutes)
MASK = "\u2022" * 12
PREVIEW_ROWS = 100               # most logins listed in the import preview
SYNC_RETRY_SECONDS = 30          # try again this soon when the sync folder can't be reached
SYNC_RECHECK_SECONDS = 300       # while idle, look again this often
SYNC_STATUS_TEXT = {"synced": "Up to date", "pending": "Waiting to sync",
                    "unreachable": "Can't reach the folder", "conflict": "Different versions",
                    "error": "Problem"}


def plural(n, word="login"):
    return f"{n} {word}" + ("" if n == 1 else "s")


def same_file(a, b):
    try:
        if Path(a).exists() and Path(b).exists():
            return os.path.samefile(a, b)
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def open_in_file_manager(path):
    """Show a vault file in Explorer / Finder (selected if it exists), else open its folder."""
    path = Path(path)
    if sys.platform == "win32":
        if path.exists():
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            os.startfile(path.parent)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.exists() else ["open", str(path.parent)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


STRENGTH_STYLE = {"weak": (0.33, ft.Colors.RED_400, "Weak"),
                  "medium": (0.66, ft.Colors.ORANGE_400, "Medium"),
                  "strong": (1.0, ft.Colors.GREEN_400, "Strong")}


class StrengthMeter:
    """A small bar with a Weak / Medium / Strong label (and an optional checklist)."""

    def __init__(self, checklist=True):
        self.checklist = checklist
        self.level = ""
        self.bar = ft.ProgressBar(value=0, bar_height=6, border_radius=3, expand=True)
        self.label = ft.Text("", size=12, weight=ft.FontWeight.W_600)
        self.items = ft.Row(wrap=True, spacing=10, run_spacing=2, visible=checklist)
        self.control = ft.Column(
            [ft.Row([self.bar, self.label], vertical_alignment=ft.CrossAxisAlignment.CENTER),
             self.items], spacing=4, visible=False)

    def update(self, password):
        self.level = password_strength(password) if password else ""
        self.control.visible = bool(password)
        if not password:
            return self.level
        value, color, text = STRENGTH_STYLE[self.level]
        self.bar.value, self.bar.color = value, color
        self.label.value, self.label.color = text, color
        if self.checklist:
            checks = password_checks(password)
            self.items.controls = [
                ft.Text(("\u2713 " if ok else "\u2717 ") + label, size=11,
                        color=ft.Colors.GREEN_400 if ok else ft.Colors.OUTLINE)
                for label, ok in checks]
            if self.level == "strong" and not all(ok for _, ok in checks):
                self.items.controls.append(ft.Text(
                    "A long phrase counts as strong", size=11, color=ft.Colors.GREEN_400))
        return self.level


class App:
    def __init__(self, page: ft.Page, open_path=None):
        self.page = page
        self.vault = None
        self.vault_path = saved_vault_path()      # None on a first run
        self.setup = None            # first-run choices while the setup screens are open
        self.pending_import = None   # (csv path, entries) to import once the new vault exists
        self.pending_old = None      # old vault file to offer deleting after a copy
        self.sync_status = "off"     # off / synced / pending / unreachable / conflict / error
        self.sync_detail = ""
        self.sync_time = None        # when the last successful sync finished
        self.sync_dirty = False      # the vault was saved since the last sync
        self.sync_due = 0.0          # time.monotonic() after which the next attempt may start
        self.syncing = False
        self.sync_for = None         # which vault the sync state above belongs to
        self.sync_bar = ft.Container(
            visible=False, border_radius=10, on_click=self.show_sync,
            padding=ft.Padding.symmetric(horizontal=12, vertical=6))
        self.last_activity = time.monotonic()
        self.clip = ft.Clipboard()
        self.picker = ft.FilePicker()
        self.clip_token = None
        self.select_mode = False
        self.selected = set()
        self.checks = {}
        self.header = ft.Container()
        self.count = ft.Text("", size=13, color=ft.Colors.OUTLINE)
        self.health_btn = ft.TextButton(content="", visible=False, on_click=self.show_health)
        self.count_row = ft.Row([self.count, self.health_btn],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        self.search = ft.TextField(
            hint_text="Search title, username or website",
            prefix_icon=ft.Icons.SEARCH, border_radius=12, dense=True,
            on_change=self.on_search)
        self.list = ft.ListView(expand=True, spacing=6)
        self.root = ft.Container(expand=True, padding=ft.Padding.all(16))

        page.services.append(self.clip)
        page.services.append(self.picker)
        page.on_keyboard_event = lambda e: self.touch()
        page.add(self.root)
        page.run_task(self.autolock_loop)
        page.run_task(self.sync_loop)
        self.reset_sync_state()
        problem = None
        if open_path:                    # started by double-clicking a .cipher file
            wanted = Path(open_path)
            if self.looks_like_vault(wanted):
                self.vault_path = wanted
                save_vault_path(wanted)
            else:
                problem = wanted
        if self.vault_path is None:
            self.show_welcome()
        else:
            self.show_lock()
        if problem:
            self.alert(f"Couldn't open {problem.name}",
                       "That file isn't a Cipher vault, or it can't be read right now.")

    # ------------------------------------------------------------ helpers
    def touch(self):
        self.last_activity = time.monotonic()

    def toast(self, text):
        self.page.show_dialog(ft.SnackBar(ft.Text(text), duration=3000))

    def alert(self, title, text):
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(title), content=ft.Text(text),
            actions=[ft.TextButton("OK", on_click=lambda x: self.page.pop_dialog())]))

    async def copy(self, text, label, sensitive=False):
        self.touch()
        await self.clip.set(text)
        if not sensitive:
            self.clip_token = None
            self.toast(f"{label} copied")
            return
        token = secrets.token_hex(4)
        self.clip_token = token
        self.toast(f"{label} copied. Clipboard clears in {CLEAR_CLIPBOARD_AFTER}s")
        await asyncio.sleep(CLEAR_CLIPBOARD_AFTER)
        if self.clip_token == token:     # nothing newer was copied meanwhile
            self.clip_token = None
            await self.clip.set("")

    # ------------------------------------------------------------ lock screen
    # ------------------------------------------------------------ first-run setup
    def show_welcome(self):
        """First run: what do you have? (a vault, a CSV from another app, nothing)."""
        self.vault = None
        self.setup = None
        self.pending_import = None
        self.pending_old = None
        wide = 320
        form = ft.Column(
            [ft.Icon(ft.Icons.SHIELD_OUTLINED, size=64, color=ft.Colors.PRIMARY),
             ft.Text(APP_NAME, size=34, weight=ft.FontWeight.BOLD),
             ft.Text("Locked. Local. Yours.", size=15, color=ft.Colors.OUTLINE,
                     weight=ft.FontWeight.W_500),
             ft.Text("Welcome. What do you have?", size=15,
                     text_align=ft.TextAlign.CENTER),
             ft.Button(content="I have a Cipher vault", icon=ft.Icons.FOLDER_OPEN,
                       width=wide, on_click=self.wizard_existing),
             ft.Button(content="Import from another app (CSV)",
                       icon=ft.Icons.UPLOAD_FILE, width=wide, on_click=self.wizard_csv),
             ft.Button(content="Start new", icon=ft.Icons.ADD, width=wide,
                       on_click=self.wizard_new),
             ft.TextButton("How does Cipher work?", icon=ft.Icons.HELP_OUTLINE,
                           style=ft.ButtonStyle(color=ft.Colors.OUTLINE),
                           on_click=self.show_help)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16, tight=True, width=380)
        self.root.content = ft.Row([form], alignment=ft.MainAxisAlignment.CENTER)
        self.root.alignment = ft.Alignment.CENTER
        self.page.update()

    def staying_put(self):
        """True when an existing vault will be used where it already is."""
        s = self.setup
        return s["kind"] == "copy" and same_file(s["folder"], s["source"].parent)

    def show_location_step(self):
        s = self.setup
        staying = self.staying_put()
        copying = s["kind"] == "copy" and not staying
        if s["kind"] != "copy":
            note = "Cipher will create your vault in this folder."
        elif staying:
            note = ("Cipher will use your vault right where it is. To keep it somewhere "
                    "else, click Change. Cipher will then copy it there. Your original "
                    "file stays untouched, and you can delete it afterwards.")
        else:
            note = ("Cipher will copy your vault into this folder. Your original file "
                    "stays untouched, and you can delete it afterwards.")
        controls = [
            ft.Icon(ft.Icons.SHIELD_OUTLINED, size=48, color=ft.Colors.PRIMARY),
            ft.Text("Where should Cipher keep your vault?", size=20,
                    weight=ft.FontWeight.W_600, text_align=ft.TextAlign.CENTER)]
        if copying:
            controls.append(ft.Text(f"Copying from: {s['source']}", size=12,
                                    color=ft.Colors.OUTLINE, selectable=True,
                                    text_align=ft.TextAlign.CENTER))
        controls += [
            ft.Container(
                content=ft.Row(
                    [ft.Icon(ft.Icons.FOLDER_OPEN, size=18, color=ft.Colors.OUTLINE),
                     ft.Text(str(s["folder"]), size=13, selectable=True, expand=True),
                     ft.TextButton("Change", on_click=self.change_dest)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
                border=ft.Border.all(1, ft.Colors.OUTLINE), border_radius=12,
                padding=ft.Padding.symmetric(horizontal=12, vertical=4)),
            ft.Text(note, size=12, color=ft.Colors.OUTLINE, text_align=ft.TextAlign.CENTER),
            ft.Button(content="Copy and continue" if copying else "Continue",
                      icon=ft.Icons.ARROW_FORWARD, on_click=self.location_continue),
            ft.TextButton("Back", icon=ft.Icons.ARROW_BACK, on_click=self.setup_back)]
        form = ft.Column(
            controls, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=14, tight=True, width=380)
        self.root.content = ft.Row([form], alignment=ft.MainAxisAlignment.CENTER)
        self.root.alignment = ft.Alignment.CENTER
        self.page.update()

    def setup_back(self, e=None):
        self.touch()
        self.show_welcome()

    async def wizard_existing(self, e=None):
        self.touch()
        files = await self.picker.pick_files(dialog_title="Choose your vault file")
        if not files or not files[0].path:
            return
        source = Path(files[0].path)
        if not self.looks_like_vault(source):
            self.alert(f"Not a {APP_NAME} vault", f"{source.name} doesn't look like a vault file.")
            return
        if same_file(source, DEFAULT_VAULT_PATH):    # already where it belongs
            self.vault_path = source
            self.show_lock()
            return
        self.setup = {"kind": "copy", "source": source, "folder": source.parent}
        self.show_location_step()

    async def wizard_csv(self, e=None):
        picked = await self.pick_csv()
        if not picked:
            return
        path, entries, skipped, mapping = picked

        def go(x):
            self.page.pop_dialog()
            self.pending_import = (path, entries)
            self.setup = {"kind": "csv", "folder": DEFAULT_VAULT_PATH.parent}
            self.show_location_step()

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Import these logins?"),
            content=self.import_preview(path, entries, skipped, mapping),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Continue", on_click=go)]))

    def wizard_new(self, e=None):
        self.touch()
        self.pending_import = None
        self.setup = {"kind": "new", "folder": DEFAULT_VAULT_PATH.parent}
        self.show_location_step()

    async def change_dest(self, e=None):
        self.touch()
        folder = self.setup["folder"]
        chosen = await self.picker.get_directory_path(
            dialog_title="Choose a folder for your vault",
            initial_directory=str(folder) if folder.exists() else None)
        if not chosen:
            return
        self.setup["folder"] = Path(chosen)
        self.show_location_step()

    def location_continue(self, e=None):
        self.touch()
        s = self.setup
        dest = s["folder"] / DEFAULT_VAULT_PATH.name
        if in_install_folder(dest):
            self.alert("Not inside the Cipher program folder",
                       "That folder belongs to the program itself, so a vault there could be "
                       "lost on an update or uninstall. Choose another folder with the Change button.")
            return
        if s["kind"] == "copy":
            if self.staying_put():           # nothing to copy: use it where it is
                self.use_in_place()
                return
            try:
                copy_vault_file(s["source"], dest)    # never overwrites anything
            except (VaultError, OSError) as err:
                self.alert("Couldn't copy the vault",
                           f"{str(err).rstrip('.')}. Choose another folder with the Change button.")
                return
            self.pending_old = None if same_file(s["source"], dest) else s["source"]
            self.vault_path, self.setup = dest, None
            self.show_lock("Vault copied. Enter its master password.")
            return
        if dest.exists():
            self.alert("That folder already has a vault",
                       "Choose another folder with the Change button, or go back and "
                       "pick \"I have a Cipher vault\".")
            return
        message = ""
        if self.pending_import:
            message = f"{plural(len(self.pending_import[1]))} will be imported."
        self.vault_path, self.setup = dest, None
        self.show_lock(message, new_vault=True)

    def use_in_place(self, e=None):
        self.touch()
        self.vault_path = self.setup["source"]
        self.setup = None
        self.pending_old = None
        self.show_lock()

    def show_lock(self, message="", error=False, show_location=False, new_vault=False):
        self.vault = None
        self.list.controls = []
        self.search.value = ""
        self.select_mode = False
        self.selected = set()
        self.checks = {}
        creating = not self.vault_path.exists()
        self.creating_vault = creating
        self.meter = StrengthMeter()
        self.pw = ft.TextField(
            label="Choose a master password" if creating else "Master password",
            password=True, can_reveal_password=True, autofocus=True,
            border_radius=12, on_submit=self.submit_lock,
            on_change=self.on_pw_change if creating else None)
        self.pw2 = ft.TextField(
            label="Repeat the master password", password=True,
            can_reveal_password=True, border_radius=12,
            visible=creating, on_submit=self.submit_lock)
        self.lock_msg = ft.Text(
            message, text_align=ft.TextAlign.CENTER,
            color=ft.Colors.ERROR if error else ft.Colors.OUTLINE)
        self.lock_btn = ft.Button(
            content="Create vault" if creating else "Unlock",
            icon=ft.Icons.LOCK_OPEN, on_click=self.submit_lock, disabled=creating)
        # Location details stay hidden unless asked for. The exception is a vault
        # that has gone missing from its remembered place (moved, NAS off...), so
        # you notice before creating a new empty one by mistake.
        missing = creating and not new_vault
        has_backups = bool(list_backups(self.vault_path))
        self.vault_panel = ft.Column(
            [ft.Row([ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=16,
                             color=ft.Colors.OUTLINE),
                     ft.Text(self.vault_path.name, size=13, weight=ft.FontWeight.W_600)],
                    alignment=ft.MainAxisAlignment.CENTER, spacing=6),
             ft.Row([ft.Text(str(self.vault_path.parent), size=12, color=ft.Colors.OUTLINE,
                             text_align=ft.TextAlign.CENTER, max_lines=2,
                             overflow=ft.TextOverflow.ELLIPSIS, selectable=True,
                             expand=True),
                     ft.IconButton(ft.Icons.FOLDER_OPEN, icon_size=18,
                                   tooltip="Choose another vault",
                                   on_click=self.choose_vault)],
                    alignment=ft.MainAxisAlignment.CENTER, spacing=0),
             ft.Row([ft.TextButton("New vault", icon=ft.Icons.ADD,
                                   on_click=self.new_vault),
                     ft.TextButton("Copy path", icon=ft.Icons.CONTENT_COPY,
                                   on_click=lambda x: self.page.run_task(
                                       self.copy, str(self.vault_path), "Path")),
                     ft.TextButton("Show in folder", icon=ft.Icons.OPEN_IN_NEW,
                                   on_click=self.open_vault_folder),
                     *([ft.TextButton("Restore backup", icon=ft.Icons.RESTORE,
                                      on_click=self.show_backups)] if has_backups else [])],
                    alignment=ft.MainAxisAlignment.CENTER, wrap=True)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4,
            visible=show_location or missing)
        location_btn = ft.TextButton(
            "Vault location", icon=ft.Icons.FOLDER_OPEN,
            style=ft.ButtonStyle(color=ft.Colors.OUTLINE),
            on_click=self.toggle_vault_panel)
        controls = [
            ft.Icon(ft.Icons.SHIELD_OUTLINED, size=64, color=ft.Colors.PRIMARY),
            ft.Text(APP_NAME, size=34, weight=ft.FontWeight.BOLD),
            ft.Text("Locked. Local. Yours.", size=15, color=ft.Colors.OUTLINE,
                    weight=ft.FontWeight.W_500)]
        if creating:
            hint = ("No vault found at the location below. Open your vault, or create "
                    "a new one here." if missing else
                    "Choose a master password for your new vault. A long phrase "
                    "(20 or more characters) also works. There is no way to "
                    "recover it if you forget it.")
            controls.append(ft.Text(hint, size=13, color=ft.Colors.OUTLINE,
                                    text_align=ft.TextAlign.CENTER))
        controls += [self.pw] + ([self.meter.control] if creating else []) + [
            self.pw2, self.lock_msg, self.lock_btn, location_btn, self.vault_panel,
            ft.TextButton("How does Cipher work?", icon=ft.Icons.HELP_OUTLINE,
                          style=ft.ButtonStyle(color=ft.Colors.OUTLINE),
                          on_click=self.show_help)]
        form = ft.Column(
            controls, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=14, tight=True, width=380)
        self.root.content = ft.Row([form], alignment=ft.MainAxisAlignment.CENTER)
        self.root.alignment = ft.Alignment.CENTER
        self.page.update()

    def toggle_vault_panel(self, e=None):
        self.touch()
        self.vault_panel.visible = not self.vault_panel.visible
        self.page.update()

    # ------------------------------------------------------------ choose a vault
    @staticmethod
    def looks_like_vault(path):
        try:
            with open(path, "rb") as f:
                return f.read(len(MAGIC)) == MAGIC
        except OSError:
            return False

    def switch_vault(self, path):
        self.pending_import = None      # you changed your mind about the setup
        self.pending_old = None
        panel = getattr(self, "vault_panel", None)
        keep_open = bool(panel and panel.visible)     # stay open if you were using it
        self.vault_path = Path(path)
        save_vault_path(self.vault_path)
        self.show_lock(show_location=keep_open, new_vault=not self.vault_path.exists())

    async def choose_vault(self, e=None):
        self.touch()
        files = await self.picker.pick_files(dialog_title="Choose your vault file")
        if not files or not files[0].path:
            return
        path = Path(files[0].path)
        if not self.looks_like_vault(path):
            self.alert(f"Not a {APP_NAME} vault", f"{path.name} doesn't look like a vault file.")
            return
        self.switch_vault(path)

    async def new_vault(self, e=None):
        self.touch()
        folder = await self.picker.get_directory_path(
            dialog_title="Choose a folder for the new vault")
        if not folder:
            return
        path = Path(folder) / DEFAULT_VAULT_PATH.name
        if in_install_folder(path):
            self.alert("Not inside the Cipher program folder",
                       "That folder belongs to the program itself, so a vault there could be "
                       "lost on an update or uninstall. Choose another folder.")
            return
        if path.exists() and not self.looks_like_vault(path):
            self.alert("That name is taken",
                       f"{path.name} already exists in that folder and isn't a vault. "
                       "Choose another folder.")
            return
        self.switch_vault(path)    # an existing vault is opened, never overwritten

    def on_pw_change(self, e=None):
        """Creating a vault: show the strength live; Create unlocks once it is strong."""
        self.touch()
        level = self.meter.update(self.pw.value or "")
        self.lock_btn.disabled = level != "strong"
        self.page.update()

    def lock_error(self, text):
        self.lock_msg.value = text
        self.lock_msg.color = ft.Colors.ERROR
        self.pw.value = ""
        self.pw2.value = ""
        self.meter.update("")
        self.lock_btn.disabled = self.creating_vault     # creating needs a strong password again
        self.page.update()

    async def submit_lock(self, e=None):
        creating = not self.vault_path.exists()
        password = self.pw.value or ""
        if creating and password != (self.pw2.value or ""):
            self.lock_error("The two passwords don't match")
            return
        self.lock_btn.disabled = True
        self.lock_msg.value = "Working..."
        self.lock_msg.color = ft.Colors.OUTLINE
        self.page.update()
        try:
            opener = Vault.create if creating else Vault.open
            self.vault = await asyncio.to_thread(opener, self.vault_path, password)
        except (VaultError, OSError) as err:
            self.lock_error(str(err))
            return
        save_vault_path(self.vault_path)
        self.touch()
        self.vault.on_save = self.on_vault_saved     # every save is also synced
        self.sync_dirty, self.sync_due = True, 0.0   # check the sync folder right after unlocking
        self.show_main()
        self.after_unlock()

    def after_unlock(self):
        """Finish what the first-run setup started (CSV import / offer to delete the old copy)."""
        if self.pending_import:
            csv_path, entries = self.pending_import
            self.pending_import = None
            try:
                added, duplicates, invalid = self.vault.add_many(entries)
            except (VaultError, OSError) as err:
                self.alert("Import failed", str(err))
                return
            self.refresh_list()
            self.page.update()
            self.show_import_result(csv_path, added, duplicates + invalid)
        elif self.pending_old:
            old, self.pending_old = self.pending_old, None
            self.offer_delete_old(old, self.vault_path)

    def offer_delete_old(self, old, new):
        backups = sorted(old.parent.glob(glob.escape(old.name) + ".*.bak"))
        lines = [f"Cipher now uses the copy at {new}.",
                 f"The old file at {old} is no longer updated. Delete it?"]
        if backups:
            lines.append(f"This also removes {plural(len(backups), 'backup file')} "
                         "stored next to it.")

        def delete_old(x):
            self.touch()
            try:
                old.unlink(missing_ok=True)
                for backup in backups:
                    backup.unlink(missing_ok=True)
            except OSError as err:
                self.page.pop_dialog()
                self.alert("Couldn't delete the old file", str(err))
                return
            self.page.pop_dialog()
            self.toast("Old vault deleted")

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Delete the old vault file?"),
            content=ft.Column([ft.Text(line, selectable=True) for line in lines],
                              tight=True, spacing=8, width=420),
            actions=[ft.TextButton("Keep it", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Delete it", on_click=delete_old)]))

    def lock(self, message=""):
        for _ in range(5):               # close any open dialogs
            if not self.page.pop_dialog():
                break
        if self.clip_token:              # don't leave a password on the clipboard
            self.clip_token = None
            self.page.run_task(self.clip.set, "")
        self.show_lock(message)

    def check_autolock(self):
        if self.vault and time.monotonic() - self.last_activity > AUTOLOCK_AFTER:
            self.lock("Locked after 5 minutes of inactivity")

    async def autolock_loop(self):
        while True:
            await asyncio.sleep(10)
            self.check_autolock()

    # ------------------------------------------------------------ main screen
    def show_main(self):
        if self.sync_for != str(self.vault_path):     # a different vault than before
            self.reset_sync_state()
        self.render_header()
        self.render_sync_bar()
        self.refresh_list()
        self.root.alignment = None
        self.root.content = ft.Column(
            [self.header, self.sync_bar, self.search, self.count_row, self.list],
            spacing=12, expand=True)
        self.page.update()

    def render_header(self):
        if self.select_mode:
            self.sel_text = ft.Text("0 selected", size=18,
                                    weight=ft.FontWeight.W_600, expand=True)
            self.del_btn = ft.Button(
                content="Delete", icon=ft.Icons.DELETE, disabled=True,
                on_click=lambda x: self.confirm_bulk_delete())
            self.header.content = ft.Row([
                ft.IconButton(ft.Icons.CLOSE, tooltip="Cancel selection",
                              on_click=lambda x: self.exit_select()),
                self.sel_text,
                ft.IconButton(ft.Icons.SELECT_ALL, tooltip="Select all shown",
                              on_click=lambda x: self.select_all()),
                self.del_btn])
            self.update_selection_ui()
        else:
            self.header.content = ft.Row([
                ft.Icon(ft.Icons.SHIELD_OUTLINED, color=ft.Colors.PRIMARY),
                ft.Text(APP_NAME, size=22, weight=ft.FontWeight.BOLD, expand=True),
                ft.Button(content="Add login", icon=ft.Icons.ADD,
                          on_click=lambda x: self.show_edit(None)),
                ft.IconButton(ft.Icons.HELP_OUTLINE, tooltip="How Cipher works",
                              on_click=self.show_help),
                ft.PopupMenuButton(
                    icon=ft.Icons.MORE_VERT, tooltip="More",
                    items=[
                        ft.PopupMenuItem(content="Vault health", icon=ft.Icons.HEALTH_AND_SAFETY_OUTLINED,
                                         on_click=self.show_health),
                        ft.PopupMenuItem(content="Import from CSV", icon=ft.Icons.UPLOAD_FILE,
                                         on_click=self.start_import),
                        ft.PopupMenuItem(content="Select several", icon=ft.Icons.CHECKLIST,
                                         on_click=lambda x: self.enter_select()),
                        ft.PopupMenuItem(content="Sync to cloud", icon=ft.Icons.CLOUD_SYNC,
                                         on_click=self.show_sync),
                        ft.PopupMenuItem(content="Backups and restore", icon=ft.Icons.RESTORE,
                                         on_click=self.show_backups),
                        ft.PopupMenuItem(content="Change master password", icon=ft.Icons.KEY,
                                         on_click=self.show_change_master),
                        ft.PopupMenuItem(content="Show vault in folder", icon=ft.Icons.OPEN_IN_NEW,
                                         on_click=self.open_vault_folder)]),
                ft.IconButton(ft.Icons.LOCK, tooltip="Lock now",
                              on_click=lambda x: self.lock("Locked"))])

    def refresh_list(self):
        query = self.search.value or ""
        entries = self.vault.search(query)
        self.checks = {}
        if entries:
            self.list.controls = [self.row(entry) for entry in entries]
        else:
            self.list.controls = [ft.Container(
                ft.Text("No logins found", italic=True, color=ft.Colors.OUTLINE),
                padding=ft.Padding.all(20))]
        total = len(self.vault)
        self.count.value = (f"{len(entries)} of {total} logins" if query.strip()
                            else f"{total} logins")
        self.update_health_chip()

    def on_search(self, e=None):
        self.touch()
        if self.vault:
            self.refresh_list()
            if self.select_mode:
                self.update_selection_ui()
            self.page.update()

    def row(self, entry):
        if self.select_mode:
            return self.select_row(entry)
        letter = (entry["title"][:1] or "?").upper()
        return ft.Card(content=ft.ListTile(
            leading=ft.CircleAvatar(content=ft.Text(letter)),
            title=ft.Text(entry["title"], weight=ft.FontWeight.W_600),
            subtitle=ft.Text(entry["username"] or entry["url"] or "", max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS),
            trailing=ft.IconButton(
                ft.Icons.CONTENT_COPY, tooltip="Copy password",
                on_click=lambda x, en=entry: self.page.run_task(
                    self.copy, en["password"], "Password", True)),
            on_click=lambda x, en=entry: self.show_details(en)))

    # ------------------------------------------------------------ vault health
    HEALTH_STYLE = {"Good": (ft.Colors.GREEN_400, ft.Icons.VERIFIED_USER_OUTLINED),
                    "Fair": (ft.Colors.AMBER_400, ft.Icons.GPP_MAYBE_OUTLINED),
                    "At risk": (ft.Colors.RED_400, ft.Icons.GPP_BAD_OUTLINED)}

    def update_health_chip(self):
        health = vault_health(self.vault.list_all()) if self.vault else {"label": None}
        label = health["label"]
        self.health_btn.visible = bool(label)
        if label:
            color, icon = self.HEALTH_STYLE[label]
            self.health_btn.content = f"Vault health: {label}"
            self.health_btn.icon = icon
            self.health_btn.style = ft.ButtonStyle(color=color)

    def show_health(self, e=None):
        self.touch()
        self.page.pop_dialog()                    # when re-opened after Ignore / Stop ignoring
        health = vault_health(self.vault.list_all())
        if not health["label"]:
            self.alert("Vault health", "Add some logins and Cipher will rate how safe "
                                       "their passwords are.")
            return
        color, icon = self.HEALTH_STYLE[health["label"]]
        flagged = health["flagged"]
        summary = ("No weak or reused passwords. Nice."
                   if not flagged else
                   f"{plural(flagged)} need{'s' if flagged == 1 else ''} attention out of "
                   f"{health['total']}.")

        def fix(entry):
            full = next((x for x in self.vault.list_all() if x["id"] == entry["id"]), None)
            if full:
                self.show_edit(full)         # also closes this window

        def set_ignored(entry, ignored):
            self.touch()
            try:
                self.vault.set_health_ignored(entry["id"], ignored)
            except (VaultError, OSError) as err:
                self.alert("Couldn't save that", str(err))
                return
            self.refresh_list()              # updates the label on the main screen
            self.page.update()
            self.show_health()

        def rows(entries):
            out = []
            for x in entries:
                if x["ignored"]:
                    buttons = [ft.TextButton("Stop ignoring",
                                             on_click=lambda ev, x=x: set_ignored(x, False))]
                    sub = "Ignored" + (f" \u00b7 {x['username']}" if x["username"] else "")
                else:
                    buttons = [ft.TextButton("Change", on_click=lambda ev, x=x: fix(x)),
                               ft.TextButton("Ignore", tooltip="Stop flagging this login",
                                             on_click=lambda ev, x=x: set_ignored(x, True))]
                    sub = x["username"] or ""
                out.append(ft.ListTile(
                    title=ft.Text(x["title"], weight=ft.FontWeight.W_600,
                                  color=ft.Colors.OUTLINE if x["ignored"] else None),
                    subtitle=ft.Text(sub, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    trailing=ft.Row(buttons, tight=True, spacing=0), dense=True))
            return out

        body = [ft.Row([ft.Icon(icon, color=color, size=36),
                        ft.Text(health["label"], size=26, weight=ft.FontWeight.BOLD, color=color)],
                       spacing=10),
                ft.Text(summary, size=13)]
        if health["reused"]:
            body.append(ft.Text("Same password on several logins", size=14,
                                weight=ft.FontWeight.W_600))
            body.append(ft.Text("If one of these leaks, every login sharing it is open. "
                                "Give each its own password, or tap Ignore if you know it is "
                                "fine.", size=12, color=ft.Colors.OUTLINE))
            for group in health["reused"]:
                body += rows(group)
                body.append(ft.Divider(height=1))
        if health["weak"]:
            body.append(ft.Text("Weak passwords", size=14, weight=ft.FontWeight.W_600))
            body.append(ft.Text("Short or too simple. The Generate button in the edit window "
                                "makes a strong one.", size=12, color=ft.Colors.OUTLINE))
            body += rows(health["weak"])
        if health["ignored"]:
            body.append(ft.Text(f"Ignored ({len(health['ignored'])})", size=14,
                                weight=ft.FontWeight.W_600))
            body.append(ft.Text("Cipher doesn't flag these. Changing a login's password "
                                "turns its warnings back on.", size=12, color=ft.Colors.OUTLINE))
            body += rows(health["ignored"])
        shown = (len(health["weak"]) + len(health["ignored"])
                 + sum(len(g) + 1 for g in health["reused"]))
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Vault health"),
            content=ft.Column(body, tight=True, spacing=8, width=480, scroll=ft.ScrollMode.AUTO,
                              height=min(520, 150 + 56 * shown)),
            actions=[ft.TextButton("How is this worked out?", on_click=self.show_health_help),
                     ft.TextButton("Close", on_click=lambda x: self.page.pop_dialog())]))

    # ------------------------------------------------------------ help
    def show_health_help(self, e=None):
        self.show_help(only_health=True)

    def show_help(self, e=None, only_health=False):
        self.touch()
        self.page.pop_dialog()                    # swaps out health or any other open window

        def head(text):
            return ft.Text(text, size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY)

        def para(text):
            return ft.Text(text, size=13)

        general = [
            head("What is a vault?"),
            para("A vault is one encrypted file that holds all your logins. Without your "
                 "master password it is unreadable. Think of a locked box: the box can sit "
                 "on your PC, a USB stick or a cloud drive and what is inside stays private."),
            head("How Cipher works"),
            para("\u2022 Everything stays on your device. Cipher has no account and no server, "
                 "and it never connects to the internet itself."),
            para("\u2022 Your logins are scrambled with strong, standard encryption (AES-256), "
                 "using a key made from your master password."),
            para("\u2022 Nobody can recover a forgotten master password, not even the person "
                 "who made Cipher. Keep it somewhere safe."),
            para(f"\u2022 Cipher locks itself after {AUTOLOCK_AFTER // 60} minutes without use, "
                 f"and clears a copied password from the clipboard after "
                 f"{CLEAR_CLIPBOARD_AFTER} seconds."),
            para("\u2022 Backups: Cipher keeps copies of your vault next to it, including one "
                 "just before risky actions like an import. Find them under Backups and restore."),
            para("\u2022 Sync to cloud: copies your vault into a folder you choose (a cloud "
                 "drive folder, a NAS or a USB drive), so a second copy exists elsewhere."),
            head("Password rules"),
            para(f"\u2022 Master password: at least {MIN_MASTER_LEN} characters with a lowercase "
                 f"letter, an uppercase letter, a number and a symbol, or a phrase of "
                 f"{PASSPHRASE_LEN} or more characters."),
            para("\u2022 Your logins: Cipher never forces a rule. It rates each password "
                 "Weak, Medium or Strong as you type, and you decide."),
        ]
        health = [
            head("How the vault health rating works"),
            para("Cipher looks for two problems: a weak password, and the same password used "
                 "on more than one login (if one leaks, they all open). A login with either "
                 "problem counts as flagged."),
            para("\u2022 Good: no password is reused, and no more than 1 in 10 logins is weak."),
            para("\u2022 Fair: some reuse, or more than 1 in 10 weak, but fewer than 3 in 10 "
                 "logins flagged."),
            para("\u2022 At risk: more than 3 in 10 logins are flagged."),
            para("If you know a password is fine (a shared family streaming login, say), tap "
                 "Ignore next to it in Vault health. Cipher stops flagging that login. "
                 "Changing its password turns the warnings back on, and Stop ignoring "
                 "undoes it any time."),
            para("The check happens on your device, and your passwords never leave the vault.")]
        body = health if only_health else general + health
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("How vault health is rated" if only_health else "How Cipher works"),
            content=ft.Column(body, tight=True, spacing=8, width=460,
                              height=None if only_health else 440, scroll=ft.ScrollMode.AUTO),
            actions=([ft.TextButton("All help", on_click=self.show_help)] if only_health else [])
            + [ft.TextButton("Close", on_click=lambda x: self.page.pop_dialog())]))

    # ------------------------------------------------------------ select several
    def enter_select(self):
        self.touch()
        self.select_mode = True
        self.selected = set()
        self.render_header()
        self.refresh_list()
        self.page.update()

    def exit_select(self):
        self.touch()
        self.select_mode = False
        self.selected = set()
        self.render_header()
        self.refresh_list()
        self.page.update()

    def select_row(self, entry):
        entry_id = entry["id"]
        box = ft.Checkbox(value=entry_id in self.selected)
        box.on_change = lambda x, i=entry_id, b=box: self.set_selected(i, b.value)
        self.checks[entry_id] = box
        return ft.Card(content=ft.ListTile(
            leading=box,
            title=ft.Text(entry["title"], weight=ft.FontWeight.W_600),
            subtitle=ft.Text(entry["username"] or entry["url"] or "", max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS),
            on_click=lambda x, i=entry_id: self.toggle(i)))

    def set_selected(self, entry_id, flag):
        self.touch()
        if flag:
            self.selected.add(entry_id)
        else:
            self.selected.discard(entry_id)
        self.update_selection_ui()
        self.page.update()

    def toggle(self, entry_id):
        box = self.checks.get(entry_id)
        flag = entry_id not in self.selected
        if box is not None:
            box.value = flag
        self.set_selected(entry_id, flag)

    def select_all(self):
        self.touch()
        for entry in self.vault.search(self.search.value or ""):
            self.selected.add(entry["id"])
        self.refresh_list()
        self.update_selection_ui()
        self.page.update()

    def visible_selected(self):
        """Selected logins that are on screen right now (what Delete acts on)."""
        shown = self.vault.search(self.search.value or "")
        return [e for e in shown if e["id"] in self.selected]

    def update_selection_ui(self):
        n = len(self.visible_selected())
        self.sel_text.value = f"{n} selected"
        self.del_btn.disabled = n == 0

    def confirm_bulk_delete(self):
        self.touch()
        ids = [e["id"] for e in self.visible_selected()]
        if not ids:
            return

        def do_delete(x):
            self.touch()
            try:
                removed = self.vault.delete_many(ids)
            except (VaultError, OSError) as err:
                self.toast(str(err))
                return
            self.page.pop_dialog()
            self.exit_select()
            self.toast(f"Deleted {plural(removed)}")

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(f"Delete {plural(len(ids))}?"),
            content=ft.Text("They will be removed for good. This can't be undone."),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Delete", on_click=do_delete)]))

    # ------------------------------------------------------------ import from CSV
    async def pick_csv(self):
        """Ask for a CSV and read it. Returns (path, entries, skipped, mapping) or None."""
        self.touch()
        files = await self.picker.pick_files(
            dialog_title="Choose the CSV export",
            file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["csv"])
        self.touch()
        if not files:
            return None
        path = Path(files[0].path)
        try:
            headers, rows = read_rows(path)
        except (OSError, UnicodeDecodeError, csv.Error) as err:
            self.alert("Couldn't read that file", str(err))
            return None
        mapping = map_columns(headers)
        if "password" not in mapping:
            self.alert("No password column found",
                       "Columns in this file: " + (", ".join(headers) or "none"))
            return None
        entries, skipped = to_entries(rows, mapping)
        if not entries:
            self.alert("Nothing to import",
                       f"{plural(len(rows), 'row')} read, but none had both a "
                       "title (or website) and a password.")
            return None
        return path, entries, skipped, mapping

    def import_preview(self, path, entries, skipped, mapping):
        """The logins that were found, so you can check them before importing.
        Passwords stay hidden unless you press Show passwords."""
        secrets_shown = {"on": False}
        pw_texts, rows = [], []
        for title, url, username, password in entries[:PREVIEW_ROWS]:
            user = username or "(no username)"
            credentials = ft.Text(f"{user} - {MASK}", size=12, selectable=True)
            pw_texts.append((credentials, user, password))
            rows.append(ft.Column(
                [ft.Text(title, weight=ft.FontWeight.W_600),
                 ft.Text(url or "(no website)", size=12, color=ft.Colors.OUTLINE),
                 credentials], spacing=0))
        toggle_btn = ft.TextButton("Show passwords", icon=ft.Icons.VISIBILITY)

        def toggle(x):
            self.touch()
            secrets_shown["on"] = not secrets_shown["on"]
            for credentials, user, secret in pw_texts:
                credentials.value = f"{user} - {secret if secrets_shown['on'] else MASK}"
            toggle_btn.content = "Hide passwords" if secrets_shown["on"] else "Show passwords"
            toggle_btn.icon = (ft.Icons.VISIBILITY_OFF if secrets_shown["on"]
                               else ft.Icons.VISIBILITY)
            self.page.update()

        toggle_btn.on_click = toggle
        notes = [f"File: {path.name}"]
        if skipped:
            notes.append(f"{plural(skipped, 'row')} skipped (no password, or no title/website).")
        missing = [f for f in ("title", "url", "username") if f not in mapping]
        if missing:
            notes.append("No column found for: " + ", ".join(missing) + " (left empty).")
        controls = [ft.Text(f"{plural(len(entries))} found", weight=ft.FontWeight.W_600),
                    *[ft.Text(n, size=12, color=ft.Colors.OUTLINE) for n in notes],
                    toggle_btn,
                    ft.Container(ft.ListView(rows, height=240, spacing=10),
                                 border=ft.Border.all(1, ft.Colors.OUTLINE),
                                 border_radius=8, padding=ft.Padding.all(10))]
        if len(entries) > len(rows):
            controls.append(ft.Text(f"...and {len(entries) - len(rows)} more not shown",
                                    size=12, color=ft.Colors.OUTLINE))
        return ft.Column(controls, tight=True, spacing=6, width=420)

    async def start_import(self, e=None):
        picked = await self.pick_csv()
        if not picked:
            return
        path, entries, skipped, mapping = picked

        def do_import(x):
            self.touch()
            try:
                added, duplicates, invalid = self.vault.add_many(entries)
            except (VaultError, OSError) as err:
                self.page.pop_dialog()
                self.alert("Import failed", str(err))
                return
            self.page.pop_dialog()
            self.refresh_list()
            self.page.update()
            self.show_import_result(path, added, duplicates + invalid)

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Import these logins?"),
            content=self.import_preview(path, entries, skipped, mapping),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Import", on_click=do_import)]))

    def show_import_result(self, path, added, skipped_dupes):
        lines = [f"Added {plural(added)}."]
        if skipped_dupes:
            lines.append(f"Skipped {plural(skipped_dupes)} that already existed.")
        lines.append("The CSV file holds every password in plain text. "
                     "Delete it now that the import is done.")

        def delete_file(x):
            try:
                path.unlink()
                message = "CSV file deleted"
            except OSError as err:
                message = f"Couldn't delete the file: {err}"
            self.page.pop_dialog()
            self.toast(message)

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Import finished"),
            content=ft.Column([ft.Text(line) for line in lines],
                              tight=True, spacing=8, width=420),
            actions=[ft.TextButton("Keep file", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Delete the CSV file", on_click=delete_file)]))

    # ------------------------------------------------------------ sync to a folder
    @staticmethod
    def format_when(moment):
        if not moment:
            return ""
        if moment.date() == datetime.now().date():
            return moment.strftime("%H:%M")
        return moment.strftime("%d %b, %H:%M")

    def sync_cfg(self):
        return get_sync_settings(self.vault_path) if self.vault_path else {}

    def sync_enabled(self):
        return bool(self.sync_cfg().get("folder"))

    def reset_sync_state(self):
        self.sync_for = str(self.vault_path) if self.vault_path else None
        cfg = self.sync_cfg()
        self.sync_status = "pending" if cfg.get("folder") else "off"
        self.sync_detail = ""
        self.sync_dirty = False
        self.sync_due = 0.0
        try:
            last = cfg.get("last_synced")
            self.sync_time = datetime.fromisoformat(last) if last else None
        except (ValueError, TypeError):
            self.sync_time = None

    def on_vault_saved(self):
        """The vault calls this after every save (sometimes from a worker thread)."""
        self.sync_dirty = True
        self.sync_due = 0.0
        if self.sync_status in ("synced", "pending", "unreachable", "error"):
            self.sync_status = "pending"

    async def sync_loop(self):
        while True:
            await asyncio.sleep(2)
            await self.sync_tick()

    async def sync_tick(self):
        if (self.vault_path and not self.syncing and self.sync_status != "conflict"
                and time.monotonic() >= self.sync_due and self.sync_enabled()):
            await self.sync_now()

    async def sync_now(self, force=False):
        cfg = self.sync_cfg()
        folder = cfg.get("folder")
        if not folder:
            self.sync_status = "off"
            self.refresh_sync_bar()
            return
        self.syncing = True
        self.sync_dirty = False
        try:
            status, new_hash = await asyncio.to_thread(
                sync_once, self.vault_path, Path(folder), cfg.get("last_hash"), force)
        except Unreachable as err:
            self.sync_status, self.sync_detail = "unreachable", str(err)
            self.sync_dirty = True
            self.sync_due = time.monotonic() + SYNC_RETRY_SECONDS
        except Exception as err:             # SyncError or anything unexpected
            self.sync_status, self.sync_detail = "error", str(err)
            self.sync_dirty = True
            self.sync_due = time.monotonic() + 60
        else:
            if status == "conflict":
                self.sync_status = "conflict"
            else:
                self.sync_time = datetime.now()
                set_sync_settings(self.vault_path, last_hash=new_hash,
                                  last_synced=self.sync_time.isoformat(timespec="seconds"))
                self.sync_status = "synced"
                self.sync_due = (0.0 if self.sync_dirty
                                 else time.monotonic() + SYNC_RECHECK_SECONDS)
        finally:
            self.syncing = False
        self.refresh_sync_bar()

    def render_sync_bar(self):
        status = self.sync_status if self.sync_enabled() else "off"
        bar = self.sync_bar
        bar.visible = status != "off"
        if status == "off":
            return
        when = self.format_when(self.sync_time)
        icon, color, text, tint, action = {
            "synced": (ft.Icons.CLOUD_DONE, ft.Colors.GREEN_400,
                       f"Synced {when}" if when else "Synced", None, None),
            "pending": (ft.Icons.CLOUD_SYNC, ft.Colors.ORANGE_400,
                        "Saved on this PC. Syncing...", None, None),
            "unreachable": (ft.Icons.CLOUD_OFF, ft.Colors.ORANGE_400,
                            "Can't reach the sync folder. Your changes are saved on this PC "
                            "and will sync when it's back.", ft.Colors.ORANGE, None),
            "conflict": (ft.Icons.WARNING_AMBER, ft.Colors.RED_400,
                         "The sync folder has a different version of your vault.",
                         ft.Colors.RED, "Resolve"),
            "error": (ft.Icons.ERROR_OUTLINE, ft.Colors.RED_400,
                      f"Sync problem: {self.sync_detail}", ft.Colors.RED, None)}[status]
        controls = [ft.Icon(icon, size=18, color=color),
                    ft.Text(text, size=12, expand=True,
                            color=ft.Colors.OUTLINE if status == "synced" else None)]
        if action:
            controls.append(ft.TextButton(action, on_click=self.show_conflict))
        bar.content = ft.Row(controls, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        bar.bgcolor = ft.Colors.with_opacity(0.12, tint) if tint else None

    def refresh_sync_bar(self):
        self.render_sync_bar()
        self.page.update()

    def show_sync(self, e=None):
        self.touch()
        if not self.vault_path:
            return
        folder = self.sync_cfg().get("folder")
        close = ft.TextButton("Close", on_click=lambda x: self.page.pop_dialog())
        if not folder:
            self.page.show_dialog(ft.AlertDialog(
                title=ft.Text("Sync to cloud"),
                content=ft.Column(
                    [ft.Text("Cipher can keep a copy of your vault in a folder you choose, "
                             "such as your NAS share or a Google Drive, OneDrive or Dropbox "
                             "folder. It saves there automatically after every change."),
                     ft.Text("The copy is encrypted, so that folder never sees your "
                             "passwords.", size=12, color=ft.Colors.OUTLINE)],
                    tight=True, spacing=8, width=420),
                actions=[close, ft.Button(content="Choose folder", icon=ft.Icons.FOLDER_OPEN,
                                          on_click=self.choose_sync_folder)]))
            return
        when = self.format_when(self.sync_time) or "never"
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Sync to cloud"),
            content=ft.Column(
                [ft.Text(f"Folder: {folder}", selectable=True),
                 ft.Text(f"Status: {SYNC_STATUS_TEXT.get(self.sync_status, '')}"),
                 ft.Text(f"Last synced: {when}"),
                 ft.Row([ft.Button(content="Sync now", icon=ft.Icons.SYNC,
                                   on_click=self.sync_now_click),
                         ft.TextButton("Change folder", on_click=self.choose_sync_folder),
                         ft.TextButton("Turn off", on_click=self.turn_off_sync)],
                        wrap=True, spacing=4)],
                tight=True, spacing=8, width=420),
            actions=[close]))

    async def choose_sync_folder(self, e=None):
        self.touch()
        self.page.pop_dialog()
        chosen = await self.picker.get_directory_path(
            dialog_title="Choose the folder to sync to")
        if not chosen:
            return
        folder = Path(chosen)
        if same_file(folder, self.vault_path.parent):
            self.alert("That's the vault's own folder",
                       "Choose a different folder, such as your NAS share or a cloud folder.")
            return
        set_sync_settings(self.vault_path, folder=str(folder), last_hash=None, last_synced=None)
        self.sync_time, self.sync_status = None, "pending"
        self.refresh_sync_bar()
        await self.sync_now()
        if self.sync_status == "conflict":
            self.show_conflict()
        elif self.sync_status == "synced":
            self.toast("Sync is on. Your vault is saved in that folder.")
        else:
            self.alert("Sync is on, but the folder isn't reachable yet",
                       f"{self.sync_detail}. Cipher will keep trying.")

    async def sync_now_click(self, e=None):
        self.touch()
        self.page.pop_dialog()
        await self.sync_now()
        if self.sync_status == "conflict":
            self.show_conflict()
        elif self.sync_status == "synced":
            self.toast("Synced")

    def turn_off_sync(self, e=None):
        self.touch()
        self.page.pop_dialog()
        clear_sync_settings(self.vault_path)
        self.reset_sync_state()
        self.refresh_sync_bar()
        self.toast("Sync turned off. Copies already in the folder were left there.")

    def show_conflict(self, e=None):
        self.touch()
        self.page.pop_dialog()
        cfg = self.sync_cfg()
        if not cfg.get("folder"):
            return
        local = self.vault_path
        remote = remote_path(local, cfg["folder"])

        def describe(path):
            try:
                info = path.stat()
            except OSError:
                return "not found"
            return (datetime.fromtimestamp(info.st_mtime).strftime("%d %b %Y, %H:%M")
                    + f", {max(info.st_size / 1024, 0.1):.1f} KB")

        async def keep_pc(x):
            self.touch()
            self.page.pop_dialog()
            try:
                if await asyncio.to_thread(remote.exists):
                    await asyncio.to_thread(snapshot_file, remote, (), "sync")   # keep the folder's version
            except OSError as err:
                self.alert("Couldn't back up the folder's version", str(err))
                return
            await self.sync_now(force=True)
            if self.sync_status == "synced":
                self.toast("The sync folder now has this PC's version")

        async def use_folder(x):
            self.touch()
            self.page.pop_dialog()
            try:
                await asyncio.to_thread(restore_backup, local, remote, "sync")   # keeps this PC's version first
                new_hash = await asyncio.to_thread(file_hash, local)
            except (VaultError, OSError) as err:
                self.alert("Couldn't use the sync folder's version", str(err))
                return
            now = datetime.now()
            set_sync_settings(local, last_hash=new_hash, last_synced=now.isoformat(timespec="seconds"))
            self.sync_status, self.sync_time, self.sync_dirty = "synced", now, False
            message = "Now using the version from the sync folder. Enter its master password."
            if self.vault:
                self.lock(message)
            else:
                self.show_lock(message)

        first = not cfg.get("last_hash")
        intro = ("That folder already contains a different vault file." if first else
                 "The copy in the sync folder is different from the one this PC last synced. "
                 "It may have been changed on another device.")
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Two versions of your vault"),
            content=ft.Column(
                [ft.Text(intro),
                 ft.Text(f"This PC: {describe(local)}", size=13),
                 ft.Text(f"Sync folder: {describe(remote)}", size=13),
                 ft.Text("The one you don't keep is saved as a backup first, so nothing "
                         "is lost.", size=12, color=ft.Colors.OUTLINE),
                 ft.Button(content="Use the sync folder's version", on_click=use_folder),
                 ft.Button(content="Keep this PC's version", on_click=keep_pc)],
                tight=True, spacing=10, width=420),
            actions=[ft.TextButton("Decide later", on_click=lambda x: self.page.pop_dialog())]))

    # ------------------------------------------------------------ show the vault in Explorer
    def open_vault_folder(self, e=None):
        self.touch()
        folder = self.vault_path.parent
        if not folder.exists():
            self.alert("Folder not found", f"{folder} doesn't exist (yet).")
            return
        try:
            open_in_file_manager(self.vault_path)
        except OSError as err:
            self.alert("Couldn't open the folder", str(err))

    # ------------------------------------------------------------ change master password
    def show_change_master(self, e=None):
        self.touch()
        current = ft.TextField(label="Current master password", password=True,
                               can_reveal_password=True, autofocus=True)
        new = ft.TextField(label="New master password", password=True,
                           can_reveal_password=True)
        again = ft.TextField(label="Repeat the new master password", password=True,
                             can_reveal_password=True)
        meter = StrengthMeter()
        status = ft.Text("", size=13)
        save_btn = ft.Button(content="Save", disabled=True)

        def say(text, bad=True):
            status.value = text
            status.color = ft.Colors.ERROR if bad else ft.Colors.OUTLINE

        def on_new(x):
            self.touch()
            save_btn.disabled = meter.update(new.value or "") != "strong"
            self.page.update()

        async def save(x):
            self.touch()
            if not self.vault:
                return
            old_pw, new_pw = current.value or "", new.value or ""
            if new_pw != (again.value or ""):
                say("The two new passwords don't match")
                self.page.update()
                return
            save_btn.disabled = True
            say("Working...", bad=False)
            self.page.update()
            try:
                if not await asyncio.to_thread(self.vault.check_password, old_pw):
                    say("The current master password is wrong")
                elif new_pw == old_pw:
                    say("That is already your master password")
                else:
                    await asyncio.to_thread(self.vault.change_master_password, new_pw)
                    self.page.pop_dialog()
                    self.toast("Master password changed")
                    return
            except (VaultError, OSError) as err:
                say(str(err))
            save_btn.disabled = meter.level != "strong"
            self.page.update()

        new.on_change = on_new
        save_btn.on_click = save
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Change master password"),
            content=ft.Column(
                [current, new, meter.control, again, status,
                 ft.Text("Backups made earlier still use the old password. There is no way "
                         "to recover the vault if you forget the new one.",
                         size=12, color=ft.Colors.OUTLINE)],
                tight=True, spacing=10, width=420),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     save_btn]))

    # ------------------------------------------------------------ backups
    def show_backups(self, e=None):
        self.touch()
        backups = list_backups(self.vault_path)
        if not backups:
            self.alert("No backups yet",
                       "Cipher saves a copy of your vault before the first change of each "
                       "session, and before risky actions like imports. Once you have made "
                       "a change, backups appear here.")
            return
        rows = [ft.ListTile(
            title=ft.Text(b["when"].strftime("%d %b %Y, %H:%M")),
            subtitle=ft.Text(f"{max(b['size'] / 1024, 0.1):.1f} KB"
                             + (f" - {RISKY_LABELS.get(b['reason'], 'Before a risky action')}"
                                if b["reason"] else "")),
            trailing=ft.TextButton("Restore", on_click=lambda x, b=b: self.confirm_restore(b)))
            for b in backups]
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Backups"),
            content=ft.Column(
                [ft.Text("Cipher keeps a copy from the start of each session: every day for "
                         "2 weeks, then every week for about 2 months. It also keeps the "
                         "last 5 taken just before an import, a bulk delete, a master "
                         "password change or a restore. The sync folder keeps older "
                         "versions the same way.", size=12, color=ft.Colors.OUTLINE),
                 ft.ListView(rows, height=min(300, 64 * len(rows)), spacing=4)],
                tight=True, spacing=8, width=420),
            actions=[ft.TextButton("Delete all backups", icon=ft.Icons.DELETE_FOREVER,
                                   on_click=self.confirm_delete_backups),
                     ft.TextButton("Close", on_click=lambda x: self.page.pop_dialog())]))

    def sync_backup_target(self):
        """The vault's copy in the sync folder (whose backups sit next to it), or None."""
        folder = self.sync_cfg().get("folder")
        return remote_path(self.vault_path, folder) if folder else None

    def confirm_delete_backups(self, e=None):
        self.touch()
        self.page.pop_dialog()
        remote = self.sync_backup_target()
        local_count = len(list_backups(self.vault_path))
        remote_count = 0
        if remote:
            try:
                remote_count = len(list_backups(remote))
            except OSError:
                remote_count = 0
        lines = [f"This removes {plural(local_count, 'backup')} stored next to your vault"
                 + (f" and {plural(remote_count, 'backup')} in the sync folder." if remote
                    else "."),
                 "Your vault itself and the sync folder's current copy are not touched."]

        def do_delete(x):
            self.touch()
            try:
                delete_backups(self.vault_path)
                if remote:
                    delete_backups(remote)
            except OSError as err:
                self.page.pop_dialog()
                self.alert("Couldn't delete the backups", str(err))
                return
            self.page.pop_dialog()
            self.toast("All backups deleted")

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Delete all backups?"),
            content=ft.Column(
                [ft.Text(lines[0]),
                 ft.Text(lines[1] + " This can't be undone.", size=12, color=ft.Colors.OUTLINE)],
                tight=True, spacing=8, width=420),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Delete all backups", on_click=do_delete)]))

    def confirm_restore(self, backup):
        self.touch()
        self.page.pop_dialog()
        when = backup["when"].strftime("%d %b %Y, %H:%M")

        def do_restore(x):
            self.touch()
            try:
                restore_backup(self.vault_path, backup["path"])
            except (VaultError, OSError) as err:
                self.page.pop_dialog()
                self.alert("Couldn't restore the backup", str(err))
                return
            self.page.pop_dialog()
            message = "Backup restored. Enter the master password this vault had at that time."
            if self.vault:
                self.lock(message)
            else:
                self.show_lock(message)

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Restore this backup?"),
            content=ft.Column(
                [ft.Text(f"Your vault will be replaced by the backup from {when}."),
                 ft.Text("What you have now is saved as a backup first, so you can undo "
                         "this. You'll need the master password the vault had at that time.",
                         size=12, color=ft.Colors.OUTLINE)],
                tight=True, spacing=8, width=420),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Restore", on_click=do_restore)]))

    # ------------------------------------------------------------ details
    def detail_row(self, label, value, secret=False):
        text = ft.Text(MASK if secret else (value or "-"), selectable=True, size=16)
        buttons = []
        if secret:
            def toggle(x):
                self.touch()
                text.value = MASK if text.value == value else value
                self.page.update()
            buttons.append(ft.IconButton(ft.Icons.VISIBILITY, tooltip="Show / hide",
                                         on_click=toggle))
        if value:
            buttons.append(ft.IconButton(
                ft.Icons.CONTENT_COPY, tooltip="Copy",
                on_click=lambda x: self.page.run_task(self.copy, value, label, secret)))
        return ft.Row(
            [ft.Column([ft.Text(label, size=12, color=ft.Colors.OUTLINE), text],
                       spacing=2, expand=True), *buttons],
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def show_details(self, entry):
        self.touch()
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(entry["title"]),
            content=ft.Column(
                [self.detail_row("Website", entry["url"]),
                 self.detail_row("Username", entry["username"]),
                 self.detail_row("Password", entry["password"], secret=True)],
                tight=True, spacing=8, width=420),
            actions=[
                ft.TextButton("Delete", on_click=lambda x: self.confirm_delete(entry)),
                ft.TextButton("Edit", on_click=lambda x: self.show_edit(entry)),
                ft.TextButton("Close", on_click=lambda x: self.page.pop_dialog())]))

    # ------------------------------------------------------------ add / edit
    def show_edit(self, entry):
        self.touch()
        self.page.pop_dialog()           # closes the details dialog if it is open
        get = (lambda key: entry[key]) if entry else (lambda key: "")
        title = ft.TextField(label="Title", value=get("title"), autofocus=True)
        url = ft.TextField(label="Website", value=get("url"))
        username = ft.TextField(label="Username / email", value=get("username"))
        meter = StrengthMeter(checklist=False)
        password = ft.TextField(label="Password", value=get("password"),
                                password=True, can_reveal_password=True)

        def on_password(x):
            self.touch()
            meter.update(password.value or "")
            self.page.update()

        password.on_change = on_password
        meter.update(password.value or "")
        length_text = ft.Text("Generated length: 20", size=13)
        length = ft.Slider(min=8, max=40, divisions=32, value=20, label="{value}")
        error = ft.Text("", color=ft.Colors.ERROR)

        def on_length(x):
            self.touch()
            length_text.value = f"Generated length: {int(length.value)}"
            self.page.update()

        def generate(x):
            self.touch()
            n = int(length.value)
            letters, digits = round(n * 0.6), round(n * 0.2)
            password.value = generate_password(letters, digits, n - letters - digits)
            password.password = False    # show it so you can see what was made
            meter.update(password.value)
            self.page.update()

        def save(x):
            self.touch()
            try:
                if entry:
                    self.vault.update(entry["id"], title=title.value, url=url.value,
                                      username=username.value, password=password.value)
                else:
                    self.vault.add(title.value, url.value, username.value, password.value)
            except (VaultError, ValueError, OSError) as err:
                error.value = str(err)
                self.page.update()
                return
            self.page.pop_dialog()
            self.refresh_list()
            self.page.update()
            self.toast("Saved")

        length.on_change = on_length
        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit login" if entry else "Add login"),
            content=ft.Column(
                [title, url, username, password, meter.control,
                 ft.Row([length_text, ft.TextButton("Generate", icon=ft.Icons.AUTORENEW,
                                                    on_click=generate)],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                 length, error],
                tight=True, spacing=10, width=420),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Save", on_click=save)]))

    # ------------------------------------------------------------ delete one
    def confirm_delete(self, entry):
        self.touch()
        self.page.pop_dialog()

        def do_delete(x):
            self.touch()
            try:
                self.vault.delete(entry["id"])
            except (VaultError, OSError) as err:
                self.toast(str(err))
                return
            self.page.pop_dialog()
            self.refresh_list()
            self.page.update()
            self.toast("Deleted")

        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Delete this login?"),
            content=ft.Text(f"'{entry['title']}' will be removed for good."),
            actions=[ft.TextButton("Cancel", on_click=lambda x: self.page.pop_dialog()),
                     ft.Button(content="Delete", on_click=do_delete)]))


def main(page: ft.Page):
    page.title = APP_NAME
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)
    page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 520
    page.window.height = 780
    page.window.min_width = 380
    page.window.min_height = 520
    App(page, open_path=startup_vault_arg())


def startup_vault_arg(argv=None):
    """The vault file Windows passed when a .cipher file was opened, or None."""
    for arg in (sys.argv[1:] if argv is None else argv):
        if arg.lower().endswith(VAULT_EXTENSION):
            return arg
    return None


if __name__ == "__main__":
    ft.run(main)
