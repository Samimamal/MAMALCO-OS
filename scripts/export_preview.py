"""Export the public site with sample data to static HTML for a shareable preview.

    python scripts/export_preview.py OUT_DIR

Sample structures get placeholder GPS points in Oshnavieh and the brand image
as their photo; every page carries a banner saying the data is a sample.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MAMAL_PREVIEW_BANNER"] = (
    "پیش‌نمایش سایت mamalco.com با داده‌ی نمونه — موقعیت‌ها، قیمت‌ها و محصولات واقعی نیستند؛ فرم‌ها در این نسخه ارسال نمی‌شوند."
)

from fastapi.testclient import TestClient  # noqa: E402

from mamal_central import cli  # noqa: E402
from mamal_central.config import Settings  # noqa: E402
from mamal_central.main import create_app  # noqa: E402
from mamal_central.models import Product, StructurePhoto, StructureStatus, Unit  # noqa: E402

# (code, name, branch, tagline, lat, lng) — placeholder points around central Oshnavieh.
SAMPLES = [
    ("F-01", "MAMAL Bazaar", "بازار محصولات محلی", "محصولات محلی مامال در یک دکه", 37.0402, 45.0990),
    ("F-02", "چای‌خانه مامال", "چای‌خانه", "طعم اصیل چای ایرانی", 37.0395, 45.1012),
    ("F-03", "لبنیات مامال", "لبنیات", "لبنیات تازه و طبیعی از دامداری‌های مامال", 37.0418, 45.1030),
]
PRODUCTS = {
    "F-02": [
        ("چای سیاه ایرانی (نمونه)", "کیلو", 850_000, 720_000, 10, True, False),
        ("قند (نمونه)", "کیلو", 110_000, 95_000, 20, True, False),
        ("چای در محل یا فلاسک (نمونه)", "لیوان", 25_000, None, None, False, True),
    ],
    "F-03": [
        ("ماست محلی (نمونه)", "کیلو", 120_000, 100_000, 10, True, False),
        ("شیر تازه (نمونه)", "لیتر", 60_000, None, None, True, False),
    ],
    "F-01": [("عسل محلی (نمونه)", "کیلو", 1_200_000, 1_050_000, 5, True, False)],
}


def build_app(data_dir: Path):
    os.environ["MAMAL_DATA_DIR"] = str(data_dir)
    settings = Settings(data_dir=data_dir, secret_key="preview")
    app = create_app(settings)
    db = app.state.session_factory()
    cli.cmd_init_structures(db, type("A", (), {"count": 10})())
    cli.cmd_init_catalog(db, None)
    cli.cmd_import_city_points(db, type("A", (), {"file": None, "source": None})())
    brand = ROOT / "mamal_central/static/img/mamal-brands.jpg"
    for code, name, branch, tagline, lat, lng in SAMPLES:
        u = db.query(Unit).filter_by(code=code).one()
        u.name, u.branch, u.tagline = name, branch, tagline
        u.latitude, u.longitude, u.is_public = lat, lng, True
        u.status = StructureStatus.operating
        u.address = "اشنویه (موقعیت نمونه)"
        stored = f"preview-{code.lower()}.jpg"
        shutil.copy(brand, settings.upload_dir / stored)
        db.add(StructurePhoto(unit_id=u.id, stored_name=stored, content_type="image/jpeg",
                              caption="طرح نمونه‌ی دکه‌های مامال"))
        for i, (pname, label, retail, whole, wmin, online, onsite) in enumerate(PRODUCTS.get(code, [])):
            db.add(Product(unit_id=u.id, name=pname, unit_label=label, retail_price=retail,
                           wholesale_price=whole, wholesale_min_qty=wmin, sold_online=online,
                           served_on_site=onsite, sort_order=i))
    db.commit()
    db.close()
    return app


PAGES = {
    "/": "index.html",
    "/pilot": "pilot.html",
    "/structures": "structures.html",
    "/structures?category=form": "structures-form.html",
    "/structures?category=village": "structures-village.html",
    "/structures?category=lodge": "structures-lodge.html",
    "/s/f-01": "s-f-01.html",
    "/s/f-02": "s-f-02.html",
    "/s/f-03": "s-f-03.html",
    "/invest": "invest.html",
    "/jobs": "jobs.html",
    "/shop": "shop.html",
    "/map": "map.html",
    "/apply/invest": "apply-invest.html",
    "/apply/rent": "apply-rent.html",
    "/apply/job": "apply-job.html",
    "/apply/general": "apply-general.html",
}


def rewrite(html: str) -> str:
    def link(match: re.Match) -> str:
        attr, url = match.group(1), match.group(2)
        path, _, frag = url.partition("#")
        frag = f"#{frag}" if frag else ""
        if path.startswith("/static/"):
            return f'{attr}="{path[1:]}{frag}"'
        if path.startswith("/photos/"):
            return f'{attr}="photos/{path.split("/")[2]}.jpg"'
        if path in PAGES:
            return f'{attr}="{PAGES[path]}{frag}"'
        base = path.split("?")[0]
        if base.startswith("/apply/") and base in PAGES:
            return f'{attr}="{PAGES[base]}{frag}"'
        if path == "" and frag:
            return f'{attr}="{frag}"'
        if path.startswith("/"):
            return f'{attr}="#"'
        return match.group(0)

    html = re.sub(r'(href|src|action)="([^"]*)"', link, html)
    # Map embeds from other sites are blocked inside an artifact.
    html = re.sub(
        r'<iframe class="map"[^>]*></iframe>',
        '<div class="map map-placeholder">نقشه‌ی تعاملی در سایت اصلی نمایش داده می‌شود</div>',
        html, flags=re.S,
    )
    # Forms do nothing in the static preview.
    return html.replace("<form ", '<form onsubmit="return false" ')


def as_artifact_page(html: str) -> str:
    """Strip the document skeleton: the artifact host wraps the main page itself."""
    html = re.sub(r"<title>.*?</title>", "<title>پیش‌نمایش سایت مامال</title>", html, flags=re.S)
    html = re.sub(r"<!doctype html>|</?html[^>]*>|</?head>|</?body[^>]*>", "", html, flags=re.I)
    html = re.sub(r'<meta (charset|name="viewport")[^>]*>', "", html)
    head, sep, rest = html.partition('<div class="preview-banner">')
    return f'{head}<div dir="rtl" lang="fa">{sep}{rest}</div>'



def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        app = build_app(Path(tmp))
        client = TestClient(app)
        for url, name in PAGES.items():
            resp = client.get(url)
            resp.raise_for_status()
            (out / name).write_text(rewrite(resp.text), encoding="utf-8")
        index = out / "index.html"
        index.write_text(as_artifact_page(index.read_text(encoding="utf-8")), encoding="utf-8")
        photos = out / "photos"
        photos.mkdir(exist_ok=True)
        db = app.state.session_factory()
        for p in db.query(StructurePhoto):
            shutil.copy(Path(tmp) / "uploads" / p.stored_name, photos / f"{p.id}.jpg")
        db.close()
    static = out / "static"
    if static.exists():
        shutil.rmtree(static)
    shutil.copytree(ROOT / "mamal_central/static", static)
    print(f"Exported {len(PAGES)} pages to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "preview"))
