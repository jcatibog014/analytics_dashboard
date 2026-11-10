"""Date ranges: "Last 30 days", "This month", a custom range, and what to compare them with.

The choice lives in the page address (?range=30d&compare=previous), so a link
shows the same numbers to whoever opens it, and the menu keeps it as you move
between pages (range_args).
"""
from dataclasses import dataclass
from datetime import date, timedelta

from flask import request

from .dates import today

PRESETS = [
    ("today", "Today"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("90d", "Last 90 days"),
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("this_quarter", "This quarter"),
    ("ytd", "Year to date"),
    ("12m", "Last 12 months"),
    ("all", "All time"),
    ("custom", "Custom dates"),
]
PRESET_NAMES = dict(PRESETS)
DEFAULT = "30d"

COMPARES = [("previous", "Previous period"), ("year", "Same dates last year"), ("none", "No comparison")]
COMPARE_NAMES = dict(COMPARES)


@dataclass(frozen=True)
class Period:
    first: date
    last: date

    @property
    def days(self):
        return (self.last - self.first).days + 1

    def __contains__(self, day):
        return self.first <= day <= self.last


@dataclass(frozen=True)
class Choice:
    key: str            # the preset ("30d", "custom"...)
    period: Period
    compare_key: str    # previous / year / none
    compare: Period | None
    grain: str          # day / week / month / quarter / year

    @property
    def label(self):
        return PRESET_NAMES.get(self.key, "Custom dates")


def _clamp_day(year, month, day):
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1  # Feb 29 in a year without one -> Feb 28


def year_before(day):
    return _clamp_day(day.year - 1, day.month, day.day)


def preset_period(key, now, bounds=None):
    """The Period for a preset, ending today (or None for "custom")."""
    if key == "today":
        return Period(now, now)
    if key in ("7d", "30d", "90d"):
        return Period(now - timedelta(days=int(key[:-1]) - 1), now)
    if key == "this_month":
        return Period(now.replace(day=1), now)
    if key == "last_month":
        last = now.replace(day=1) - timedelta(days=1)
        return Period(last.replace(day=1), last)
    if key == "this_quarter":
        return Period(date(now.year, (now.month - 1) // 3 * 3 + 1, 1), now)
    if key == "ytd":
        return Period(date(now.year, 1, 1), now)
    if key == "12m":
        return Period(year_before(now) + timedelta(days=1), now)
    if key == "all":
        if bounds and bounds[0]:
            return Period(bounds[0], max(bounds[1], bounds[0]))
        return Period(now, now)
    return None


def compare_period(period, how):
    if how == "previous":
        return Period(period.first - timedelta(days=period.days), period.first - timedelta(days=1))
    if how == "year":
        return Period(year_before(period.first), year_before(period.last))
    return None


def auto_grain(period):
    """Days for a month, weeks for a season, months for longer."""
    if period.days <= 45:
        return "day"
    if period.days <= 200:
        return "week"
    if period.days <= 1100:
        return "month"
    return "quarter"


def _parse(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def choose(args, bounds=None, now=None):
    """The Choice from a page's address. Anything odd falls back to the default."""
    now = now or today()
    key = args.get("range", DEFAULT)
    period = None
    if key == "custom":
        first, last = _parse(args.get("from")), _parse(args.get("to"))
        if first and last:
            if first > last:
                first, last = last, first
            if (last - first).days > 3660:
                first = last - timedelta(days=3660)  # ten years is plenty
            period = Period(first, last)
    elif key in PRESET_NAMES:
        period = preset_period(key, now, bounds)
    if period is None:
        key, period = DEFAULT, preset_period(DEFAULT, now)
    compare_key = args.get("compare", "previous")
    if compare_key not in COMPARE_NAMES or key == "all":
        compare_key = "none" if key == "all" else "previous"
    grain = args.get("grain") or auto_grain(period)
    if grain not in ("day", "week", "month", "quarter", "year") or (grain == "day" and period.days > 400):
        grain = auto_grain(period)
    return Choice(key, period, compare_key, compare_period(period, compare_key), grain)


def args_for(choice, **changes):
    """The address parts for a choice (to build links), with some changed."""
    out = {"range": choice.key}
    if choice.key == "custom":
        out["from"], out["to"] = choice.period.first.isoformat(), choice.period.last.isoformat()
    if choice.compare_key != "previous":
        out["compare"] = choice.compare_key
    out.update(changes)
    return {k: v for k, v in out.items() if v is not None}


RANGE_KEYS = ("range", "from", "to", "compare", "grain", "region", "channel", "category")


def range_args():
    """The current page's range and filters, to keep in menu links."""
    try:
        return {k: request.args[k] for k in RANGE_KEYS if request.args.get(k)}
    except RuntimeError:
        return {}


def init_app(app):
    app.jinja_env.globals.update(range_args=range_args, PRESETS=PRESETS, COMPARES=COMPARES)
