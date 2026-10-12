"""Gregorian -> Jalali (Solar Hijri) conversion for display."""

import datetime as dt

MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_jalali(date: dt.date) -> tuple[int, int, int]:
    gy, gm, gd = date.year, date.month, date.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
        + gd + g_d_m[gm - 1]
    )
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def persian_digits(value) -> str:
    return str(value).translate(_PERSIAN_DIGITS)


def format_jalali(date: dt.date) -> str:
    y, m, d = to_jalali(date)
    return persian_digits(f"{y}/{m:02d}/{d:02d}")


def format_jalali_long(date: dt.date) -> str:
    y, m, d = to_jalali(date)
    return persian_digits(f"{d} {MONTHS[m - 1]} {y}")
