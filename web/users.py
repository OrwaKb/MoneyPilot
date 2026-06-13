# web/users.py
"""Account admin:  python -m web.users add|passwd|list|remove <name>"""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from web.auth import UserStore
from web.registry import valid_username

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_USERS = Path(os.environ.get("MP_USERS_DIR",
                                    str(PROJECT_DIR / "users"))) / "users.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m web.users")
    ap.add_argument("action", choices=["add", "passwd", "list", "remove"])
    ap.add_argument("username", nargs="?")
    ap.add_argument("--users", default=str(DEFAULT_USERS),
                    help="path to users.json")
    args = ap.parse_args(argv)
    store = UserStore(args.users)

    if args.action == "list":
        for name in store.list():
            print(name)
        return 0

    if not args.username:
        ap.error(f"{args.action} requires a username")

    if args.action in ("add", "passwd") and not valid_username(args.username):
        ap.error("username must be 1-32 chars of lowercase letters, digits, "
                 "'-' or '_' (so it maps to a safe per-user folder)")

    if args.action == "remove":
        store.remove(args.username)
        print(f"removed {args.username}")
        return 0

    if args.action == "add" and store.exists(args.username):
        ap.error(f"user {args.username!r} already exists (use passwd to reset)")

    pw = getpass.getpass(f"password for {args.username}: ")
    if not pw:
        ap.error("empty password")
    confirm = getpass.getpass("confirm: ")
    if pw != confirm:
        ap.error("passwords do not match")
    store.add(args.username, pw)
    print(f"{'updated' if args.action == 'passwd' else 'added'} {args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
