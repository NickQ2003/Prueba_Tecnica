from datetime import date
from decimal import Decimal

import pytest
from app.domain.calculations import add_months, premium, renewed_rent


@pytest.mark.parametrize(
    ("source", "months", "expected"),
    [
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2025, 1, 31), 1, date(2025, 2, 28)),
        (date(2024, 10, 31), 4, date(2025, 2, 28)),
    ],
)
@pytest.mark.unit
def test_add_months_uses_calendar_boundaries(source, months, expected):
    assert add_months(source, months) == expected


@pytest.mark.unit
def test_renewal_uses_decimal_and_half_up_rounding():
    assert renewed_rent(Decimal("1000000.01"), Decimal("0.08500")) == Decimal("1085000.01")
    assert premium(Decimal("1085000.01"), 12) == Decimal("13020000.12")


@pytest.mark.unit
def test_invalid_month_count_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        add_months(date(2025, 1, 1), 0)
