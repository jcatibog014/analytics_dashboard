"""Goals (monthly targets) and Alerts (days that broke a rule)."""
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select, update

from .. import analytics, charts, db, goals, workspaces
from ..auth import login_required
from ..dates import today, utcnow
from ..formatting import kpi, nice_date
from ..models import Alert, AlertRule, Goal

bp = Blueprint("goals", __name__)

STATUS = {
    "reached": ("Reached", "good", "check"),
    "on_track": ("On track", "good", "up"),
    "behind": ("Behind", "warn", "warning"),
    "missed": ("Missed", "bad", "x"),
    "upcoming": ("Not started", "neutral", "clock"),
}


def parse_amount(text_value, kind):
    """ "150,000" -> 15000000 centavos (money) or 150000 (a count)."""
    cleaned = (text_value or "").replace(",", "").replace("₱", "").strip()
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    if amount <= 0 or amount > 10_000_000_000:
        return None
    return round(amount * 100) if kind == "money" else round(amount)


def goal_chart(result):
    kind = result["kind"]
    label = goals.GOAL_BY_KEY[result["goal"].measure][1]
    points = []
    for point in result["points"]:
        rows = []
        if point["value"] is not None:
            rows.append(("main", "So far", charts.fmt(kind, point["value"])))
        rows.append(("second", "Pace to the goal", charts.fmt(kind, point["second"])))
        if point["compare"] is not None:
            rows.append(("compare", "Forecast", charts.fmt(kind, point["compare"])))
        points.append({"label": f"{point['day']:%b} {point['day'].day}", "value": point["value"],
                       "second": point["second"], "compare": point["compare"],
                       "tip": {"label": f"{label}, {nice_date(point['day'])}", "rows": rows}})
    return charts.line_charts(points, kind, compare=True, second=True, wide=560)  # a half-width card


@bp.route("/goals", methods=["GET", "POST"])
@login_required
def index():
    s = db.session()
    errors = {}
    form = {"measure": "revenue", "month": today().replace(day=1).isoformat()[:7], "target": "", "region": ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "") for k in form}
        measure = form["measure"] if form["measure"] in goals.GOAL_BY_KEY else None
        try:
            month = date.fromisoformat(form["month"] + "-01")
        except ValueError:
            month = None
        region_id = None
        if form["region"]:
            regions = dict(analytics.choices(s, g.workspace.id, g.workspace.data_version)["regions"])
            region_id = int(form["region"]) if form["region"].isdigit() and int(form["region"]) in regions else None
        target = parse_amount(form["target"], goals.GOAL_BY_KEY[measure][2]) if measure else None
        if measure is None:
            errors["measure"] = "Choose what the goal is for."
        if month is None:
            errors["month"] = "Choose a month."
        if target is None:
            errors["target"] = "Enter a number bigger than 0."
        if not errors:
            existing = s.scalar(select(Goal).where(Goal.workspace_id == g.workspace.id, Goal.measure == measure,
                                                   Goal.month == month, Goal.region_id.is_(region_id)
                                                   if region_id is None else Goal.region_id == region_id))
            if existing:
                existing.target = target
                flash("Goal updated.")
            else:
                s.add(Goal(workspace_id=g.workspace.id, measure=measure, month=month, target=target,
                           region_id=region_id))
                flash("Goal set.")
            s.commit()
            return redirect(url_for("goals.index"))
    this_month = today().replace(day=1)
    all_goals = s.scalars(select(Goal).where(Goal.workspace_id == g.workspace.id)
                          .order_by(Goal.month.desc(), Goal.measure)).all()
    current = [goals.progress(s, g.workspace, goal) for goal in all_goals if goal.month == this_month]
    for result in current:
        result["chart"] = goal_chart(result)
    other = [goals.progress(s, g.workspace, goal) for goal in all_goals if goal.month != this_month]
    options = analytics.choices(s, g.workspace.id, g.workspace.data_version)
    return render_template("goals/index.html", current=current, other=other, errors=errors, form=form,
                           measures=goals.GOAL_MEASURES, regions=options["regions"], status=STATUS, kpi=kpi,
                           measure_names={key: name for key, name, _kind in goals.GOAL_MEASURES},
                           this_month=this_month), 400 if errors else 200


@bp.post("/goals/<int:goal_id>/delete")
@login_required
def delete(goal_id):
    s = db.session()
    goal = workspaces.get_in_workspace(s, Goal, goal_id, g.workspace)
    s.delete(goal)
    s.commit()
    flash("Goal deleted.")
    return redirect(url_for("goals.index"))


# ---------- Alerts ----------

@bp.get("/alerts")
@login_required
def alerts():
    s = db.session()
    found = s.scalars(select(Alert).where(Alert.workspace_id == g.workspace.id)
                      .order_by(Alert.day.desc(), Alert.id.desc()).limit(100)).all()
    rules = s.scalars(select(AlertRule).where(AlertRule.workspace_id == g.workspace.id)
                      .order_by(AlertRule.id)).all()
    options = analytics.choices(s, g.workspace.id, g.workspace.data_version)
    return render_template("goals/alerts.html", alerts=found, rules=rules, describe=goals.describe,
                           subject=goals.subject, verb=goals.verb,
                           measures=goals.RULE_MEASURES, rule_kind=lambda r: goals.RULE_BY_KEY[r.measure][2],
                           regions=options["regions"], channels=options["channels"], kpi=kpi,
                           unseen=sum(1 for a in found if a.seen_at is None))


@bp.post("/alerts/seen")
@login_required
def mark_seen():
    s = db.session()
    s.execute(update(Alert).where(Alert.workspace_id == g.workspace.id, Alert.seen_at.is_(None))
              .values(seen_at=utcnow()).execution_options(synchronize_session=False))
    s.commit()
    return redirect(url_for("goals.alerts"))


@bp.post("/alerts/rules")
@login_required
def add_rule():
    s = db.session()
    measure = request.form.get("measure")
    direction = request.form.get("direction")
    try:
        percent = int(request.form.get("percent", ""))
    except ValueError:
        percent = 0
    if measure not in goals.RULE_BY_KEY or direction not in ("drop", "rise") or not 1 <= percent <= 500:
        flash("Choose a number, a direction and a percent between 1 and 500.", "error")
        return redirect(url_for("goals.alerts"))
    options = analytics.choices(s, g.workspace.id, g.workspace.data_version)

    def pick(name, allowed):
        value = request.form.get(name, "")
        return int(value) if value.isdigit() and int(value) in dict(allowed) else None

    rule = AlertRule(workspace_id=g.workspace.id, measure=measure, direction=direction, percent=percent,
                     region_id=pick("region", options["regions"]), channel_id=pick("channel", options["channels"]))
    s.add(rule)
    s.flush()
    new = goals.check(s, g.workspace)
    s.commit()
    flash(f"Rule added. It found {new} day{'s' if new != 1 else ''} in the last 5 weeks." if new
          else "Rule added. No recent day breaks it.")
    return redirect(url_for("goals.alerts"))


@bp.post("/alerts/rules/<int:rule_id>")
@login_required
def change_rule(rule_id):
    s = db.session()
    rule = workspaces.get_in_workspace(s, AlertRule, rule_id, g.workspace)
    action = request.form.get("action")
    if action == "delete":
        s.delete(rule)
        flash("Rule deleted, with its alerts.")
    elif action == "toggle":
        rule.is_active = not rule.is_active
        if rule.is_active:
            goals.check(s, g.workspace)
        flash("Rule switched on." if rule.is_active else "Rule switched off. Its alerts stay until you delete it.")
    else:
        abort(400)
    s.commit()
    return redirect(url_for("goals.alerts"))
