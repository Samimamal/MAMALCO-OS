"""Builds the per-unit scoreboard shown on a workspace dashboard."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import DailyReport, Unit


@dataclass
class DayCell:
    date: dt.date
    report: DailyReport | None

    @property
    def flags(self) -> list[str]:
        if self.report is None:
            return ["missing"]
        flags = []
        if not self.report.attachments:
            flags.append("no_photo")
        if self.report.card_sales_other:
            flags.append("off_pos")
        return flags


@dataclass
class UnitSummary:
    unit: Unit
    days: list[DayCell] = field(default_factory=list)

    @property
    def reports(self) -> list[DailyReport]:
        return [d.report for d in self.days if d.report]

    @property
    def days_reported(self) -> int:
        return len(self.reports)

    @property
    def total_sales(self) -> int:
        return sum(r.sales_total for r in self.reports)

    @property
    def avg_daily_sales(self) -> int:
        return self.total_sales // self.days_reported if self.days_reported else 0

    @property
    def total_customers(self) -> int:
        return sum(r.customer_count for r in self.reports)

    @property
    def avg_ticket(self) -> int:
        return self.total_sales // self.total_customers if self.total_customers else 0

    @property
    def total_waste(self) -> int:
        return sum(r.waste for r in self.reports)

    @property
    def total_off_pos(self) -> int:
        return sum(r.card_sales_other for r in self.reports)

    @property
    def missing_photo_days(self) -> int:
        return sum(1 for r in self.reports if not r.attachments)

    @property
    def target_pct(self) -> int | None:
        if not self.unit.daily_target or not self.days_reported:
            return None
        return round(100 * self.avg_daily_sales / self.unit.daily_target)

    @property
    def days_on_target(self) -> int:
        target = self.unit.daily_target
        return sum(1 for r in self.reports if target and r.sales_total >= target)


def build_dashboard(
    db: Session, units: list[Unit], end: dt.date, days: int
) -> list[UnitSummary]:
    start = end - dt.timedelta(days=days - 1)
    dates = [end - dt.timedelta(days=i) for i in range(days)]
    reports = db.scalars(
        select(DailyReport)
        .where(
            DailyReport.unit_id.in_([u.id for u in units]),
            DailyReport.report_date.between(start, end),
        )
        .options(selectinload(DailyReport.attachments))
    ).all()
    by_key = {(r.unit_id, r.report_date): r for r in reports}
    return [
        UnitSummary(unit=u, days=[DayCell(d, by_key.get((u.id, d))) for d in dates])
        for u in units
    ]
