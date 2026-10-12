"""Admin command line: python -m mamal_central.cli <command> ..."""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from .config import Settings
from .db import make_engine, make_session_factory
from .models import Membership, Role, Unit, User, Workspace
from .security import hash_password

# Starting point for the Oshnavieh kiosks; targets in toman per day.
MAMAL_KIOSKS = {
    "slug": "mamal-oshnavieh",
    "name": "MAMAL — اشنویه",
    "units": [
        ("MAMAL Bazaar", 3_000_000),
        ("چای‌خانه مامال", 3_000_000),
        ("لبنیات مامال", 3_000_000),
    ],
}


def _read_password(args) -> str:
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            sys.exit("Passwords do not match")
    if len(password) < 10:
        sys.exit("Password must be at least 10 characters")
    return password


def _workspace(db, slug: str) -> Workspace:
    ws = db.scalar(select(Workspace).where(Workspace.slug == slug))
    if not ws:
        sys.exit(f"No workspace '{slug}'")
    return ws


def cmd_create_user(db, args):
    username = args.username.strip().lower()
    if db.scalar(select(User).where(User.username == username)):
        sys.exit(f"User '{username}' already exists")
    db.add(
        User(
            username=username,
            full_name=args.full_name or "",
            password_hash=hash_password(_read_password(args)),
            is_admin=args.admin,
        )
    )
    db.commit()
    print(f"Created user {username}{' (admin)' if args.admin else ''}")


def cmd_set_password(db, args):
    user = db.scalar(select(User).where(User.username == args.username.lower()))
    if not user:
        sys.exit("No such user")
    user.password_hash = hash_password(_read_password(args))
    db.commit()
    print("Password updated")


def cmd_add_workspace(db, args):
    if db.scalar(select(Workspace).where(Workspace.slug == args.slug)):
        sys.exit(f"Workspace '{args.slug}' already exists")
    db.add(Workspace(slug=args.slug, name=args.name))
    db.commit()
    print(f"Created workspace {args.slug}")


def cmd_add_unit(db, args):
    ws = _workspace(db, args.workspace)
    db.add(Unit(workspace_id=ws.id, name=args.name, daily_target=args.target))
    db.commit()
    print(f"Added unit '{args.name}' to {ws.slug}")


def cmd_grant(db, args):
    ws = _workspace(db, args.workspace)
    user = db.scalar(select(User).where(User.username == args.username.lower()))
    if not user:
        sys.exit("No such user")
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws.id)
    )
    if membership:
        membership.role = Role(args.role)
    else:
        db.add(Membership(user_id=user.id, workspace_id=ws.id, role=Role(args.role)))
    db.commit()
    print(f"{user.username} is {args.role} of {ws.slug}")


def cmd_init_mamal(db, _args):
    if db.scalar(select(Workspace).where(Workspace.slug == MAMAL_KIOSKS["slug"])):
        sys.exit("Already initialised")
    ws = Workspace(slug=MAMAL_KIOSKS["slug"], name=MAMAL_KIOSKS["name"])
    ws.units = [Unit(name=n, daily_target=t) for n, t in MAMAL_KIOSKS["units"]]
    db.add(ws)
    db.commit()
    print(f"Created workspace {ws.slug} with {len(ws.units)} units")


def cmd_list(db, _args):
    for ws in db.scalars(select(Workspace).order_by(Workspace.slug)):
        print(f"{ws.slug}  {ws.name}")
        for u in ws.units:
            state = "" if u.active else " (inactive)"
            print(f"  unit #{u.id}  {u.name}  target={u.daily_target:,}{state}")
        for m in ws.memberships:
            print(f"  {m.role.value:8} {m.user.username}")
    admins = db.scalars(select(User).where(User.is_admin)).all()
    print("admins:", ", ".join(u.username for u in admins) or "-")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mamal_central.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user")
    p.add_argument("username")
    p.add_argument("--full-name")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("set-password")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("add-workspace")
    p.add_argument("slug")
    p.add_argument("name")
    p.set_defaults(func=cmd_add_workspace)

    p = sub.add_parser("add-unit")
    p.add_argument("workspace")
    p.add_argument("name")
    p.add_argument("--target", type=int, default=0, help="daily sales target in toman")
    p.set_defaults(func=cmd_add_unit)

    p = sub.add_parser("grant")
    p.add_argument("username")
    p.add_argument("workspace")
    p.add_argument("role", choices=[r.value for r in Role])
    p.set_defaults(func=cmd_grant)

    sub.add_parser("init-mamal").set_defaults(func=cmd_init_mamal)
    sub.add_parser("list").set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    settings = Settings()
    db = make_session_factory(make_engine(settings.database_url))()
    try:
        args.func(db, args)
    finally:
        db.close()


if __name__ == "__main__":
    main()
