"""MAMAL business rules in one place (amounts in toman).

Change numbers here, not in templates.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .jalali import persian_digits
from .models import Category, Employee, RaiseRule

CATEGORY_LABELS = {
    Category.form: "سازه‌های فرم‌دار شهری",
    Category.village: "سازه‌های دهکده",
    Category.lodge: "اقامتگاه‌های مامال",
    Category.other: "سایر",
}
CATEGORY_PREFIX = {Category.form: "F", Category.village: "V", Category.lodge: "L"}
# Oshnavieh plan (project dossier 1405/07/20): phase one = 30 kiosk sites of
# ~12 m² under the 10-year municipality contract (10 in year one, then 5 a
# year); a 150-kiosk vision for the city; the village = 30 superadobe domes of
# up to ~36 m², built 10 a year; lodge units on the 13,500 m² Qala Tuk land,
# also 10 a year.
KIOSKS_VISION = 150
UNITS_PER_YEAR = {Category.village: 10, Category.lodge: 10}
VILLAGE_TOTAL = 30
STRUCTURE_COUNTS = {Category.form: 30, Category.village: 30, Category.lodge: 10}
CITY_ROLLOUT = [(1, 10), (2, 15), (3, 20), (4, 25), (5, 30)]  # (contract year, cumulative sites)
EXPANSION_STEPS = [
    ("اشنویه (پایلوت)", "فاز اول ۳۰ دکه (۱۰ در سال اول، سپس هر سال ۵)؛ دهکده سالی ۱۰ سازه تا ۳۰؛ اقامتگاه قلاتوک سالی ۱۰ واحد؛ چشم‌انداز ۱۵۰ دکه"),
    ("شهرهای استان آذربایجان غربی", "کپی همان بسته در شهرهایی که ظرفیت و شهرداری همراه دارند"),
    ("استان‌های دیگر ایران", "هر برند جدا، با سرمایه‌گذار یا بهره‌بردار محلی (فرانچایز)"),
    ("کشورهای همسایه و اروپا", "پس از ثبت برند، دفترچه‌ی عملیات و داده‌ی ۶ تا ۱۲ ماهه"),
]

# Default branch names for in-city form structures; editable per structure.
FORM_BRANCHES = ["چای‌خانه", "کافه", "لبنیات", "اسموتی", "بستنی", "میوه"]

# What a structure budget covers up to opening; used as default budget lines.
BUDGET_ITEMS = [
    "سازه (بدنه ابرخشت)",
    "مصالح",
    "دستمزد کارگر",
    "برق‌کشی",
    "لوله‌کشی و تأسیسات",
    "تجهیزات داخلی",
    "دکور",
    "مجوزها",
    "مارکتینگ و افتتاح",
    "سرمایه در گردش اولیه",
]

BUILD_DAYS = 45


@dataclass(frozen=True)
class InvestmentRule:
    min_cash: int
    cheques_accepted: bool
    payment_plan: str
    note: str = ""


def investment_rule(category: Category, tier_min_price: int | None) -> InvestmentRule:
    if category == Category.form:
        return InvestmentRule(
            min_cash=700_000_000,
            cheques_accepted=True,
            payment_plan=(
                f"حداقل ۷۰۰ میلیون تومان نقد؛ باقی‌مانده با چک. ساخت هر سازه حدود "
                f"{persian_digits(BUILD_DAYS)} روز: ۵۰٪ نقد در شروع ساخت و ۵۰٪ تا تکمیل سازه."
            ),
        )
    # Village and lodge: half in cash at signing, the rest in stages with construction.
    min_cash = (tier_min_price or 0) // 2
    return InvestmentRule(
        min_cash=min_cash,
        cheques_accepted=True,
        payment_plan=(
            "۵۰٪ نقد در زمان قرارداد؛ باقی‌مانده مرحله‌ای همراه با پیشرفت اجرای پروژه."
        ),
        note="پیش‌ثبت‌نام از امروز تا بهار باز است؛ اجرای پروژه از بهار شروع می‌شود.",
    )


# ------------------------------------------------------------------ salaries

SALARY_MIN = 15_000_000
SALARY_MAX = 30_000_000
MIN_CONTRACT_MONTHS = 12
RAISES = {
    RaiseRule.six_months: (6, 3_000_000),
    RaiseRule.yearly: (12, 5_000_000),
}
RAISE_LABELS = {
    RaiseRule.six_months: "۳ میلیون هر ۶ ماه",
    RaiseRule.yearly: "۵ میلیون هر سال",
}


def _months_between(start: dt.date, end: dt.date) -> int:
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def _add_months(date: dt.date, months: int) -> dt.date:
    y, m = divmod(date.month - 1 + months, 12)
    year, month = date.year + y, m + 1
    for day in (date.day, 30, 29, 28):
        try:
            return dt.date(year, month, day)
        except ValueError:
            continue
    raise ValueError(date)


def current_salary(employee: Employee, today: dt.date) -> int:
    period, step = RAISES[employee.raise_rule]
    raises = _months_between(employee.start_date, today) // period
    return min(employee.base_salary + raises * step, SALARY_MAX)


def next_raise_date(employee: Employee, today: dt.date) -> dt.date | None:
    if current_salary(employee, today) >= SALARY_MAX:
        return None
    period, _ = RAISES[employee.raise_rule]
    done = _months_between(employee.start_date, today) // period
    return _add_months(employee.start_date, (done + 1) * period)


def contract_end(employee: Employee) -> dt.date:
    return _add_months(employee.start_date, employee.contract_months)
