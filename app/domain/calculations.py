import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MONEY_QUANTUM = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def add_months(value: date, months: int) -> date:
    """Add calendar months, clamping the day to the destination month."""
    if months < 1:
        raise ValueError("months must be positive")
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def renewed_rent(previous: Decimal, rate: Decimal) -> Decimal:
    if rate < Decimal("-1"):
        raise ValueError("IPC rate cannot reduce rent below zero")
    return money(previous * (Decimal("1") + rate))


def premium(monthly_rent: Decimal, months: int) -> Decimal:
    return money(monthly_rent * months)
