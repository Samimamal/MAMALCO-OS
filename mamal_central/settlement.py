"""Monthly investor statement for a partnership contract.

Per Jalali month, from the contract start to the current month:

    revenue        = sum of daily-report sales
    materials      = sum of daily-report purchases
    operating      = MonthlyCost total (salaries, taxes, utilities, depreciation, ads, other)
    management fee = management_fee_pct x revenue      (taken before the split)
    net profit     = revenue - materials - operating - management fee

A positive net profit is split investor_pct_before / rest until the investor's
cumulative share equals the capital, then investor_pct_after / rest. A month
that crosses the line is split pro rata. Losses are reported but not carried
forward against later profit (the template is silent on this; «فرض»).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import jalali
from .models import DailyReport, MonthlyCost, PartnershipContract


@dataclass
class MonthInput:
    year: int
    month: int
    revenue: int = 0
    materials: int = 0
    operating: int = 0
    has_costs: bool = False
    days_reported: int = 0
    in_progress: bool = False


@dataclass
class MonthLine:
    input: MonthInput
    management_fee: int
    net_profit: int
    investor_share: int
    manager_share: int
    investor_cumulative: int
    recovered: bool


@dataclass
class Statement:
    contract: PartnershipContract
    lines: list[MonthLine] = field(default_factory=list)
    today: dt.date = field(default_factory=dt.date.today)

    @property
    def investor_total(self) -> int:
        return self.lines[-1].investor_cumulative if self.lines else 0

    @property
    def manager_total(self) -> int:
        return sum(line.manager_share for line in self.lines)

    @property
    def net_profit_total(self) -> int:
        return sum(line.net_profit for line in self.lines)

    @property
    def remaining_capital(self) -> int:
        return max(self.contract.capital - self.investor_total, 0)

    @property
    def recovered_pct(self) -> int:
        cap = self.contract.capital
        return min(100, round(100 * self.investor_total / cap)) if cap else 0

    @property
    def is_recovered(self) -> bool:
        return self.remaining_capital == 0 and self.contract.capital > 0

    @property
    def current_investor_pct(self) -> int:
        c = self.contract
        return c.investor_pct_after if self.is_recovered else c.investor_pct_before

    def _complete(self, n: int) -> list[MonthLine]:
        done = [line for line in self.lines if not line.input.in_progress]
        return done[-n:]

    @property
    def avg_net_profit_6m(self) -> int:
        recent = self._complete(6)
        return sum(line.net_profit for line in recent) // len(recent) if recent else 0

    @property
    def months_to_recover(self) -> int | None:
        """Projection from the average investor share of the last 3 complete months."""
        if self.is_recovered:
            return 0
        recent = self._complete(3)
        avg = sum(line.investor_share for line in recent) // len(recent) if recent else 0
        if avg <= 0:
            return None
        return -(-self.remaining_capital // avg)

    @property
    def contract_year(self) -> int | None:
        start = self.contract.start_date
        if not start or self.today < start:
            return None
        years = self.today.year - start.year - (
            (self.today.month, self.today.day) < (start.month, start.day)
        )
        return years + 1

    @property
    def end_date(self) -> dt.date | None:
        start = self.contract.start_date
        if not start:
            return None
        try:
            return start.replace(year=start.year + self.contract.term_years)
        except ValueError:  # 29 Feb
            return start.replace(year=start.year + self.contract.term_years, day=28)

    @property
    def exit_penalty_pct(self) -> int | None:
        year = self.contract_year
        penalties = self.contract.penalty_list
        if year is None or self.is_recovered or not penalties:
            return None
        return penalties[min(year, len(penalties)) - 1]

    @property
    def exit_penalty_amount(self) -> int | None:
        """Penalty assumed to apply to the unreturned capital («فرض»)."""
        pct = self.exit_penalty_pct
        return self.remaining_capital * pct // 100 if pct is not None else None

    @property
    def buyout_value(self) -> int:
        """Article 10: 6-month average net profit x multiplier x investor share."""
        avg = max(self.avg_net_profit_6m, 0)
        return avg * self.contract.buyout_multiplier * self.current_investor_pct // 100


def split_month(
    net_profit: int, already_paid: int, capital: int, pct_before: int, pct_after: int
) -> int:
    """Investor's share of one month's net profit."""
    if net_profit <= 0:
        return 0
    remaining = max(capital - already_paid, 0)
    before = net_profit * pct_before // 100
    if before <= remaining:
        return before
    # Part of this month's profit, at pct_before, exactly returns the capital;
    # the rest is split at pct_after.
    profit_to_recover = -(-remaining * 100 // pct_before)
    return remaining + (net_profit - profit_to_recover) * pct_after // 100


def compute_statement(
    contract: PartnershipContract, months: list[MonthInput], today: dt.date
) -> Statement:
    statement = Statement(contract=contract, today=today)
    cumulative = 0
    for m in months:
        fee = m.revenue * contract.management_fee_pct // 100
        net = m.revenue - m.materials - m.operating - fee
        share = split_month(
            net, cumulative, contract.capital,
            contract.investor_pct_before, contract.investor_pct_after,
        )
        cumulative += share
        statement.lines.append(
            MonthLine(
                input=m,
                management_fee=fee,
                net_profit=net,
                investor_share=share,
                manager_share=max(net, 0) - share,
                investor_cumulative=cumulative,
                recovered=contract.capital > 0 and cumulative >= contract.capital,
            )
        )
    return statement


def load_month_inputs(
    db: Session, contract: PartnershipContract, today: dt.date
) -> list[MonthInput]:
    if not contract.start_date or contract.start_date > today:
        return []
    jy, jm, _ = jalali.to_jalali(contract.start_date)
    last = jalali.to_jalali(today)[:2]
    reports = db.scalars(
        select(DailyReport).where(
            DailyReport.unit_id == contract.unit_id,
            DailyReport.report_date.between(contract.start_date, today),
        )
    ).all()
    costs = {
        (c.jalali_year, c.jalali_month): c
        for c in db.scalars(select(MonthlyCost).where(MonthlyCost.unit_id == contract.unit_id))
    }
    by_month: dict[tuple[int, int], list[DailyReport]] = {}
    for r in reports:
        by_month.setdefault(jalali.to_jalali(r.report_date)[:2], []).append(r)

    months = []
    while (jy, jm) <= last:
        rs = by_month.get((jy, jm), [])
        cost = costs.get((jy, jm))
        months.append(
            MonthInput(
                year=jy,
                month=jm,
                revenue=sum(r.sales_total for r in rs),
                materials=sum(r.purchases for r in rs),
                operating=cost.total if cost else 0,
                has_costs=cost is not None,
                days_reported=len(rs),
                in_progress=(jy, jm) == last,
            )
        )
        jy, jm = jalali.next_month(jy, jm)
    return months
