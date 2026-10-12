"""Back-office pages for each structure: profile & photos, build budget,
inbound applications, products, online orders, staff and investor payouts."""

from __future__ import annotations

import datetime as dt

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import (
    Application,
    BudgetLine,
    CaseStatus,
    Employee,
    Order,
    OrderStatus,
    Payout,
    Product,
    RaiseRule,
    StructurePhoto,
    StructureStatus,
    StructureTier,
    Unit,
    User,
    Workspace,
)
from .rules import (
    BUDGET_ITEMS,
    MIN_CONTRACT_MONTHS,
    SALARY_MAX,
    SALARY_MIN,
    contract_end,
    current_salary,
    investment_rule,
    next_raise_date,
)


def register(app: FastAPI, ctx) -> None:
    get_db, current_user, render = ctx.get_db, ctx.current_user, ctx.render

    def manager_scope(request_db: Session, user: User, slug: str) -> tuple[Workspace, str, list[Unit]]:
        workspace, role = ctx.load_workspace(request_db, user, slug)
        ctx.require_manager(role)
        return workspace, role, ctx.units_for(user, workspace)

    def base(user, workspace, role, active, **extra):
        return {"user": user, "workspace": workspace, "role": role, "active": active, **extra}

    # ------------------------------------------------------------ structures

    @app.get("/w/{slug}/structures", response_class=HTMLResponse)
    def structures(
        request: Request, slug: str,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role, units = manager_scope(db, user, slug)
        return render(
            request, "admin_structures.html",
            base(user, workspace, role, "structures", units=units, today=ctx.today()),
        )

    @app.get("/w/{slug}/structures/{unit_id}", response_class=HTMLResponse)
    def structure_edit(
        request: Request, slug: str, unit_id: int, saved: int = 0,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role, _units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        budget = unit.budget_lines or [BudgetLine(item=i, amount=0) for i in BUDGET_ITEMS]
        return render(
            request, "admin_structure_edit.html",
            base(user, workspace, role, "structures", unit=unit, budget=budget,
                 statuses=list(StructureStatus), saved=bool(saved), today=ctx.today()),
        )

    @app.post("/w/{slug}/structures/{unit_id}")
    def structure_save(
        request: Request, slug: str, unit_id: int,
        name: str = Form(...),
        branch: str = Form(""),
        tagline: str = Form(""),
        description: str = Form(""),
        address: str = Form(""),
        latitude: str = Form(""),
        longitude: str = Form(""),
        phone: str = Form(""),
        status: StructureStatus = Form(StructureStatus.planned),
        daily_target: int = Form(0, ge=0),
        build_start_date: str = Form(""),
        build_days: int = Form(45, ge=1, le=1000),
        is_public: bool = Form(False),
        open_for_investment: bool = Form(False),
        open_for_rent: bool = Form(False),
        open_for_jobs: bool = Form(False),
        csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role, _units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        lat, lng = _parse_coord(latitude, -90, 90), _parse_coord(longitude, -180, 180)
        if (lat is None) != (lng is None):
            raise HTTPException(400, "Enter both latitude and longitude")
        unit.name, unit.branch = name.strip(), branch.strip()
        unit.tagline, unit.description = tagline.strip(), description.strip()
        unit.address, unit.phone = address.strip(), phone.strip()
        unit.latitude, unit.longitude = lat, lng
        unit.status, unit.daily_target = status, daily_target
        unit.build_start_date = ctx.parse_date(build_start_date) if build_start_date else None
        unit.build_days = build_days
        unit.is_public = is_public
        unit.open_for_investment = open_for_investment
        unit.open_for_rent = open_for_rent
        unit.open_for_jobs = open_for_jobs
        db.commit()
        return RedirectResponse(f"/w/{slug}/structures/{unit.id}?saved=1", status_code=303)

    @app.post("/w/{slug}/structures/{unit_id}/photos")
    async def structure_photos(
        request: Request, slug: str, unit_id: int,
        photos: list[UploadFile] = File(default=[]),
        caption: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role, _units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        photos = [p for p in photos if p.filename]
        if len(photos) > 10:
            raise HTTPException(400, "Too many files")
        stored = []
        try:
            for p in photos:
                stored.append(await ctx.save_upload(p, ctx.image_uploads))
        except Exception:
            for name, _ in stored:
                (ctx.settings.upload_dir / name).unlink(missing_ok=True)
            raise
        start = len(unit.photos)
        for i, (name, ctype) in enumerate(stored):
            db.add(StructurePhoto(
                unit_id=unit.id, stored_name=name, content_type=ctype,
                caption=caption.strip(), sort_order=start + i,
            ))
        db.commit()
        return RedirectResponse(f"/w/{slug}/structures/{unit.id}?saved=1", status_code=303)

    @app.post("/w/{slug}/structures/{unit_id}/photos/{photo_id}/delete")
    def structure_photo_delete(
        request: Request, slug: str, unit_id: int, photo_id: int, csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role, _units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        photo = db.get(StructurePhoto, photo_id)
        if not photo or photo.unit_id != unit.id:
            raise HTTPException(404)
        (ctx.settings.upload_dir / photo.stored_name).unlink(missing_ok=True)
        db.delete(photo)
        db.commit()
        return RedirectResponse(f"/w/{slug}/structures/{unit.id}", status_code=303)

    @app.post("/w/{slug}/structures/{unit_id}/budget")
    async def structure_budget(
        request: Request, slug: str, unit_id: int,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        form = await request.form()
        ctx.require_csrf(request, form.get("csrf"))
        workspace, _role, _units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        items, amounts = form.getlist("item"), form.getlist("amount")
        if len(items) != len(amounts) or len(items) > 50:
            raise HTTPException(400)
        for line in list(unit.budget_lines):
            db.delete(line)
        for i, (item, amount) in enumerate(zip(items, amounts)):
            item = str(item).strip()
            if not item:
                continue
            try:
                value = int(str(amount or "0"))
            except ValueError:
                raise HTTPException(400, f"Invalid amount for {item}")
            if value < 0:
                raise HTTPException(400, f"Invalid amount for {item}")
            db.add(BudgetLine(unit_id=unit.id, item=item[:200], amount=value, sort_order=i))
        db.commit()
        return RedirectResponse(f"/w/{slug}/structures/{unit.id}?saved=1", status_code=303)

    # ---------------------------------------------------------- applications

    def workspace_cases_query(user: User, workspace: Workspace, units: list[Unit]):
        unit_ids = [u.id for u in units]
        restricted = not user.is_admin and any(
            m.workspace_id == workspace.id and m.unit_id for m in user.memberships
        )
        categories = {u.category for u in workspace.units}
        if restricted:
            return Application.unit_id.in_(unit_ids)
        # General requests (no structure) about this workspace's categories too.
        return or_(
            Application.unit_id.in_(unit_ids),
            Application.unit_id.is_(None) & Application.category.in_(categories),
        )

    @app.get("/w/{slug}/applications", response_class=HTMLResponse)
    def applications(
        request: Request, slug: str, status: str | None = None, kind: str | None = None,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role, units = manager_scope(db, user, slug)
        query = select(Application).where(workspace_cases_query(user, workspace, units))
        if status:
            query = query.where(Application.status == status)
        if kind:
            query = query.where(Application.kind == kind)
        cases = db.scalars(query.order_by(Application.id.desc()).limit(300)).all()
        tiers = {t.category: t.min_price for t in db.scalars(select(StructureTier))}
        below_min = {
            c.id for c in cases
            if c.kind.value == "invest" and c.category and c.cash_amount is not None
            and c.cash_amount < investment_rule(c.category, tiers.get(c.category)).min_cash
        }
        return render(
            request, "admin_applications.html",
            base(user, workspace, role, "applications", cases=cases, below_min=below_min,
                 statuses=list(CaseStatus), f_status=status or "", f_kind=kind or ""),
        )

    @app.post("/w/{slug}/applications/{case_id}")
    def application_update(
        request: Request, slug: str, case_id: int,
        status: CaseStatus = Form(...),
        rating: str = Form(""),
        internal_notes: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role, units = manager_scope(db, user, slug)
        case = db.scalar(
            select(Application).where(
                Application.id == case_id, workspace_cases_query(user, workspace, units)
            )
        )
        if not case:
            raise HTTPException(404)
        if rating not in ("", "A", "B", "C", "D"):
            raise HTTPException(400, "Rating must be A-D")
        case.status, case.rating, case.internal_notes = status, rating, internal_notes.strip()
        db.commit()
        return RedirectResponse(f"/w/{slug}/applications#case-{case.id}", status_code=303)

    # --------------------------------------------------------------- products

    @app.get("/w/{slug}/products", response_class=HTMLResponse)
    def products(
        request: Request, slug: str, unit_id: int | None = None, edit: int | None = None,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role, units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        editing = next((p for p in unit.products if p.id == edit), None)
        return render(
            request, "admin_products.html",
            base(user, workspace, role, "products", unit=unit, units=units, editing=editing),
        )

    @app.post("/w/{slug}/products")
    def product_save(
        request: Request, slug: str,
        unit_id: int = Form(...),
        product_id: int | None = Form(None),
        name: str = Form(...),
        description: str = Form(""),
        unit_label: str = Form("عدد"),
        retail_price: int = Form(..., ge=0),
        wholesale_price: int | None = Form(None, ge=0),
        wholesale_min_qty: int | None = Form(None, ge=1),
        sold_online: bool = Form(False),
        served_on_site: bool = Form(False),
        active: bool = Form(False),
        csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role, _units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        if (wholesale_price is None) != (wholesale_min_qty is None):
            raise HTTPException(400, "Wholesale needs both a price and a minimum quantity")
        if product_id:
            product = db.get(Product, product_id)
            if not product or product.unit_id != unit.id:
                raise HTTPException(404)
        else:
            product = Product(unit_id=unit.id, sort_order=len(unit.products))
            db.add(product)
        product.name, product.description = name.strip(), description.strip()
        product.unit_label = unit_label.strip() or "عدد"
        product.retail_price = retail_price
        product.wholesale_price, product.wholesale_min_qty = wholesale_price, wholesale_min_qty
        product.sold_online, product.served_on_site, product.active = sold_online, served_on_site, active
        db.commit()
        return RedirectResponse(f"/w/{slug}/products?unit_id={unit.id}", status_code=303)

    # ----------------------------------------------------------------- orders

    @app.get("/w/{slug}/orders", response_class=HTMLResponse)
    def orders(
        request: Request, slug: str, status: str | None = None,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role, units = manager_scope(db, user, slug)
        query = select(Order).where(Order.unit_id.in_([u.id for u in units]))
        if status:
            query = query.where(Order.status == status)
        rows = db.scalars(query.order_by(Order.id.desc()).limit(300)).all()
        return render(
            request, "admin_orders.html",
            base(user, workspace, role, "orders", orders=rows,
                 statuses=list(OrderStatus), f_status=status or ""),
        )

    @app.post("/w/{slug}/orders/{order_id}")
    def order_update(
        request: Request, slug: str, order_id: int,
        status: OrderStatus = Form(...), csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role, units = manager_scope(db, user, slug)
        order = db.get(Order, order_id)
        if not order or order.unit_id not in {u.id for u in units}:
            raise HTTPException(404)
        order.status = status
        db.commit()
        return RedirectResponse(f"/w/{slug}/orders#order-{order.id}", status_code=303)

    # ------------------------------------------------------------------ staff

    @app.get("/w/{slug}/staff", response_class=HTMLResponse)
    def staff(
        request: Request, slug: str, edit: int | None = None,
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        workspace, role, units = manager_scope(db, user, slug)
        employees = db.scalars(
            select(Employee)
            .where(Employee.unit_id.in_([u.id for u in units]))
            .order_by(Employee.active.desc(), Employee.unit_id, Employee.full_name)
        ).all()
        today = ctx.today()
        rows = [
            {
                "e": e,
                "salary": current_salary(e, today),
                "next_raise": next_raise_date(e, today),
                "contract_end": contract_end(e),
            }
            for e in employees
        ]
        editing = next((e for e in employees if e.id == edit), None)
        return render(
            request, "admin_staff.html",
            base(user, workspace, role, "staff", rows=rows, units=units, editing=editing,
                 raise_rules=list(RaiseRule), salary_min=SALARY_MIN, salary_max=SALARY_MAX,
                 min_months=MIN_CONTRACT_MONTHS, today=today,
                 payroll=sum(r["salary"] for r in rows if r["e"].active)),
        )

    @app.post("/w/{slug}/staff")
    def staff_save(
        request: Request, slug: str,
        employee_id: int | None = Form(None),
        unit_id: int = Form(...),
        full_name: str = Form(...),
        phone: str = Form(""),
        job_title: str = Form(""),
        start_date: str = Form(...),
        contract_months: int = Form(MIN_CONTRACT_MONTHS, ge=MIN_CONTRACT_MONTHS, le=120),
        base_salary: int = Form(..., ge=SALARY_MIN, le=SALARY_MAX),
        raise_rule: RaiseRule = Form(RaiseRule.yearly),
        active: bool = Form(False),
        notes: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role, units = manager_scope(db, user, slug)
        unit = ctx.get_unit(workspace, unit_id, user)
        if employee_id:
            employee = db.get(Employee, employee_id)
            if not employee or employee.unit_id not in {u.id for u in units}:
                raise HTTPException(404)
        else:
            employee = Employee()
            db.add(employee)
        employee.unit_id = unit.id
        employee.full_name, employee.phone = full_name.strip(), phone.strip()
        employee.job_title = job_title.strip()
        employee.start_date = ctx.parse_date(start_date)
        employee.contract_months, employee.base_salary = contract_months, base_salary
        employee.raise_rule, employee.active, employee.notes = raise_rule, active, notes.strip()
        db.commit()
        return RedirectResponse(f"/w/{slug}/staff", status_code=303)

    # ---------------------------------------------------------- investor payouts

    @app.post("/w/{slug}/contracts/{contract_id}/payouts")
    def payout_add(
        request: Request, slug: str, contract_id: int,
        paid_on: str = Form(...),
        amount: int = Form(..., gt=0),
        note: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user), db: Session = Depends(get_db),
    ):
        ctx.require_csrf(request, csrf)
        workspace, _role = ctx.load_workspace(db, user, slug)
        ctx.require_admin(user)
        contract = ctx.load_contract(db, workspace, contract_id, user)
        date = ctx.parse_date(paid_on)
        if date > ctx.today():
            raise HTTPException(400, "Payout date is in the future")
        db.add(Payout(contract_id=contract.id, paid_on=date, amount=amount, note=note.strip()))
        db.commit()
        return RedirectResponse(f"/w/{slug}/contracts/{contract.id}#payouts", status_code=303)


def _parse_coord(value: str, low: float, high: float) -> float | None:
    value = (value or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789."))
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        raise HTTPException(400, "Invalid coordinate")
    if not low <= number <= high:
        raise HTTPException(400, "Coordinate out of range")
    return round(number, 6)
