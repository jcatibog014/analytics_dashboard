"""Filters for the templates: money, numbers, percents and dates the way people read them.

Money is whole centavos everywhere (123450 = P1,234.50).
"""
from datetime import date, datetime

from .dates import to_local, today, utcnow

PESO = "₱"


def money(centavos, cents=False):
    """P12,345 (or P12,345.50 with cents=True). Negative: -P1,200."""
    if centavos is None:
        return "—"
    sign = "-" if centavos < 0 else ""
    centavos = abs(int(centavos))
    if cents:
        text = f"{centavos // 100:,}.{centavos % 100:02d}"
    else:
        # Half up, in whole numbers (Python's round() would make 1234.5 into 1234).
        text = f"{(centavos + 50) // 100:,}"
    return f"{sign}{PESO}{text}"


def money_cents(centavos):
    return money(centavos, cents=True)


def short_number(value):
    """1.2K, 3.4M, 950 (for chart axes and small tiles)."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    for size, letter in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= size:
            text = f"{value / size:.1f}".rstrip("0").rstrip(".")
            return f"{sign}{text}{letter}"
    return f"{sign}{value:,.0f}"


def money_short(centavos):
    """P1.2M, P35K: money for axes and tight spaces."""
    if centavos is None:
        return "—"
    text = short_number(centavos / 100)
    return f"-{PESO}{text[1:]}" if text.startswith("-") else f"{PESO}{text}"


def number(value):
    """12,345"""
    if value is None:
        return "—"
    return f"{value:,.0f}" if isinstance(value, float) else f"{value:,}"


def percent(value, digits=1):
    """0.1234 -> 12.3%. None -> a dash (for "no data to compare with")."""
    if value is None:
        return "—"
    text = f"{value * 100:.{digits}f}"
    if digits and "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def nice_date(moment):
    """Oct 30, 2026 (written by hand: %-d doesn't work on Windows).
    Takes a date or a moment from the database (naive UTC)."""
    if isinstance(moment, datetime):
        moment = to_local(moment)
    return f"{moment:%b} {moment.day}, {moment.year}"


def short_date(day):
    """Oct 30 (this year) or Oct 30, 2027."""
    if isinstance(day, datetime):
        day = to_local(day)
    return f"{day:%b} {day.day}" if day.year == today().year else nice_date(day)


def month_name(day):
    """October 2026"""
    return f"{day:%B} {day.year}"


def clock(moment):
    """2:30 PM"""
    local = to_local(moment)
    return f"{local.hour % 12 or 12}:{local:%M %p}"


def nice_datetime(moment):
    """Oct 30, 2026, 2:30 PM"""
    return f"{nice_date(moment)}, {clock(moment)}"


def ago(moment):
    """How long ago: just now, 5 min ago, 3 h ago, yesterday, Oct 30."""
    seconds = (utcnow() - moment).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    if seconds < 7 * 86400:
        days = int(seconds // 86400)
        return "yesterday" if days == 1 else f"{days} days ago"
    return short_date(moment)


def date_range(first, last):
    """Oct 1 - 31, 2026 / Oct 1 - Nov 15, 2026 / Dec 1, 2025 - Jan 31, 2026."""
    if first == last:
        return nice_date(first)
    if first.year != last.year:
        return f"{nice_date(first)} – {nice_date(last)}"
    if first.month != last.month:
        return f"{first:%b} {first.day} – {last:%b} {last.day}, {last.year}"
    return f"{first:%b} {first.day} – {last.day}, {last.year}"


def plural(count, word, many=None):
    """1 order, 3 orders."""
    return f"{number(count)} {word if count == 1 else many or word + 's'}"


def iso(day):
    return day.isoformat() if isinstance(day, (date, datetime)) else day


def kpi(value, kind):
    """A KPI in full: money, number or percent."""
    if kind == "money":
        return money(value)
    if kind == "percent":
        return percent(value)
    return number(round(value)) if isinstance(value, float) else number(value)


def kpi_short(value, kind):
    """A KPI tile's big number: in full up to P1M / 100,000, then 1.2M."""
    if value is None:
        return "—"
    if kind == "money" and abs(value) >= 100_000_000:
        return money_short(value)
    if kind == "number" and abs(value) >= 100_000:
        return short_number(value)
    return kpi(value, kind)


def init_app(app):
    for function in (money, money_cents, money_short, short_number, number, percent, nice_date, short_date,
                     month_name, clock, nice_datetime, ago, iso, kpi, kpi_short):
        app.jinja_env.filters[function.__name__] = function
    app.jinja_env.globals.update(plural=plural, date_range=date_range, PESO=PESO)
