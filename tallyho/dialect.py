"""The few places where SQLite and PostgreSQL write SQL differently.

Most SQL here is standard and runs on both: SELECT, JOIN, GROUP BY, CASE,
window functions (OVER), WITH RECURSIVE. Dates are the exception: SQLite
keeps them as text ("2026-03-25") and has date() and strftime(); PostgreSQL
has a real DATE type with date_trunc() and "+ 1" for the next day.

Every function returns a piece of SQL text for an expression we wrote
ourselves (a column name). User input never goes in here: it always goes in
as a bound parameter (:name).
"""
from datetime import date, timedelta

GRAINS = ("day", "week", "month", "quarter", "year")
GRAIN_NAMES = {"day": "Day", "week": "Week", "month": "Month", "quarter": "Quarter", "year": "Year"}


def name_of(s):
    """ "sqlite" or "postgresql" for this session's database."""
    return s.get_bind().dialect.name


def date_param(dialect, param):
    """A bound parameter as a date (SQLite compares the text, which sorts like dates)."""
    return f"date(:{param})" if dialect == "sqlite" else f"CAST(:{param} AS DATE)"


def add_days(dialect, expression, days):
    if dialect == "sqlite":
        return f"date({expression}, '{int(days):+d} day')"
    return f"({expression} + {int(days)})"


def bucket(dialect, expression, grain):
    """The first day of the day/week/month/quarter/year that contains the date.
    Weeks start on Monday (as in the Philippines and ISO 8601)."""
    if grain not in GRAINS:
        raise ValueError(grain)
    if grain == "day":
        return expression
    if dialect == "sqlite":
        if grain == "week":
            # strftime('%w') is 0 for Sunday ... 6 for Saturday; step back to Monday.
            return f"date({expression}, '-' || ((CAST(strftime('%w', {expression}) AS INTEGER) + 6) % 7) || ' days')"
        if grain == "month":
            return f"strftime('%Y-%m-01', {expression})"
        if grain == "quarter":
            return (f"printf('%s-%02d-01', strftime('%Y', {expression}), "
                    f"((CAST(strftime('%m', {expression}) AS INTEGER) - 1) / 3) * 3 + 1)")
        return f"strftime('%Y-01-01', {expression})"
    return f"CAST(date_trunc('{grain}', {expression}) AS DATE)"


def weekday(dialect, expression):
    """0 = Monday ... 6 = Sunday (Python's weekday())."""
    if dialect == "sqlite":
        return f"((CAST(strftime('%w', {expression}) AS INTEGER) + 6) % 7)"
    return f"CAST(EXTRACT(ISODOW FROM {expression}) AS INTEGER) - 1"


def month_number(dialect, expression):
    """Months since year 0 (2026-03 -> 2026*12 + 2), so two months can be subtracted."""
    if dialect == "sqlite":
        return f"(CAST(strftime('%Y', {expression}) AS INTEGER) * 12 + CAST(strftime('%m', {expression}) AS INTEGER) - 1)"
    return f"(CAST(EXTRACT(YEAR FROM {expression}) AS INTEGER) * 12 + CAST(EXTRACT(MONTH FROM {expression}) AS INTEGER) - 1)"


def day_series(dialect, first_param, last_param):
    """A CTE "days(day)" with every date from :first to :last, so days without
    sales still get a row (a chart line must touch zero, not skip the day)."""
    return f"""days(day) AS (
        SELECT {date_param(dialect, first_param)}
        UNION ALL
        SELECT {add_days(dialect, 'day', 1)} FROM days WHERE day < {date_param(dialect, last_param)}
    )"""


# ---------- The same in Python (for filling gaps and labels) ----------

def as_date(value):
    """SQLite gives dates from hand-written SQL back as text; PostgreSQL as dates."""
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def bucket_start(day, grain):
    if grain == "day":
        return day
    if grain == "week":
        return day - timedelta(days=day.weekday())
    if grain == "month":
        return day.replace(day=1)
    if grain == "quarter":
        return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
    return date(day.year, 1, 1)


def next_bucket(start, grain):
    if grain == "day":
        return start + timedelta(days=1)
    if grain == "week":
        return start + timedelta(days=7)
    months = {"month": 1, "quarter": 3, "year": 12}[grain]
    month = start.month - 1 + months
    return date(start.year + month // 12, month % 12 + 1, 1)


def buckets(first, last, grain):
    """Every bucket start from the one holding first to the one holding last."""
    start = bucket_start(first, grain)
    out = []
    while start <= last:
        out.append(start)
        start = next_bucket(start, grain)
    return out


def bucket_label(start, grain, short=False):
    if grain == "day":
        return f"{start:%b} {start.day}"
    if grain == "week":
        return f"Week of {start:%b} {start.day}" if not short else f"{start:%b} {start.day}"
    if grain == "month":
        return f"{start:%b} {start.year}" if not short else f"{start:%b}"
    if grain == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    return str(start.year)
