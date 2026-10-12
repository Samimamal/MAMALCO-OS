"""Admin command line: python -m mamal_central.cli <command> ..."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import sys

from sqlalchemy import select

from .config import Settings
from .db import make_engine, make_session_factory
import csv
import json
from pathlib import Path

from .geo import OSHNAVIEH_UTM_ZONE, parse_kml, shoelace_area, utm_to_latlng
from .models import (
    Category,
    CityPoint,
    LandStatus,
    Site,
    Membership,
    Role,
    StructureStatus,
    StructureTier,
    Unit,
    User,
    Workspace,
)
from .rules import CATEGORY_LABELS, CATEGORY_PREFIX, STRUCTURES_PER_CATEGORY
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

# Price list (toman), all-in until the unit is operating. Final price depends on
# size, material, site, and inflation; update weekly from the /catalog page.
STRUCTURE_TIERS = [
    {
        "category": Category.form,
        "name": "سازه‌های فرم‌دار مامال",
        "examples": "کیوسک، چای‌خانه، کافه، اتوبوس‌کافه، لبنیاتی",
        "includes": "سازه، تجهیزات، مارکتینگ و راه‌اندازی تا بهره‌برداری",
        "min_price": 1_500_000_000,
        "max_price": 3_000_000_000,
    },
    {
        "category": Category.village,
        "name": "سازه‌های دهکده مامال",
        "examples": "واحدهای اقامتی و خدماتی دهکده",
        "includes": "سازه، تجهیزات و راه‌اندازی تا بهره‌برداری",
        "min_price": 3_000_000_000,
        "max_price": 6_000_000_000,
    },
    {
        "category": Category.lodge,
        "name": "اقامتگاه‌های مامال",
        "examples": "اقامتگاه کامل مامال",
        "includes": "سازه، تجهیزات، مارکتینگ و راه‌اندازی تا بهره‌برداری",
        "min_price": 6_000_000_000,
        "max_price": 10_000_000_000,
    },
]


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
    unit_id = None
    if args.unit:
        unit = db.scalar(select(Unit).where(Unit.code == args.unit.upper()))
        if not unit or unit.workspace_id != ws.id:
            sys.exit(f"No structure {args.unit} in {ws.slug}")
        unit_id = unit.id
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws.id)
    )
    if membership:
        membership.role, membership.unit_id = Role(args.role), unit_id
    else:
        db.add(Membership(user_id=user.id, workspace_id=ws.id, role=Role(args.role), unit_id=unit_id))
    db.commit()
    scope = f" (only {args.unit.upper()})" if unit_id else ""
    print(f"{user.username} is {args.role} of {ws.slug}{scope}")


def cmd_init_mamal(db, _args):
    if db.scalar(select(Workspace).where(Workspace.slug == MAMAL_KIOSKS["slug"])):
        sys.exit("Already initialised")
    ws = Workspace(slug=MAMAL_KIOSKS["slug"], name=MAMAL_KIOSKS["name"])
    ws.units = [Unit(name=n, daily_target=t) for n, t in MAMAL_KIOSKS["units"]]
    db.add(ws)
    db.commit()
    print(f"Created workspace {ws.slug} with {len(ws.units)} units")


STRUCTURE_WORKSPACES = {
    Category.form: ("mamal-form", "سازه‌های فرم‌دار مامال", "سازه‌ی فرم‌دار مامال"),
    Category.village: ("mamal-village", "دهکده مامال", "سازه‌ی دهکده مامال"),
    Category.lodge: ("mamal-lodges", "اقامتگاه‌های مامال", "اقامتگاه مامال"),
}
# Units already operating get the first form codes.
EXISTING_FORM_UNITS = [
    ("MAMAL Bazaar", "بازار"),
    ("چای‌خانه مامال", "چای‌خانه"),
    ("لبنیات مامال", "لبنیات"),
]


def cmd_init_structures(db, args):
    """Create one workspace per category with N numbered structure slots."""
    count = args.count
    created = 0
    for category, (slug, ws_name, unit_name) in STRUCTURE_WORKSPACES.items():
        ws = db.scalar(select(Workspace).where(Workspace.slug == slug))
        if ws is None:
            ws = Workspace(slug=slug, name=ws_name)
            db.add(ws)
            db.flush()
        prefix = CATEGORY_PREFIX[category]
        for i in range(1, count + 1):
            code = f"{prefix}-{i:02d}"
            if db.scalar(select(Unit).where(Unit.code == code)):
                continue
            name, branch, status, target = f"{unit_name} {code}", "", StructureStatus.planned, 0
            if category == Category.form and i <= len(EXISTING_FORM_UNITS):
                name, branch = EXISTING_FORM_UNITS[i - 1]
                status, target = StructureStatus.operating, 3_000_000
            db.add(Unit(
                workspace_id=ws.id, name=name, code=code, category=category, branch=branch,
                status=status, daily_target=target,
            ))
            created += 1
    db.commit()
    print(f"Created {created} structures ({', '.join(CATEGORY_LABELS[c] for c in STRUCTURE_WORKSPACES)})")


# Survey sketch (کروکی) of the village land, multi-frequency GPS, UTM zone 38N.
# Part of plot 4 subsidiary of 75 main, Qala Tuk; owner: National Land and Housing
# Organization. Declared area 13,500 m². The p1->p2 side follows the curved access
# road (32.9 + 28.2 + 44.3 + 47.8 + 26.0 m); its intermediate points are not in
# the table, so the straight-chord polygon below is larger than the declared area.
VILLAGE_SITE = {
    "code": "SITE-VILLAGE-QT",
    "name": "زمین دهکده مامال — قلاتوک",
    "category": Category.village,
    "owner": "سازمان ملی زمین و مسکن (قسمتی از پلاک ۴ فرعی از ۷۵ اصلی قلعه توک)",
    "declared_area_m2": 13_500,
    "points": [
        ("p1", 507555.368, 4100021.290),
        ("p2", 507664.383, 4099970.002),
        ("p3", 507630.616, 4099882.568),
        ("p4", 507603.654, 4099929.311),
        ("p5", 507546.796, 4099955.429),
        ("p6", 507513.870, 4099864.475),
        ("p7", 507411.830, 4099932.980),
    ],
    "source_note": (
        "کروکی نقشه‌برداری با GPS مولتی‌فرکانس، بر اساس درخواست مالک؛ سند مالکیت یا قرارداد واگذاری نیست. "
        "ضلع p1 تا p2 در امتداد راه دسترسی قوسی است و نقاط میانی آن در جدول نیامده؛ "
        "برای مرز دقیق، فایل نقاط کامل (DWG/KML) از نقشه‌بردار گرفته شود."
    ),
}


def cmd_init_village_site(db, _args):
    if db.scalar(select(Site).where(Site.code == VILLAGE_SITE["code"])):
        sys.exit("Village site already exists")
    pts = VILLAGE_SITE["points"]
    polygon = [utm_to_latlng(x, y, OSHNAVIEH_UTM_ZONE) for _, x, y in pts]
    site = Site(
        code=VILLAGE_SITE["code"], name=VILLAGE_SITE["name"], category=VILLAGE_SITE["category"],
        owner=VILLAGE_SITE["owner"], land_status=LandStatus.sketch,
        declared_area_m2=VILLAGE_SITE["declared_area_m2"],
        polygon_json=json.dumps(polygon),
        source_coordinates="\n".join(f"{n},{x},{y}" for n, x, y in pts) + "\nUTM zone 38N",
        source_note=VILLAGE_SITE["source_note"],
    )
    db.add(site)
    db.flush()
    for u in db.scalars(select(Unit).where(Unit.category == Category.village)):
        if u.site_id is None:
            u.site_id = site.id
    db.commit()
    straight = shoelace_area([(x, y) for _, x, y in pts])
    print(f"Created {site.code}: declared 13,500 m², straight-chord polygon {straight:,.0f} m²")


POINTS_FILE = Path(__file__).parent / "seed" / "oshnavieh_points_1405.csv"
POINTS_SOURCE = "صورتجلسه‌ی تأییدیه‌ی جانمایی دکه‌های دهکده مامال، ۱۴۰۵/۰۲/۲۱ (شماره ۱۷۱۴)"


def cmd_import_city_points(db, args):
    """Approved kiosk locations (number,name,lat,lng,booths,trades) -> map."""
    path = Path(args.file) if args.file else POINTS_FILE
    count = 0
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            number = int(row["number"])
            point = db.scalar(select(CityPoint).where(CityPoint.number == number)) or CityPoint(number=number)
            point.name = row["name"].strip()
            point.latitude, point.longitude = float(row["lat"]), float(row["lng"])
            if not (36.9 < point.latitude < 37.2 and 44.9 < point.longitude < 45.3):
                sys.exit(f"Point {number} is outside Oshnavieh: {point.latitude},{point.longitude}")
            point.booths = int(row["booths"])
            point.trades = row.get("trades", "").strip()
            point.source = args.source or POINTS_SOURCE
            db.add(point)
            count += 1
    db.commit()
    total = sum(p.booths for p in db.scalars(select(CityPoint)))
    print(f"Imported {count} points, {total} booths in total")


def cmd_import_kml(db, args):
    """Google Earth KML -> a Site polygon (lodge / village plot)."""
    with open(args.file, encoding="utf-8") as fh:
        marks = parse_kml(fh.read())
    polygons = [m for m in marks if m["type"] == "polygon"]
    if not polygons:
        sys.exit("No polygon in KML")
    poly = polygons[0]["coords"]
    site = db.scalar(select(Site).where(Site.code == args.code))
    if site is None:
        site = Site(code=args.code, name=args.name or polygons[0]["name"] or args.code)
        db.add(site)
    site.category = Category(args.category)
    if args.name:
        site.name = args.name
    if args.declared_area:
        site.declared_area_m2 = args.declared_area
    if args.owner:
        site.owner = args.owner
    site.polygon_json = json.dumps(poly)
    site.source_note = f"Imported from {args.file}"
    db.commit()
    print(f"{site.code}: {len(poly)} points, ~{site.computed_area_m2:,} m²")


def cmd_import_points(db, args):
    """CSV with code + (x, y UTM) or (lat, lng) -> structure locations."""
    updated = missing = 0
    with open(args.file, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            code = (row.get("code") or "").strip().upper()
            unit = db.scalar(select(Unit).where(Unit.code == code))
            if not unit:
                missing += 1
                print(f"  skip: no structure {code!r}")
                continue
            if row.get("x") and row.get("y"):
                lat, lng = utm_to_latlng(float(row["x"]), float(row["y"]), args.zone)
            else:
                lat, lng = float(row["lat"]), float(row["lng"])
            if not (24 < lat < 40 and 44 < lng < 64):
                sys.exit(f"{code}: {lat},{lng} is outside Iran — check the columns/zone")
            unit.latitude, unit.longitude = lat, lng
            if row.get("address"):
                unit.address = row["address"].strip()
            updated += 1
    db.commit()
    print(f"Updated {updated} structures, {missing} skipped")


def cmd_init_catalog(db, _args):
    if db.scalar(select(StructureTier)):
        sys.exit("Catalog already exists; edit prices on the /catalog page")
    for i, tier in enumerate(STRUCTURE_TIERS):
        db.add(StructureTier(**tier, sort_order=i, price_updated_on=dt.date.today()))
    db.commit()
    print(f"Created {len(STRUCTURE_TIERS)} structure tiers")


def cmd_list(db, _args):
    for ws in db.scalars(select(Workspace).order_by(Workspace.slug)):
        print(f"{ws.slug}  {ws.name}")
        for u in ws.units:
            state = "" if u.active else " (inactive)"
            print(f"  {u.code or '#' + str(u.id):6} {u.name}  target={u.daily_target:,}{state}")
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
    p.add_argument("--unit", help="limit access to one structure code, e.g. F-02")
    p.set_defaults(func=cmd_grant)

    sub.add_parser("init-mamal").set_defaults(func=cmd_init_mamal)
    sub.add_parser("init-catalog").set_defaults(func=cmd_init_catalog)

    sub.add_parser("init-village-site").set_defaults(func=cmd_init_village_site)

    p = sub.add_parser("import-city-points", help="approved kiosk locations CSV -> map")
    p.add_argument("file", nargs="?", help="defaults to the 1405 site-approval table")
    p.add_argument("--source")
    p.set_defaults(func=cmd_import_city_points)

    p = sub.add_parser("import-kml", help="Google Earth polygon -> land site")
    p.add_argument("file")
    p.add_argument("--code", required=True, help="e.g. SITE-LODGE-01")
    p.add_argument("--name")
    p.add_argument("--category", choices=[c.value for c in Category], default="lodge")
    p.add_argument("--declared-area", type=int)
    p.add_argument("--owner")
    p.set_defaults(func=cmd_import_kml)

    p = sub.add_parser("import-points", help="CSV code,x,y (UTM) or code,lat,lng -> structures")
    p.add_argument("file")
    p.add_argument("--zone", type=int, default=OSHNAVIEH_UTM_ZONE)
    p.set_defaults(func=cmd_import_points)

    p = sub.add_parser("init-structures")
    p.add_argument("--count", type=int, default=STRUCTURES_PER_CATEGORY)
    p.set_defaults(func=cmd_init_structures)
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
