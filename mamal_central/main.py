"""MAMAL Central web app: login, workspace dashboards and the daily unit report."""

from __future__ import annotations

import datetime as dt
import mimetypes
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import jalali
from .config import Settings
from .dashboard import build_dashboard
from .db import make_engine, make_session_factory
from .models import (
    Attachment,
    ContractStatus,
    DailyReport,
    MonthlyCost,
    PartnershipContract,
    Role,
    StructureTier,
    Unit,
    User,
    Workspace,
)
from . import admin_ops, public
from .rules import CATEGORY_LABELS, RAISE_LABELS
from .security import csrf_ok, csrf_token, verify_password
from .settlement import compute_statement, load_month_inputs

BASE_DIR = Path(__file__).parent
ALLOWED_UPLOADS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".pdf": "application/pdf",
}
MAX_FILES_PER_SUBMIT = 10
# Staff may submit or correct reports only for today and this many days back.
STAFF_EDIT_WINDOW_DAYS = 1

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["jdate"] = jalali.format_jalali
templates.env.filters["jdate_long"] = jalali.format_jalali_long
templates.env.filters["money"] = lambda v: jalali.persian_digits(f"{int(v or 0):,}").replace(
    ",", "٬"
)
templates.env.filters["fa"] = jalali.persian_digits
templates.env.filters["jmonth"] = lambda ym: jalali.format_month(*ym)


def billions(value) -> str:
    """3_000_000_000 -> '۳ میلیارد', 1_500_000_000 -> '۱٫۵ میلیارد'."""
    text = f"{value / 1_000_000_000:.2f}".rstrip("0").rstrip(".")
    return jalali.persian_digits(text).replace(".", "٫") + " میلیارد"


templates.env.filters["billions"] = billions
STATUS_LABELS = {"draft": "پیش‌نویس", "active": "فعال", "ended": "پایان‌یافته"}
templates.env.globals["status_labels"] = STATUS_LABELS
templates.env.globals["category_labels"] = {k.value: v for k, v in CATEGORY_LABELS.items()}
templates.env.globals["raise_labels"] = {k.value: v for k, v in RAISE_LABELS.items()}
templates.env.globals["structure_status_labels"] = {
    "planned": "در برنامه", "building": "در حال ساخت", "operating": "فعال",
}
templates.env.globals["case_kind_labels"] = {
    "invest": "سرمایه‌گذاری", "rent": "اجاره / بهره‌برداری", "job": "استخدام", "general": "عمومی",
}
templates.env.globals["case_status_labels"] = {
    "new": "جدید", "contacted": "تماس گرفته شد", "qualified": "واجد شرایط",
    "rejected": "رد شد", "converted": "قرارداد شد",
}
templates.env.globals["land_status_labels"] = {
    "sketch": "فقط کروکی", "negotiating": "در حال مذاکره",
    "agreement": "قرارداد واگذاری امضا شده", "permit": "پروانه ساخت صادر شده",
}
templates.env.globals["order_status_labels"] = {
    "new": "جدید", "confirmed": "تأیید شد", "delivered": "تحویل شد", "cancelled": "لغو شد",
}
# Shown on every public page when set (static previews with sample data).
templates.env.globals["preview_banner"] = os.environ.get("MAMAL_PREVIEW_BANNER", "")
IMAGE_UPLOADS = {k: v for k, v in ALLOWED_UPLOADS.items() if v.startswith("image/")}


class NotLoggedIn(Exception):
    pass


def create_app(settings: Settings | None = None, today=dt.date.today) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    SessionLocal = make_session_factory(engine)

    app = FastAPI(title="MAMAL Central", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.session_factory = SessionLocal
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="mamal_session",
        same_site="lax",
        https_only=False,
        max_age=14 * 24 * 3600,
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def current_user(request: Request, db: Session = Depends(get_db)) -> User:
        user_id = request.session.get("user_id")
        user = db.get(User, user_id) if user_id else None
        if not user or not user.active:
            raise NotLoggedIn()
        return user

    @app.exception_handler(NotLoggedIn)
    async def _redirect_to_login(request: Request, _exc: NotLoggedIn):
        return RedirectResponse("/login", status_code=303)

    def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
        ctx = dict(ctx or {})
        ctx.setdefault("user", None)
        ctx["csrf"] = csrf_token(request.session)
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def require_csrf(request: Request, token: str | None):
        if not csrf_ok(request.session, token):
            raise HTTPException(403, "CSRF token invalid")

    def role_in(user: User, workspace: Workspace) -> str | None:
        if user.is_admin:
            return "admin"
        for m in user.memberships:
            if m.workspace_id == workspace.id:
                return m.role.value
        return None

    def units_for(user: User, workspace: Workspace) -> list[Unit]:
        """Structures this user may see inside the workspace."""
        units = [u for u in workspace.units if u.active]
        if user.is_admin:
            return units
        for m in user.memberships:
            if m.workspace_id == workspace.id and m.unit_id is not None:
                return [u for u in units if u.id == m.unit_id]
        return units

    def visible_workspaces(db: Session, user: User) -> list[Workspace]:
        if user.is_admin:
            return list(db.scalars(select(Workspace).order_by(Workspace.name)))
        return sorted((m.workspace for m in user.memberships), key=lambda w: w.name)

    def load_workspace(db: Session, user: User, slug: str) -> tuple[Workspace, str]:
        workspace = db.scalar(select(Workspace).where(Workspace.slug == slug))
        role = role_in(user, workspace) if workspace else None
        if not workspace or not role:
            # Same response for "missing" and "forbidden" so slugs can't be probed.
            raise HTTPException(404)
        return workspace, role

    def can_edit_date(role: str, date: dt.date) -> bool:
        if date > today():
            return False
        if role == Role.staff.value:
            return (today() - date).days <= STAFF_EDIT_WINDOW_DAYS
        return True

    # ---------------------------------------------------------------- auth

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html")

    @app.post("/login")
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf: str = Form(""),
        db: Session = Depends(get_db),
    ):
        require_csrf(request, csrf)
        user = db.scalar(select(User).where(User.username == username.strip().lower()))
        if not user or not user.active or not verify_password(password, user.password_hash):
            return render(
                request, "login.html", {"error": "نام کاربری یا رمز اشتباه است."}, 401
            )
        request.session.clear()
        request.session["user_id"] = user.id
        return RedirectResponse("/admin", status_code=303)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        require_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---------------------------------------------------------- workspaces

    @app.get("/admin", response_class=HTMLResponse)
    def home(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        workspaces = visible_workspaces(db, user)
        if len(workspaces) == 1:
            return RedirectResponse(f"/w/{workspaces[0].slug}", status_code=303)
        return render(request, "home.html", {"user": user, "workspaces": workspaces})

    @app.get("/w/{slug}", response_class=HTMLResponse)
    def workspace_home(
        request: Request,
        slug: str,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        workspace, role = load_workspace(db, user, slug)
        if role == Role.staff.value:
            return RedirectResponse(f"/w/{slug}/report", status_code=303)
        summaries = build_dashboard(db, units_for(user, workspace), today(), settings.dashboard_days)
        return render(
            request,
            "dashboard.html",
            {
                "user": user,
                "workspace": workspace,
                "role": role,
                "summaries": summaries,
                "days": settings.dashboard_days,
            },
        )

    # ------------------------------------------------------- daily report

    def parse_date(value: str | None) -> dt.date:
        if not value:
            return today()
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            raise HTTPException(400, "Invalid date")

    def get_unit(workspace: Workspace, unit_id: int | None, user: User) -> Unit:
        units = units_for(user, workspace)
        if not units:
            raise HTTPException(404, "This workspace has no units yet")
        if unit_id is None:
            return units[0]
        for u in units:
            if u.id == unit_id:
                return u
        raise HTTPException(404)

    @app.get("/w/{slug}/report", response_class=HTMLResponse)
    def report_form(
        request: Request,
        slug: str,
        unit_id: int | None = None,
        date: str | None = None,
        saved: int = 0,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        workspace, role = load_workspace(db, user, slug)
        unit = get_unit(workspace, unit_id, user)
        report_date = parse_date(date)
        report = db.scalar(
            select(DailyReport).where(
                DailyReport.unit_id == unit.id, DailyReport.report_date == report_date
            )
        )
        return render(
            request,
            "report_form.html",
            {
                "user": user,
                "workspace": workspace,
                "role": role,
                "unit": unit,
                "units": units_for(user, workspace),
                "report_date": report_date,
                "report": report,
                "editable": can_edit_date(role, report_date),
                "saved": bool(saved),
            },
        )

    @app.post("/w/{slug}/report")
    async def report_submit(
        request: Request,
        slug: str,
        unit_id: int = Form(...),
        report_date: str = Form(...),
        cash_sales: int = Form(0, ge=0),
        card_sales_own_pos: int = Form(0, ge=0),
        card_sales_other: int = Form(0, ge=0),
        customer_count: int = Form(0, ge=0),
        purchases: int = Form(0, ge=0),
        waste: int = Form(0, ge=0),
        notes: str = Form(""),
        photos: list[UploadFile] = File(default=[]),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        require_csrf(request, csrf)
        workspace, role = load_workspace(db, user, slug)
        unit = get_unit(workspace, unit_id, user)
        date = parse_date(report_date)
        if not can_edit_date(role, date):
            raise HTTPException(403, "This date can no longer be edited")

        photos = [p for p in photos if p.filename]
        if len(photos) > MAX_FILES_PER_SUBMIT:
            raise HTTPException(400, "Too many files")
        for p in photos:
            if Path(p.filename).suffix.lower() not in ALLOWED_UPLOADS:
                raise HTTPException(400, f"File type not allowed: {p.filename}")

        report = db.scalar(
            select(DailyReport).where(
                DailyReport.unit_id == unit.id, DailyReport.report_date == date
            )
        )
        if report is None:
            report = DailyReport(unit_id=unit.id, report_date=date, submitted_by_id=user.id)
            db.add(report)
        report.cash_sales = cash_sales
        report.card_sales_own_pos = card_sales_own_pos
        report.card_sales_other = card_sales_other
        report.customer_count = customer_count
        report.purchases = purchases
        report.waste = waste
        report.notes = notes.strip()
        db.flush()

        written: list[Path] = []
        try:
            for p in photos:
                suffix = Path(p.filename).suffix.lower()
                stored_name = f"{uuid.uuid4().hex}{suffix}"
                target = settings.upload_dir / stored_name
                size = 0
                with target.open("wb") as fh:
                    written.append(target)
                    while chunk := await p.read(1024 * 1024):
                        size += len(chunk)
                        if size > settings.max_upload_bytes:
                            raise HTTPException(400, f"File too large: {p.filename}")
                        fh.write(chunk)
                db.add(
                    Attachment(
                        report_id=report.id,
                        original_name=Path(p.filename).name[:255],
                        stored_name=stored_name,
                        content_type=ALLOWED_UPLOADS[suffix],
                        uploaded_by_id=user.id,
                    )
                )
            db.commit()
        except Exception:
            db.rollback()
            for path in written:
                path.unlink(missing_ok=True)
            raise

        return RedirectResponse(
            f"/w/{slug}/report?unit_id={unit.id}&date={date.isoformat()}&saved=1",
            status_code=303,
        )

    @app.get("/attachments/{attachment_id}")
    def attachment(
        attachment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)
    ):
        att = db.get(Attachment, attachment_id)
        if not att:
            raise HTTPException(404)
        workspace, _role = load_workspace(db, user, att.report.unit.workspace.slug)
        get_unit(workspace, att.report.unit_id, user)
        path = settings.upload_dir / att.stored_name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(
            path,
            media_type=att.content_type or mimetypes.guess_type(path.name)[0],
            headers={"X-Content-Type-Options": "nosniff"},
        )

    async def save_upload(upload: UploadFile, allowed: dict[str, str]) -> tuple[str, str]:
        """Store an upload under a random name; returns (stored_name, content_type)."""
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in allowed:
            raise HTTPException(400, f"File type not allowed: {upload.filename}")
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        target = settings.upload_dir / stored_name
        size = 0
        try:
            with target.open("wb") as fh:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(400, f"File too large: {upload.filename}")
                    fh.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return stored_name, allowed[suffix]

    # ------------------------------------------------------ structure catalog

    def require_manager(role: str):
        if role not in ("admin", Role.manager.value):
            raise HTTPException(404)

    def require_admin(user: User):
        if not user.is_admin:
            raise HTTPException(404)

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not user.is_admin and not any(m.role == Role.manager for m in user.memberships):
            raise HTTPException(404)
        tiers = db.scalars(select(StructureTier).order_by(StructureTier.sort_order)).all()
        return render(
            request, "catalog.html", {"user": user, "tiers": tiers, "today": today()}
        )

    @app.post("/catalog/{tier_id}")
    def catalog_update(
        request: Request,
        tier_id: int,
        min_price: int = Form(..., ge=0),
        max_price: int = Form(..., ge=0),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        require_csrf(request, csrf)
        require_admin(user)
        tier = db.get(StructureTier, tier_id)
        if not tier:
            raise HTTPException(404)
        if max_price < min_price:
            raise HTTPException(400, "max_price must be >= min_price")
        tier.min_price, tier.max_price, tier.price_updated_on = min_price, max_price, today()
        db.commit()
        return RedirectResponse("/catalog", status_code=303)

    # ----------------------------------------------------- monthly costs

    def recent_months(n: int = 12) -> list[tuple[int, int]]:
        jy, jm, _ = jalali.to_jalali(today())
        months = []
        for _ in range(n):
            months.append((jy, jm))
            jy, jm = (jy - 1, 12) if jm == 1 else (jy, jm - 1)
        return months

    def parse_month(value: str | None) -> tuple[int, int]:
        if not value:
            return jalali.to_jalali(today())[:2]
        try:
            y, m = (int(x) for x in value.split("-"))
        except ValueError:
            raise HTTPException(400, "Invalid month")
        if not 1 <= m <= 12:
            raise HTTPException(400, "Invalid month")
        return y, m

    @app.get("/w/{slug}/costs", response_class=HTMLResponse)
    def costs_form(
        request: Request,
        slug: str,
        unit_id: int | None = None,
        month: str | None = None,
        saved: int = 0,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        workspace, role = load_workspace(db, user, slug)
        require_manager(role)
        unit = get_unit(workspace, unit_id, user)
        jy, jm = parse_month(month)
        cost = db.scalar(
            select(MonthlyCost).where(
                MonthlyCost.unit_id == unit.id,
                MonthlyCost.jalali_year == jy,
                MonthlyCost.jalali_month == jm,
            )
        )
        return render(
            request,
            "costs_form.html",
            {
                "user": user, "workspace": workspace, "role": role, "unit": unit,
                "units": units_for(user, workspace),
                "ym": (jy, jm), "months": recent_months(), "cost": cost, "saved": bool(saved),
            },
        )

    @app.post("/w/{slug}/costs")
    def costs_submit(
        request: Request,
        slug: str,
        unit_id: int = Form(...),
        month: str = Form(...),
        salaries: int = Form(0, ge=0),
        taxes: int = Form(0, ge=0),
        utilities: int = Form(0, ge=0),
        depreciation: int = Form(0, ge=0),
        advertising: int = Form(0, ge=0),
        other: int = Form(0, ge=0),
        notes: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        require_csrf(request, csrf)
        workspace, role = load_workspace(db, user, slug)
        require_manager(role)
        unit = get_unit(workspace, unit_id, user)
        jy, jm = parse_month(month)
        cost = db.scalar(
            select(MonthlyCost).where(
                MonthlyCost.unit_id == unit.id,
                MonthlyCost.jalali_year == jy,
                MonthlyCost.jalali_month == jm,
            )
        )
        if cost is None:
            cost = MonthlyCost(unit_id=unit.id, jalali_year=jy, jalali_month=jm)
            db.add(cost)
        cost.salaries, cost.taxes, cost.utilities = salaries, taxes, utilities
        cost.depreciation, cost.advertising, cost.other = depreciation, advertising, other
        cost.notes = notes.strip()
        db.commit()
        return RedirectResponse(
            f"/w/{slug}/costs?unit_id={unit.id}&month={jy}-{jm}&saved=1", status_code=303
        )

    # ------------------------------------------------- partnership contracts

    def load_contract(
        db: Session, workspace: Workspace, contract_id: int, user: User
    ) -> PartnershipContract:
        contract = db.get(PartnershipContract, contract_id)
        if not contract or contract.workspace_id != workspace.id:
            raise HTTPException(404)
        get_unit(workspace, contract.unit_id, user)
        return contract

    @app.get("/w/{slug}/contracts", response_class=HTMLResponse)
    def contracts(
        request: Request, slug: str,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role = load_workspace(db, user, slug)
        require_manager(role)
        rows = []
        for c in db.scalars(
            select(PartnershipContract)
            .where(
                PartnershipContract.workspace_id == workspace.id,
                PartnershipContract.unit_id.in_([u.id for u in units_for(user, workspace)]),
            )
            .order_by(PartnershipContract.code)
        ):
            rows.append(compute_statement(c, load_month_inputs(db, c, today()), today()))
        return render(
            request, "contracts.html",
            {"user": user, "workspace": workspace, "role": role, "statements": rows},
        )

    def contract_form_ctx(user, workspace, role, contract):
        return {
            "user": user, "workspace": workspace, "role": role, "contract": contract,
            "units": units_for(user, workspace), "statuses": list(ContractStatus),
        }

    @app.get("/w/{slug}/contracts/new", response_class=HTMLResponse)
    def contract_new(
        request: Request, slug: str,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role = load_workspace(db, user, slug)
        require_admin(user)
        return render(request, "contract_form.html", contract_form_ctx(user, workspace, role, None))

    @app.get("/w/{slug}/contracts/{contract_id}/edit", response_class=HTMLResponse)
    def contract_edit(
        request: Request, slug: str, contract_id: int,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role = load_workspace(db, user, slug)
        require_admin(user)
        contract = load_contract(db, workspace, contract_id, user)
        return render(
            request, "contract_form.html", contract_form_ctx(user, workspace, role, contract)
        )

    @app.post("/w/{slug}/contracts/save")
    def contract_save(
        request: Request,
        slug: str,
        contract_id: int | None = Form(None),
        code: str = Form(...),
        title: str = Form(...),
        unit_id: int = Form(...),
        investor_name: str = Form(...),
        investor_contact: str = Form(""),
        capital_structure: int = Form(0, ge=0),
        capital_launch: int = Form(0, ge=0),
        start_date: str = Form(""),
        term_years: int = Form(3, ge=1, le=20),
        max_term_years: int = Form(5, ge=1, le=30),
        investor_pct_before: int = Form(60, ge=1, le=100),
        investor_pct_after: int = Form(30, ge=0, le=100),
        management_fee_pct: int = Form(10, ge=0, le=100),
        exit_penalties: str = Form("7,5,3"),
        buyout_multiplier: int = Form(2, ge=0, le=20),
        status: ContractStatus = Form(ContractStatus.draft),
        documents_url: str = Form(""),
        notes: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        require_csrf(request, csrf)
        workspace, _role = load_workspace(db, user, slug)
        require_admin(user)
        unit = get_unit(workspace, unit_id, user)
        penalties = [p.strip() for p in exit_penalties.replace("،", ",").split(",") if p.strip()]
        if not all(p.isdigit() and int(p) <= 100 for p in penalties):
            raise HTTPException(400, "Exit penalties must be comma-separated percentages")
        if documents_url and not documents_url.startswith("https://"):
            raise HTTPException(400, "Documents link must start with https://")
        code = code.strip().upper()
        clash = db.scalar(select(PartnershipContract).where(PartnershipContract.code == code))
        if contract_id is None:
            if clash:
                raise HTTPException(400, f"Contract code {code} already exists")
            contract = PartnershipContract(workspace_id=workspace.id)
            db.add(contract)
        else:
            contract = load_contract(db, workspace, contract_id, user)
            if clash and clash.id != contract.id:
                raise HTTPException(400, f"Contract code {code} already exists")
        contract.code, contract.title, contract.unit_id = code, title.strip(), unit.id
        contract.investor_name = investor_name.strip()
        contract.investor_contact = investor_contact.strip()
        contract.capital_structure, contract.capital_launch = capital_structure, capital_launch
        contract.start_date = parse_date(start_date) if start_date else None
        contract.term_years, contract.max_term_years = term_years, max_term_years
        contract.investor_pct_before = investor_pct_before
        contract.investor_pct_after = investor_pct_after
        contract.management_fee_pct = management_fee_pct
        contract.exit_penalties = ",".join(penalties)
        contract.buyout_multiplier = buyout_multiplier
        contract.status = status
        contract.documents_url = documents_url.strip()
        contract.notes = notes.strip()
        db.commit()
        return RedirectResponse(f"/w/{slug}/contracts/{contract.id}", status_code=303)

    @app.get("/w/{slug}/contracts/{contract_id}", response_class=HTMLResponse)
    def contract_detail(
        request: Request, slug: str, contract_id: int,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role = load_workspace(db, user, slug)
        require_manager(role)
        contract = load_contract(db, workspace, contract_id, user)
        statement = compute_statement(contract, load_month_inputs(db, contract, today()), today())
        return render(
            request, "contract_detail.html",
            {"user": user, "workspace": workspace, "role": role, "s": statement, "c": contract},
        )

    ctx = SimpleNamespace(
        settings=settings, today=today, get_db=get_db, current_user=current_user,
        render=render, require_csrf=require_csrf, load_workspace=load_workspace,
        units_for=units_for, get_unit=get_unit, parse_date=parse_date,
        require_manager=require_manager, require_admin=require_admin,
        load_contract=load_contract, save_upload=save_upload, image_uploads=IMAGE_UPLOADS,
    )
    admin_ops.register(app, ctx)
    public.register(app, ctx)
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory mamal_central.main:app_factory`."""
    return create_app()
