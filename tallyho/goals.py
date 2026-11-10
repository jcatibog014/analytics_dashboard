"""Goals (monthly targets, pace and forecast) and alerts (days that broke a rule).

Pace and forecast follow the week's rhythm: a shop that sells twice as much on
Saturday should be "on pace" with less done by Friday. The weights are each
weekday's average over the 8 weeks before the month (from the daily rollup).

    expected by today = target x (weights of the days so far / weights of the whole month)
    forecast          = so far + the usual amount for each day still to come

Alerts compare a day with the same weekday in the 4 weeks before it (a Monday
with Mondays), so a quiet Sunday isn't a "drop" every week.
"""
import calendar
from datetime import timedelta

from flask import g
from sqlalchemy import delete, func, select

from . import analytics
from .dates import today, utcnow
from .models import Alert, AlertRule, Goal, Workspace

GOAL_MEASURES = [("revenue", "Revenue", "money"), ("orders", "Orders", "number"), ("profit", "Gross profit", "money")]
GOAL_BY_KEY = {m[0]: m for m in GOAL_MEASURES}
RULE_MEASURES = [("revenue", "Revenue", "money"), ("orders", "Orders", "number"), ("aov", "Average order", "money"),
                 ("new_customers", "New customers", "number")]
RULE_BY_KEY = {m[0]: m for m in RULE_MEASURES}
WEIGHT_WEEKS = 8
ALERT_WEEKS = 4
CHECK_DAYS = 35  # alerts are about recent days only


def month_bounds(month):
    first = month.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def _filters(workspace, first, last, region_id=None, channel_id=None):
    return analytics.Filters(workspace.id, workspace.data_version or 0, first, last,
                             region_id=region_id, channel_id=channel_id)


def weekday_usual(s, workspace, measure, before, region_id=None):
    """The average amount per weekday (0 = Monday) over the 8 weeks before a day."""
    rows = analytics.trend(s, _filters(workspace, before - timedelta(days=7 * WEIGHT_WEEKS), before - timedelta(days=1),
                                       region_id), "day")
    usual = {}
    for weekday in range(7):
        values = [analytics.measure_of(r, measure) or 0 for r in rows if r["start"].weekday() == weekday]
        usual[weekday] = sum(values) / len(values) if values else 0
    return usual


def progress(s, workspace, goal, now=None):
    """How a goal is doing: so far, expected by now, forecast, status, and chart points."""
    now = now or today()
    first, last = month_bounds(goal.month)
    as_of = min(now, last)
    started = as_of >= first
    rows = analytics.trend(s, _filters(workspace, first, as_of, goal.region_id), "day") if started else []
    done = sum(analytics.measure_of(r, goal.measure) or 0 for r in rows)
    usual = weekday_usual(s, workspace, goal.measure, first, goal.region_id)
    if not any(usual.values()):
        usual = {d: 1 for d in range(7)}  # no history yet: every day counts the same
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    weights = [usual[d.weekday()] for d in days]
    total_weight = sum(weights) or 1
    elapsed = sum(w for d, w in zip(days, weights) if d <= as_of) if started else 0
    expected = goal.target * elapsed / total_weight
    remaining = sum(usual[d.weekday()] for d in days if d > as_of)
    forecast = done + remaining if started else sum(usual[d.weekday()] for d in days)
    if done >= goal.target:
        status = "reached"
    elif not started:
        status = "upcoming"
    elif as_of >= last:
        status = "missed"
    elif done >= expected * 0.98 or forecast >= goal.target:
        status = "on_track"
    else:
        status = "behind"
    points, running, pace, ahead = [], 0, 0, done
    by_day = {r["start"]: analytics.measure_of(r, goal.measure) or 0 for r in rows}
    for day, weight in zip(days, weights):
        pace += goal.target * weight / total_weight
        if started and day <= as_of:
            running += by_day.get(day, 0)
            actual, guess = running, (running if day == as_of else None)
        else:
            ahead += usual[day.weekday()]
            actual, guess = None, ahead
        points.append({"day": day, "value": actual, "second": pace, "compare": guess})
    return {"goal": goal, "first": first, "last": last, "as_of": as_of, "done": done, "expected": expected,
            "forecast": forecast, "share": done / goal.target if goal.target else None,
            "expected_share": expected / goal.target if goal.target else None,
            "status": status, "points": points, "kind": GOAL_BY_KEY[goal.measure][2],
            "days_left": max(0, (last - as_of).days) if started else (last - first).days + 1}


# ---------- Alerts ----------

def evaluate(rule, value, expected):
    """True when the day broke the rule."""
    if value is None or not expected:
        return False
    if rule.direction == "rise":
        return value >= expected * (1 + rule.percent / 100)
    return value <= expected * (1 - rule.percent / 100)


def check(s, workspace, first=None, last=None):
    """Look at recent days (within first..last if given) for every active rule.
    Adds alerts for days that broke a rule and removes ones that no longer do. Returns how many are new."""
    data_first, data_last = analytics.bounds(s, workspace.id, workspace.data_version or 0)
    if data_last is None:
        return 0
    start = max(data_last - timedelta(days=CHECK_DAYS - 1), data_first + timedelta(days=7 * ALERT_WEEKS))
    end = data_last
    if first is not None:
        start = max(start, first)
    if last is not None:
        end = min(end, last)
    if start > end:
        return 0
    rules = s.scalars(select(AlertRule).where(AlertRule.workspace_id == workspace.id,
                                              AlertRule.is_active.is_(True))).all()
    new = 0
    for rule in rules:
        # source="rollup" skips the cache: this can run inside an import that might still roll back.
        rows = analytics.trend(s, _filters(workspace, start - timedelta(days=7 * ALERT_WEEKS), end, rule.region_id,
                                           rule.channel_id), "day", source="rollup")
        by_day = {r["start"]: r for r in rows}
        existing = {a.day: a for a in s.scalars(select(Alert).where(Alert.rule_id == rule.id, Alert.day >= start,
                                                                    Alert.day <= end))}
        day = start
        while day <= end:
            row = by_day.get(day)
            earlier = [by_day.get(day - timedelta(days=7 * k)) for k in range(1, ALERT_WEEKS + 1)]
            value = analytics.measure_of(row, rule.measure) if row else None
            expected = _usual(earlier, rule.measure)
            broke = evaluate(rule, value, expected)
            found = existing.get(day)
            if broke and found is None:
                s.add(Alert(workspace_id=workspace.id, rule_id=rule.id, day=day, value=float(value),
                            expected=float(expected), created_at=utcnow()))
                new += 1
            elif broke:
                found.value, found.expected = float(value), float(expected)
            elif found is not None:
                s.delete(found)
            day += timedelta(days=1)
    s.flush()
    return new


def _usual(rows, measure):
    """The usual amount from the same weekday in earlier weeks (ratios from the totals)."""
    rows = [r for r in rows if r is not None]
    if len(rows) < ALERT_WEEKS:
        return None
    if measure == "aov":
        orders = sum(r["orders"] for r in rows)
        return sum(r["revenue"] for r in rows) / orders if orders else None
    return sum(analytics.measure_of(r, measure) or 0 for r in rows) / len(rows)


PLURAL_MEASURES = ("orders", "new_customers")


def verb(rule, past=False):
    """ "Orders were", "Revenue was" """
    if rule.measure in PLURAL_MEASURES:
        return "were" if past else "are"
    return "was" if past else "is"


def subject(rule):
    """ "Revenue", "Orders in Cebu", "Revenue in Metro Manila (Website)" """
    text_value = RULE_BY_KEY[rule.measure][1]
    if rule.region:
        text_value += f" in {rule.region.name}"
    if rule.channel:
        text_value += f" ({rule.channel.name})"
    return text_value


def describe(rule):
    side = "below" if rule.direction == "drop" else "above"
    return f"{subject(rule)} {verb(rule)} {rule.percent}% or more {side} the usual for that weekday"


def unseen_count():
    if getattr(g, "workspace", None) is None:
        return 0
    from . import db
    return db.session().scalar(select(func.count()).select_from(Alert).where(
        Alert.workspace_id == g.workspace.id, Alert.seen_at.is_(None))) or 0


def after_change(s, workspace, first, last):
    """New sales arrived (importer.after_change): check the days they touched."""
    check(s, workspace, first, last)


def tick_job(s, moment):
    """The timer checks every workspace's recent days (not the demo template, not finished demos)."""
    moment = moment or utcnow()
    new = 0
    for workspace in s.scalars(select(Workspace).where(Workspace.is_template.is_(False))):
        if workspace.is_demo and workspace.demo_expires_at and workspace.demo_expires_at <= moment:
            continue
        new += check(s, workspace)
    s.commit()
    return {"alerts": new}


def clear_alerts_for_rule(s, rule):
    s.execute(delete(Alert).where(Alert.rule_id == rule.id).execution_options(synchronize_session=False))


def init_app(app):
    from . import importer, tick
    if after_change not in importer.AFTER_CHANGE:
        importer.AFTER_CHANGE.append(after_change)
    if tick_job not in tick.JOBS:
        tick.JOBS.append(tick_job)
    app.jinja_env.globals["new_alert_count"] = unseen_count

