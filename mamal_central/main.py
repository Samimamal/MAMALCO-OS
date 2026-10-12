"""MAMAL Central web app: login, workspace dashboards and the daily unit report."""

from __future__ import annotations

import datetime as dt
import mimetypes
import uuid
from pathlib import Path

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
from .models import Attachment, DailyReport, Role, Unit, User, Workspace
from .security import csrf_ok, csrf_token, verify_password

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
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        require_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---------------------------------------------------------- workspaces

    @app.get("/", response_class=HTMLResponse)
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
        summaries = build_dashboard(db, workspace, today(), settings.dashboard_days)
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

    def get_unit(workspace: Workspace, unit_id: int | None) -> Unit:
        units = [u for u in workspace.units if u.active]
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
        unit = get_unit(workspace, unit_id)
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
                "units": [u for u in workspace.units if u.active],
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
        unit = get_unit(workspace, unit_id)
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
        load_workspace(db, user, att.report.unit.workspace.slug)
        path = settings.upload_dir / att.stored_name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(
            path,
            media_type=att.content_type or mimetypes.guess_type(path.name)[0],
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory mamal_central.main:app_factory`."""
    return create_app()
