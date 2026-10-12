"""Public site (mamalco.com): structures with map & photos, shop and order form,
and the invest / rent / job application forms. No login required."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Application,
    CaseKind,
    Category,
    CityPoint,
    Order,
    OrderItem,
    StructurePhoto,
    StructureTier,
    Unit,
    User,
)
from .rules import (
    BUDGET_ITEMS,
    BUILD_DAYS,
    CITY_ROLLOUT,
    EXPANSION_STEPS,
    MIN_CONTRACT_MONTHS,
    RAISE_LABELS,
    SALARY_MAX,
    SALARY_MIN,
    STRUCTURES_PER_CATEGORY,
    investment_rule,
)

PUBLIC_CATEGORIES = [Category.form, Category.village, Category.lodge]
KIND_TITLES = {
    CaseKind.invest: "درخواست سرمایه‌گذاری",
    CaseKind.rent: "درخواست اجاره / بهره‌برداری",
    CaseKind.job: "درخواست همکاری و استخدام",
    CaseKind.general: "تماس با مامال",
}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
PHONE_RE = re.compile(r"^\+?[0-9 ]{7,20}$")
# Max public submissions per client IP per window.
RATE_LIMIT, RATE_WINDOW = 8, 600


class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


def _to_int(value) -> int | None:
    text = str(value or "").translate(_DIGITS).replace(",", "").replace("٬", "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(value)
    return int(text)


def schematic(points: list[CityPoint], width: int = 720, pad: int = 48) -> dict:
    """Place points on an equal-scale lat/lng drawing (no base map needed)."""
    if not points:
        return {"points": [], "width": width, "height": 0}
    import math

    lat0 = sum(p.latitude for p in points) / len(points)
    kx = math.cos(math.radians(lat0))
    xs = [p.longitude * kx for p in points]
    ys = [p.latitude for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1e-6
    scale = (width - 2 * pad - 96) / span  # room on the right for nudged labels
    height = int((max(ys) - min(ys)) * scale + 2 * pad)
    placed = []
    for p, x, y in zip(points, xs, ys):
        px, py = pad + (x - min(xs)) * scale, pad + (max(ys) - y) * scale
        # Nudge sideways until clear of points already drawn (duplicates and near neighbours).
        while any(abs(px - q["x"]) < 24 and abs(py - q["y"]) < 24 for q in placed):
            px += 24
        placed.append({"p": p, "x": round(px, 1), "y": round(py, 1)})
    meters = 111_320 * span
    return {"points": placed, "width": width, "height": height,
            "bar_px": round(500 / meters * (width - 2 * pad), 1)}


def register(app: FastAPI, ctx) -> None:
    get_db, render = ctx.get_db, ctx.render
    limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW)
    app.state.public_rate_limiter = limiter

    def listed_units(db: Session, category: Category | None = None) -> list[Unit]:
        query = (
            select(Unit)
            .where(Unit.is_public, Unit.active, Unit.code.is_not(None))
            .options(selectinload(Unit.photos), selectinload(Unit.products))
            .order_by(Unit.code)
        )
        if category:
            query = query.where(Unit.category == category)
        return [u for u in db.scalars(query) if u.is_listed]

    def tiers_by_category(db: Session) -> dict[Category, StructureTier]:
        return {t.category: t for t in db.scalars(select(StructureTier))}

    def listed_unit(db: Session, code: str) -> Unit:
        unit = db.scalar(select(Unit).where(Unit.code == code.upper()))
        if not unit or not unit.is_listed:
            raise HTTPException(404)
        return unit

    def page(request: Request, name: str, data: dict, status_code: int = 200):
        data.setdefault("public", True)
        data.setdefault("category_order", PUBLIC_CATEGORIES)
        return render(request, name, data, status_code)

    def check_submission(request: Request, form) -> None:
        ctx.require_csrf(request, form.get("csrf"))
        # Honeypot: real people never see or fill this field.
        if form.get("website"):
            raise HTTPException(400)
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            raise HTTPException(429, "Too many requests, try again later")

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, db: Session = Depends(get_db)):
        units = listed_units(db)
        counts = {c: sum(1 for u in units if u.category == c) for c in PUBLIC_CATEGORIES}
        return page(request, "public/home.html", {
            "featured": units[:6], "counts": counts, "tiers": tiers_by_category(db),
            "planned": STRUCTURES_PER_CATEGORY,
        })

    @app.get("/structures", response_class=HTMLResponse)
    def structures(request: Request, category: str | None = None, db: Session = Depends(get_db)):
        try:
            cat = Category(category) if category else None
        except ValueError:
            raise HTTPException(404)
        return page(request, "public/structures.html", {
            "units": listed_units(db, cat), "category": cat, "tiers": tiers_by_category(db),
            "planned": STRUCTURES_PER_CATEGORY,
        })

    @app.get("/s/{code}", response_class=HTMLResponse)
    def structure(request: Request, code: str, db: Session = Depends(get_db)):
        unit = listed_unit(db, code)
        tier = tiers_by_category(db).get(unit.category)
        return page(request, "public/structure.html", {
            "u": unit,
            "products": [p for p in unit.products if p.active],
            "tier": tier,
            "rule": investment_rule(unit.category, tier.min_price if tier else None),
            "progress": unit.build_progress(ctx.today()),
        })

    @app.get("/pilot", response_class=HTMLResponse)
    def pilot(request: Request, db: Session = Depends(get_db)):
        tiers = tiers_by_category(db)
        points = db.scalars(select(CityPoint).order_by(CityPoint.number)).all()
        return page(request, "public/pilot.html", {
            "city_points": points, "schematic": schematic(list(points)),
            "booth_total": sum(p.booths for p in points),
            "tiers": tiers, "rollout": CITY_ROLLOUT, "expansion": EXPANSION_STEPS,
            "rules": {c: investment_rule(c, tiers[c].min_price if c in tiers else None)
                      for c in PUBLIC_CATEGORIES},
            "salary_min": SALARY_MIN, "salary_max": SALARY_MAX, "min_months": MIN_CONTRACT_MONTHS,
            "raise_labels": list(RAISE_LABELS.values()), "build_days": BUILD_DAYS,
            "budget_items": BUDGET_ITEMS,
        })

    @app.get("/map", response_class=HTMLResponse)
    def public_map(request: Request, db: Session = Depends(get_db)):
        points = [
            {
                "lat": u.latitude, "lng": u.longitude, "code": u.code, "name": u.name,
                "category": u.category.value, "status": u.status.value,
                "url": f"/s/{u.code.lower()}", "public": True,
            }
            for u in listed_units(db)
        ]
        city = [
            {"lat": p.latitude, "lng": p.longitude, "number": p.number, "name": p.name,
             "booths": p.booths, "trades": p.trades}
            for p in db.scalars(select(CityPoint).order_by(CityPoint.number))
        ]
        return page(request, "map.html", {"points": points, "sites": [], "city": city,
                                          "admin_view": False})

    @app.get("/invest", response_class=HTMLResponse)
    def invest(request: Request, db: Session = Depends(get_db)):
        tiers = tiers_by_category(db)
        rows = [
            (c, tiers.get(c), investment_rule(c, tiers[c].min_price if c in tiers else None))
            for c in PUBLIC_CATEGORIES
        ]
        return page(request, "public/invest.html", {
            "rows": rows, "budget_items": BUDGET_ITEMS, "build_days": BUILD_DAYS,
        })

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, db: Session = Depends(get_db)):
        units = [u for u in listed_units(db) if u.open_for_jobs]
        return page(request, "public/jobs.html", {
            "units": units, "salary_min": SALARY_MIN, "salary_max": SALARY_MAX,
            "raise_labels": list(RAISE_LABELS.values()), "min_months": MIN_CONTRACT_MONTHS,
        })

    @app.get("/shop", response_class=HTMLResponse)
    def shop(request: Request, db: Session = Depends(get_db)):
        units = [u for u in listed_units(db) if any(p.active and p.sold_online for p in u.products)]
        return page(request, "public/shop.html", {"units": units})

    @app.get("/photos/{photo_id}")
    def photo(request: Request, photo_id: int, db: Session = Depends(get_db)):
        p = db.get(StructurePhoto, photo_id)
        if not p:
            raise HTTPException(404)
        if not p.unit.is_listed:
            # Unpublished structures: only signed-in staff with access.
            user_id = request.session.get("user_id")
            user = db.get(User, user_id) if user_id else None
            if not user:
                raise HTTPException(404)
            workspace, _ = ctx.load_workspace(db, user, p.unit.workspace.slug)
            ctx.get_unit(workspace, p.unit_id, user)
        path = ctx.settings.upload_dir / p.stored_name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(
            path, media_type=p.content_type,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=86400"},
        )

    # ------------------------------------------------------------ applications

    def parse_kind(kind: str) -> CaseKind:
        try:
            return CaseKind(kind)
        except ValueError:
            raise HTTPException(404)

    def form_context(kind: CaseKind, unit: Unit | None, category: Category | None, db, **extra):
        tiers = tiers_by_category(db)
        cat = unit.category if unit else category
        tier = tiers.get(cat) if cat else None
        rule = investment_rule(cat, tier.min_price if tier else None) if cat else None
        return {
            "kind": kind, "title": KIND_TITLES[kind], "u": unit, "category": cat,
            "tier": tier, "rule": rule, "categories": PUBLIC_CATEGORIES,
            "values": {}, "errors": [], **extra,
        }

    @app.get("/apply/{kind}", response_class=HTMLResponse)
    def apply_form(
        request: Request, kind: str, category: str | None = None, s: str | None = None,
        db: Session = Depends(get_db),
    ):
        k = parse_kind(kind)
        unit = listed_unit(db, s) if s else None
        try:
            cat = Category(category) if category else None
        except ValueError:
            cat = None
        return page(request, "public/apply.html", form_context(k, unit, cat, db))

    @app.post("/apply/{kind}", response_class=HTMLResponse)
    async def apply_submit(request: Request, kind: str, db: Session = Depends(get_db)):
        k = parse_kind(kind)
        form = await request.form()
        check_submission(request, form)
        unit = listed_unit(db, str(form.get("s"))) if form.get("s") else None
        try:
            cat = unit.category if unit else (
                Category(str(form.get("category"))) if form.get("category") else None
            )
        except ValueError:
            cat = None

        values = {key: str(form.get(key) or "").strip() for key in (
            "full_name", "phone", "email", "city", "message", "cash_amount",
            "cheque_amount", "desired_role", "experience",
        )}
        errors = []
        phone = values["phone"].translate(_DIGITS)
        if len(values["full_name"]) < 3:
            errors.append("نام و نام خانوادگی را کامل بنویسید.")
        if not PHONE_RE.match(phone):
            errors.append("شماره تماس معتبر نیست.")
        if values["email"] and "@" not in values["email"]:
            errors.append("ایمیل معتبر نیست.")
        try:
            cash = _to_int(values["cash_amount"])
            cheque = _to_int(values["cheque_amount"])
        except ValueError:
            errors.append("مبلغ را فقط با عدد بنویسید.")
            cash = cheque = None
        if k == CaseKind.invest and not cash:
            errors.append("مبلغ نقدی آماده را وارد کنید.")
        if k == CaseKind.invest and not cat:
            errors.append("نوع سازه را انتخاب کنید.")
        if k == CaseKind.job and not values["desired_role"]:
            errors.append("سمت مورد نظر را بنویسید.")
        if any(len(v) > 4000 for v in values.values()):
            errors.append("متن خیلی طولانی است.")
        if errors:
            ctx_data = form_context(k, unit, cat, db, values=values, errors=errors)
            return page(request, "public/apply.html", ctx_data, status_code=400)

        case = Application(
            kind=k, unit_id=unit.id if unit else None, category=cat,
            full_name=values["full_name"][:200], phone=phone[:40],
            email=values["email"][:200], city=values["city"][:100],
            message=values["message"], cash_amount=cash, cheque_amount=cheque,
            desired_role=values["desired_role"][:200], experience=values["experience"],
        )
        db.add(case)
        db.commit()
        db.refresh(case)
        request.session["last_ref"] = case.case_id
        return RedirectResponse(f"/thanks?ref={case.case_id}", status_code=303)

    # ------------------------------------------------------------------ orders

    @app.post("/s/{code}/order", response_class=HTMLResponse)
    async def order_submit(request: Request, code: str, db: Session = Depends(get_db)):
        unit = listed_unit(db, code)
        form = await request.form()
        check_submission(request, form)
        products = {p.id: p for p in unit.products if p.active and p.sold_online}
        items, errors = [], []
        for pid, product in products.items():
            try:
                qty = _to_int(form.get(f"qty_{pid}"))
            except ValueError:
                qty = None
                errors.append(f"تعداد «{product.name}» معتبر نیست.")
            if qty:
                if qty > 10_000:
                    errors.append(f"تعداد «{product.name}» خیلی زیاد است.")
                    continue
                price = product.price_for(qty)
                items.append(OrderItem(
                    product_id=pid, product_name=product.name, unit_label=product.unit_label,
                    qty=qty, unit_price=price, wholesale=price != product.retail_price,
                ))
        name = str(form.get("full_name") or "").strip()
        phone = str(form.get("phone") or "").translate(_DIGITS).strip()
        delivery = form.get("delivery") == "1"
        address = str(form.get("address") or "").strip()
        if not items:
            errors.append("حداقل یک محصول انتخاب کنید.")
        if len(name) < 3:
            errors.append("نام را کامل بنویسید.")
        if not PHONE_RE.match(phone):
            errors.append("شماره تماس معتبر نیست.")
        if delivery and len(address) < 10:
            errors.append("برای ارسال، آدرس کامل لازم است.")
        if errors:
            tier = tiers_by_category(db).get(unit.category)
            return page(request, "public/structure.html", {
                "u": unit, "products": [p for p in unit.products if p.active], "tier": tier,
                "rule": investment_rule(unit.category, tier.min_price if tier else None),
                "progress": unit.build_progress(ctx.today()),
                "order_errors": errors, "order_values": dict(form),
            }, status_code=400)
        order = Order(
            unit_id=unit.id, full_name=name[:200], phone=phone[:40], delivery=delivery,
            address=address[:1000], note=str(form.get("note") or "").strip()[:1000], items=items,
        )
        db.add(order)
        db.commit()
        return RedirectResponse(f"/thanks?ref={order.order_code}&s={unit.code}", status_code=303)

    @app.get("/thanks", response_class=HTMLResponse)
    def thanks(request: Request, ref: str = "", s: str = "", db: Session = Depends(get_db)):
        if not re.fullmatch(r"M[CO]-[0-9-]{4,12}", ref):
            return RedirectResponse("/", status_code=303)
        unit = db.scalar(select(Unit).where(Unit.code == s.upper())) if s else None
        return page(request, "public/thanks.html", {
            "ref": ref, "is_order": ref.startswith("MO-"),
            "phone": unit.phone if unit and unit.is_listed else "",
        })
