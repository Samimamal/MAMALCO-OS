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


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    units: Mapped[list[Unit]] = relationship(back_populates="workspace", order_by="Unit.id")
    memberships: Mapped[list[Membership]] = relationship(back_populates="workspace")


class Unit(Base):
    """A selling point: a kiosk, tea house, shop, online store."""

    __tablename__ = "units"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(200))
    # Daily sales target in toman.
    daily_target: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    workspace: Mapped[Workspace] = relationship(back_populates="units")
    reports: Mapped[list[DailyReport]] = relationship(back_populates="unit")


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
