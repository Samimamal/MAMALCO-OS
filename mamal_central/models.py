"""Database models.

A Workspace is one brand, company or project of the MAMAL group (e.g. the
kiosks in Oshnavieh, MAMAL Products, MAMAL Beauty). Every Workspace is built
from the same tables, so onboarding a new business is just a new row.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    manager = "manager"  # sees the workspace dashboard, can submit/edit reports
    staff = "staff"  # operator: submits the daily report for their units


class Category(str, enum.Enum):
    form = "form"  # in-city form structures: tea house, café, dairy, ...
    village = "village"
    lodge = "lodge"
    other = "other"


class StructureStatus(str, enum.Enum):
    planned = "planned"
    building = "building"
    operating = "operating"


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    units: Mapped[list[Unit]] = relationship(back_populates="workspace", order_by="Unit.id")
    memberships: Mapped[list[Membership]] = relationship(back_populates="workspace")


class Unit(Base):
    """One MAMAL structure: an independent business with its own public page,
    shop, staff, accounting and investors (tea house, café, village unit, lodge)."""

    __tablename__ = "units"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(200))
    # Daily sales target in toman.
    daily_target: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Public identity on mamalco.com: /s/<code>
    code: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    category: Mapped[Category] = mapped_column(Enum(Category), default=Category.other)
    branch: Mapped[str] = mapped_column(String(100), default="")  # چای‌خانه، کافه، لبنیات، ...
    tagline: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    phone: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[StructureStatus] = mapped_column(
        Enum(StructureStatus), default=StructureStatus.planned
    )
    # Shown on the public site only when this is set AND it has GPS and a photo.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    open_for_investment: Mapped[bool] = mapped_column(Boolean, default=True)
    open_for_rent: Mapped[bool] = mapped_column(Boolean, default=True)
    open_for_jobs: Mapped[bool] = mapped_column(Boolean, default=True)
    build_start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    build_days: Mapped[int] = mapped_column(Integer, default=45)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="units")
    site: Mapped[Site | None] = relationship(back_populates="units")
    reports: Mapped[list[DailyReport]] = relationship(back_populates="unit")
    photos: Mapped[list[StructurePhoto]] = relationship(
        back_populates="unit", order_by="StructurePhoto.sort_order, StructurePhoto.id"
    )
    budget_lines: Mapped[list[BudgetLine]] = relationship(
        back_populates="unit", order_by="BudgetLine.sort_order, BudgetLine.id"
    )
    products: Mapped[list[Product]] = relationship(
        back_populates="unit", order_by="Product.sort_order, Product.id"
    )

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def is_listed(self) -> bool:
        """Visible on the public site."""
        return bool(self.is_public and self.active and self.code and self.has_location and self.photos)

    @property
    def budget_total(self) -> int:
        return sum(b.amount for b in self.budget_lines)

    @property
    def build_end_date(self) -> dt.date | None:
        if not self.build_start_date:
            return None
        return self.build_start_date + dt.timedelta(days=self.build_days)

    def build_progress(self, today: dt.date) -> int | None:
        if not self.build_start_date or not self.build_days:
            return None
        elapsed = (today - self.build_start_date).days
        return max(0, min(100, round(100 * elapsed / self.build_days)))


class LandStatus(str, enum.Enum):
    sketch = "sketch"  # survey sketch (کروکی) only
    negotiating = "negotiating"  # talking to the owner / authority
    agreement = "agreement"  # signed lease / transfer agreement
    permit = "permit"  # building permit issued


class Site(Base):
    """A land parcel: the village land, a lodge plot, or a municipality point area.

    Kept separate from structures: one parcel can carry many structures, and its
    legal status (who owns it, what is signed) decides what may be built and sold.
    """

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[Category] = mapped_column(Enum(Category), default=Category.other)
    owner: Mapped[str] = mapped_column(String(300), default="")
    land_status: Mapped[LandStatus] = mapped_column(Enum(LandStatus), default=LandStatus.sketch)
    declared_area_m2: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # JSON list of [lat, lng]
    polygon_json: Mapped[str] = mapped_column(Text, default="[]")
    # Original coordinates as given in the source document, for audit.
    source_coordinates: Mapped[str] = mapped_column(Text, default="")
    source_note: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    units: Mapped[list[Unit]] = relationship(back_populates="site")

    @property
    def polygon(self) -> list[tuple[float, float]]:
        import json

        return [tuple(p) for p in json.loads(self.polygon_json or "[]")]

    @property
    def computed_area_m2(self) -> int:
        from .geo import latlng_area

        return round(latlng_area(self.polygon))

    @property
    def area_mismatch_pct(self) -> int | None:
        if not self.declared_area_m2 or len(self.polygon) < 3:
            return None
        return round(100 * (self.computed_area_m2 - self.declared_area_m2) / self.declared_area_m2)


class StructurePhoto(Base):
    __tablename__ = "structure_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    caption: Mapped[str] = mapped_column(String(300), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    unit: Mapped[Unit] = relationship(back_populates="photos")


class BudgetLine(Base):
    """Cost line of building a structure up to opening (toman)."""

    __tablename__ = "budget_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    item: Mapped[str] = mapped_column(String(200))
    amount: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    unit: Mapped[Unit] = relationship(back_populates="budget_lines")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    role: Mapped[Role] = mapped_column(Enum(Role))
    # When set, the user only sees this one structure inside the workspace.
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"), nullable=True)

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")


class DailyReport(Base):
    """One unit's numbers for one day. All amounts in toman."""

    __tablename__ = "daily_reports"
    __table_args__ = (UniqueConstraint("unit_id", "report_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    report_date: Mapped[dt.date] = mapped_column(Date)

    cash_sales: Mapped[int] = mapped_column(Integer, default=0)
    # Card payments through MAMAL's own POS terminal.
    card_sales_own_pos: Mapped[int] = mapped_column(Integer, default=0)
    # Card payments through a neighbour's POS or a personal account; tracked
    # separately because this money does not land in MAMAL's account.
    card_sales_other: Mapped[int] = mapped_column(Integer, default=0)
    customer_count: Mapped[int] = mapped_column(Integer, default=0)
    purchases: Mapped[int] = mapped_column(Integer, default=0)
    waste: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")

    submitted_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    unit: Mapped[Unit] = relationship(back_populates="reports")
    submitted_by: Mapped[User] = relationship()
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="report", order_by="Attachment.id"
    )

    @property
    def sales_total(self) -> int:
        return self.cash_sales + self.card_sales_own_pos + self.card_sales_other

    @property
    def average_ticket(self) -> int:
        return self.sales_total // self.customer_count if self.customer_count else 0


class Attachment(Base):
    """Invoice / receipt photo attached to a daily report."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("daily_reports.id"))
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    report: Mapped[DailyReport] = relationship(back_populates="attachments")


class StructureTier(Base):
    """Group-level price list for MAMAL structures (toman, all-in to launch)."""

    __tablename__ = "structure_tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[Category] = mapped_column(Enum(Category), default=Category.other)
    name: Mapped[str] = mapped_column(String(200))
    examples: Mapped[str] = mapped_column(Text, default="")
    includes: Mapped[str] = mapped_column(Text, default="")
    min_price: Mapped[int] = mapped_column(Integer)
    max_price: Mapped[int] = mapped_column(Integer)
    # Prices move with inflation; the page shows how old they are.
    price_updated_on: Mapped[dt.date] = mapped_column(Date)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ContractStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    ended = "ended"


class PartnershipContract(Base):
    """Civil-partnership investment in one unit (template: Coffee Bus 03).

    Defaults mirror the template: 3-year term extendable to 5, 60/40 split
    (investor/manager) until the capital is returned, 30/70 after, a 10%
    management fee on gross sales taken before the split, early-exit
    penalties of 7/5/3% by contract year, and a buy-out value of
    6-month average net profit x 2 x investor share.

    Investor national ID numbers are deliberately not stored here; keep
    identity documents in the restricted Drive folder linked in documents_url.
    """

    __tablename__ = "partnership_contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    code: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    investor_name: Mapped[str] = mapped_column(String(200))
    investor_contact: Mapped[str] = mapped_column(String(200), default="")

    capital_structure: Mapped[int] = mapped_column(Integer, default=0)
    capital_launch: Mapped[int] = mapped_column(Integer, default=0)
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    term_years: Mapped[int] = mapped_column(Integer, default=3)
    max_term_years: Mapped[int] = mapped_column(Integer, default=5)

    investor_pct_before: Mapped[int] = mapped_column(Integer, default=60)
    investor_pct_after: Mapped[int] = mapped_column(Integer, default=30)
    management_fee_pct: Mapped[int] = mapped_column(Integer, default=10)
    # Comma-separated penalty % for exit in contract year 1, 2, 3, ...
    exit_penalties: Mapped[str] = mapped_column(String(64), default="7,5,3")
    buyout_multiplier: Mapped[int] = mapped_column(Integer, default=2)

    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus), default=ContractStatus.draft
    )
    documents_url: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    unit: Mapped[Unit] = relationship()
    payouts: Mapped[list[Payout]] = relationship(order_by="Payout.paid_on, Payout.id")

    @property
    def paid_total(self) -> int:
        return sum(p.amount for p in self.payouts)

    @property
    def capital(self) -> int:
        return self.capital_structure + self.capital_launch

    @property
    def penalty_list(self) -> list[int]:
        return [int(p) for p in self.exit_penalties.split(",") if p.strip()]


class MonthlyCost(Base):
    """A unit's operating costs for one Jalali month (toman).

    Raw materials come from the daily reports' purchases, so they are not here.
    """

    __tablename__ = "monthly_costs"
    __table_args__ = (UniqueConstraint("unit_id", "jalali_year", "jalali_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    jalali_year: Mapped[int] = mapped_column(Integer)
    jalali_month: Mapped[int] = mapped_column(Integer)
    salaries: Mapped[int] = mapped_column(Integer, default=0)
    taxes: Mapped[int] = mapped_column(Integer, default=0)
    utilities: Mapped[int] = mapped_column(Integer, default=0)  # water, power, gas, terminal charge, services
    depreciation: Mapped[int] = mapped_column(Integer, default=0)
    advertising: Mapped[int] = mapped_column(Integer, default=0)
    other: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")

    @property
    def total(self) -> int:
        return (
            self.salaries + self.taxes + self.utilities + self.depreciation
            + self.advertising + self.other
        )


# ------------------------------------------------------------------ public cases


class CaseKind(str, enum.Enum):
    invest = "invest"
    rent = "rent"
    job = "job"
    general = "general"


class CaseStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    rejected = "rejected"
    converted = "converted"


class Application(Base):
    """An inbound request from the public site (investor, renter, job seeker).

    Deliberately no national ID / bank fields: documents are collected later
    into a restricted Drive folder.
    """

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[CaseKind] = mapped_column(Enum(CaseKind))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    category: Mapped[Category | None] = mapped_column(Enum(Category), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    # invest
    cash_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cheque_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # job / rent
    desired_role: Mapped[str] = mapped_column(String(200), default="")
    experience: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus), default=CaseStatus.new)
    rating: Mapped[str] = mapped_column(String(1), default="")  # A-D
    internal_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    unit: Mapped[Unit | None] = relationship()

    @property
    def case_id(self) -> str:
        year = self.created_at.year if self.created_at else dt.date.today().year
        return f"MC-{year}-{self.id:04d}"


# ------------------------------------------------------------------------ shop


class Product(Base):
    """Something a structure sells, in person and/or online, retail and/or wholesale."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    unit_label: Mapped[str] = mapped_column(String(50), default="عدد")  # کیلو، بسته، لیوان
    retail_price: Mapped[int] = mapped_column(Integer, default=0)
    wholesale_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wholesale_min_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sold_online: Mapped[bool] = mapped_column(Boolean, default=True)
    served_on_site: Mapped[bool] = mapped_column(Boolean, default=False)  # drink/eat there
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    unit: Mapped[Unit] = relationship(back_populates="products")

    def price_for(self, qty: int) -> int:
        if self.wholesale_price and self.wholesale_min_qty and qty >= self.wholesale_min_qty:
            return self.wholesale_price
        return self.retail_price


class OrderStatus(str, enum.Enum):
    new = "new"
    confirmed = "confirmed"
    delivered = "delivered"
    cancelled = "cancelled"


class Order(Base):
    """Online order; payment is confirmed by phone/WhatsApp until a gateway is added."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(40))
    address: Mapped[str] = mapped_column(Text, default="")
    delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.new)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    unit: Mapped[Unit] = relationship()
    items: Mapped[list[OrderItem]] = relationship(back_populates="order", order_by="OrderItem.id")

    @property
    def total(self) -> int:
        return sum(i.line_total for i in self.items)

    @property
    def order_code(self) -> str:
        return f"MO-{self.id:05d}"

    @property
    def is_wholesale(self) -> bool:
        return any(i.wholesale for i in self.items)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(200))
    unit_label: Mapped[str] = mapped_column(String(50))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer)
    wholesale: Mapped[bool] = mapped_column(Boolean, default=False)

    order: Mapped[Order] = relationship(back_populates="items")

    @property
    def line_total(self) -> int:
        return self.qty * self.unit_price


# ----------------------------------------------------------------------- staff


class RaiseRule(str, enum.Enum):
    six_months = "six_months"  # +3M toman every 6 months
    yearly = "yearly"  # +5M toman every 12 months


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(40), default="")
    job_title: Mapped[str] = mapped_column(String(200), default="")
    start_date: Mapped[dt.date] = mapped_column(Date)
    contract_months: Mapped[int] = mapped_column(Integer, default=12)
    base_salary: Mapped[int] = mapped_column(Integer)
    raise_rule: Mapped[RaiseRule] = mapped_column(Enum(RaiseRule), default=RaiseRule.yearly)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    unit: Mapped[Unit] = relationship()


# -------------------------------------------------------------- investor payouts


class Payout(Base):
    """Money actually paid to an investor under a partnership contract."""

    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("partnership_contracts.id"))
    paid_on: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(300), default="")
