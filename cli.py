"""Menu app for the encrypted vault (replaces the old main.py).

Run:  py cli.py                        -> uses the vault the app last opened
      py cli.py "D:/other/vault.cipher"   -> uses that file instead
(Run it from a real terminal, not PyCharm's Run window, so the password
prompt can hide what you type.)
"""
import os
import sys
from getpass import getpass
from pathlib import Path

from config import load_vault_path
from vault import Vault, VaultError, WrongPasswordError, generate_password


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def line():
    print("_" * 42)


def show(entry, number=None, reveal=False):
    prefix = f"[{number}] " if number is not None else ""
    password = entry["password"] if reveal else "*" * 12
    print(f"{prefix}Login Title : {entry['title']}\n"
          f"Website Url : {entry['url']}\n"
          f"Username    : {entry['username']}\n"
          f"Password    : {password}")
    line()


def wants_reveal():
    return input("Show the passwords (y/n)? ").strip().lower() == "y"


def ask_int(prompt, low, high):
    while True:
        try:
            value = int(input(prompt).strip())
        except ValueError:
            print("Please type a number.")
            continue
        if low <= value <= high:
            return value
        print(f"Please type a number between {low} and {high}.")


def choose_password():
    """Returns a password, or None if the user cancels."""
    line()
    print("1 to type a password\n2 to generate a password\n3 to cancel")
    line()
    option = ask_int("> ", 1, 3)
    if option == 1:
        while True:
            password = getpass("Enter the password: ")
            if not password:
                print("Password can't be empty.")
                continue
            if getpass("Repeat the password: ") == password:
                return password
            print("They didn't match, try again.")
    if option == 2:
        letters, digits, symbols = 12, 7, 7
        custom = input("Choose the number of letters, digits and symbols (y/n)? ")
        if custom.strip().lower() == "y":
            letters = ask_int("Number of letters: ", 0, 100)
            digits = ask_int("Number of digits: ", 0, 100)
            symbols = ask_int("Number of symbols: ", 0, 100)
            if letters + digits + symbols == 0:
                print("Need at least one character, using the defaults.")
                letters, digits, symbols = 12, 7, 7
        while True:
            password = generate_password(letters, digits, symbols)
            print(f"\nGenerated password: {password}")
            print("1 to use it\n2 to generate again\n3 to cancel")
            choice = ask_int("> ", 1, 3)
            if choice == 1:
                return password
            if choice == 3:
                return None
    return None


def add_login(v):
    line()
    title = input("Enter the Login Title: ").strip()
    url = input("Enter the Website Url: ").strip()
    username = input("Enter the Username: ").strip()
    if not title:
        print("Title can't be empty.")
        return
    if v.has_login(title, username):
        print("That login already exists.")
        return
    password = choose_password()
    if password is None:
        print("Cancelled.")
        return
    entry = v.add(title, url, username, password)
    clear()
    line()
    show(entry)
    print("Login saved successfully")


def view_all(v):
    reveal = wants_reveal()
    line()
    entries = v.list_all()
    if not entries:
        print("The vault is empty.")
        return
    for entry in entries:
        show(entry, reveal=reveal)


def search_logins(v):
    text = input("Enter Login title, username or website: ")
    reveal = wants_reveal()
    clear()
    line()
    matches = v.search(text)
    if not matches:
        print("The login is not in the vault.")
        return
    for entry in matches:
        show(entry, reveal=reveal)


def pick(v):
    """Let the user choose one login from a numbered list."""
    text = input("Search text (leave blank to list everything): ")
    matches = v.search(text)
    if not matches:
        print("No logins found.")
        return None
    clear()
    line()
    for number, entry in enumerate(matches, start=1):
        show(entry, number)
    number = ask_int("Number of the login (0 to cancel): ", 0, len(matches))
    return matches[number - 1] if number else None


def edit_login(v):
    entry = pick(v)
    if entry is None:
        return
    print("Press Enter to keep the current value.")
    changes = {}
    for field, label in (("title", "Title"), ("url", "Website Url"),
                         ("username", "Username")):
        new = input(f"{label} [{entry[field]}]: ").strip()
        if new:
            changes[field] = new
    if input("Change the password (y/n)? ").strip().lower() == "y":
        password = choose_password()
        if password is not None:
            changes["password"] = password
    if not changes:
        print("Nothing changed.")
        return
    v.update(entry["id"], **changes)
    print("Login updated.")


def delete_login(v):
    entry = pick(v)
    if entry is None:
        return
    answer = input(f"Delete '{entry['title']}' ({entry['username']}) for good (y/n)? ")
    if answer.strip().lower() == "y":
        v.delete(entry["id"])
        print("Deleted.")
    else:
        print("Not deleted.")


def change_master(v):
    new = getpass("New master password: ")
    if getpass("Repeat it: ") != new:
        print("They didn't match.")
        return
    v.change_master_password(new)
    print("Master password changed.")


def open_or_create(path):
    if path.exists():
        for _ in range(3):
            try:
                return Vault.open(path, getpass("Master password: "))
            except WrongPasswordError as error:
                print(error)
            except (VaultError, OSError) as error:
                sys.exit(f"Can't open the vault: {error}")
        sys.exit("Too many wrong attempts.")
    print(f"No vault found at {path}")
    if input("Create a new one here (y/n)? ").strip().lower() != "y":
        sys.exit(0)
    print("The master password needs 14+ characters with a lowercase letter, an")
    print("uppercase letter, a number and a symbol. A phrase of 20 or more characters")
    print("also works.")
    print("There is NO way to recover the vault if you forget this password.")
    while True:
        first = getpass("Choose a master password: ")
        if getpass("Repeat it: ") != first:
            print("They didn't match.")
            continue
        try:
            return Vault.create(path, first)
        except VaultError as error:
            print(error)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else load_vault_path()
    v = open_or_create(path)
    actions = {1: add_login, 2: view_all, 3: search_logins,
               4: edit_login, 5: delete_login, 6: change_master}
    while True:
        clear()
        line()
        print("1 to Add Login\n2 to View Logins\n3 to Search Login\n"
              "4 to Edit Login\n5 to Delete Login\n"
              "6 to Change Master Password\n7 to Quit")
        line()
        choice = ask_int("> ", 1, 7)
        if choice == 7:
            break
        clear()
        try:
            actions[choice](v)
        except VaultError as error:
            print(error)
        input("\nPress Enter to continue...")
    clear()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nBye")
