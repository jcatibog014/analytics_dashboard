from datetime import date

from tallyho import formatting as f


def test_money_is_centavos():
    assert f.money(123450) == "₱1,235"
    assert f.money(123450, cents=True) == "₱1,234.50"
    assert f.money(-120000) == "-₱1,200"
    assert f.money(None) == "—"


def test_short_numbers():
    assert f.short_number(950) == "950"
    assert f.short_number(1200) == "1.2K"
    assert f.short_number(2_000_000) == "2M"
    assert f.short_number(-35_400) == "-35.4K"
    assert f.money_short(120_000_000) == "₱1.2M"
    assert f.money_short(-3_500_000) == "-₱35K"


def test_percent():
    assert f.percent(0.1234) == "12.3%"
    assert f.percent(0.5) == "50%"
    assert f.percent(-0.05) == "-5%"
    assert f.percent(None) == "—"


def test_date_range():
    assert f.date_range(date(2026, 10, 1), date(2026, 10, 31)) == "Oct 1 – 31, 2026"
    assert f.date_range(date(2026, 10, 1), date(2026, 11, 15)) == "Oct 1 – Nov 15, 2026"
    assert f.date_range(date(2025, 12, 1), date(2026, 1, 31)) == "Dec 1, 2025 – Jan 31, 2026"
    assert f.date_range(date(2026, 3, 4), date(2026, 3, 4)) == "Mar 4, 2026"
