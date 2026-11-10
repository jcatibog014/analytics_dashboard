"""Goals (pace and forecast follow the week) and alerts (a day against the same weekday)."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tallyho import auth, db, goals, workspaces
from tallyho.models import Alert, AlertRule, Goal, Workspace

from .conftest import log_in_as, post
from .test_analytics import load


def week_pattern(first, last, saturday=3000, other=1000, skip=()):
    """One order a day: P1,000, or P3,000 on Saturdays."""
    lines = ["Order,Date,SKU,Qty,Price,Region"]
    day = first
    while day <= last:
        if day not in skip:
            lines.append(f"D{day:%Y%m%d},{day.isoformat()},X,1,{saturday if day.weekday() == 5 else other},North")
        day += timedelta(days=1)
    return "\n".join(lines) + "\n"


def workspace(app):
    return workspaces.main_workspace(db.session())


def test_pace_and_forecast_follow_the_week(app):
    # Eight weeks of history before March, then March 1-10.
    load(app, week_pattern(date(2026, 1, 4), date(2026, 3, 10)))
    with app.app_context():
        s = db.session()
        goal = Goal(workspace_id=workspace(app).id, measure="revenue", month=date(2026, 3, 1), target=3_800_000)
        s.add(goal)
        s.commit()
        result = goals.progress(s, workspace(app), goal, now=date(2026, 3, 10))
    # March 2026 has 4 Saturdays: 27 x 1,000 + 4 x 3,000 = 39,000 usual.
    # By the 10th (Saturday the 7th is in it): 9 x 1,000 + 3,000 = 12,000.
    assert result["done"] == 1_200_000
    assert result["expected"] == pytest.approx(3_800_000 * 12_000 / 39_000)
    assert result["forecast"] == pytest.approx(1_200_000 + (39_000 - 12_000) * 100)
    assert result["status"] == "on_track"  # P12,000 done is more than the P11,692 expected by now
    assert result["days_left"] == 21
    assert result["points"][9]["value"] == 1_200_000 and result["points"][10]["value"] is None
    assert result["points"][-1]["compare"] == pytest.approx(result["forecast"])
    assert result["points"][-1]["second"] == pytest.approx(3_800_000)


def test_goal_statuses(app):
    load(app, week_pattern(date(2026, 1, 4), date(2026, 3, 10)))
    with app.app_context():
        s = db.session()
        ws = workspace(app)
        reached = goals.progress(s, ws, Goal(workspace_id=ws.id, measure="orders", month=date(2026, 3, 1), target=5),
                                 now=date(2026, 3, 10))
        behind = goals.progress(s, ws, Goal(workspace_id=ws.id, measure="revenue", month=date(2026, 3, 1),
                                            target=10_000_000), now=date(2026, 3, 10))
        upcoming = goals.progress(s, ws, Goal(workspace_id=ws.id, measure="revenue", month=date(2026, 4, 1),
                                              target=1), now=date(2026, 3, 10))
        missed = goals.progress(s, ws, Goal(workspace_id=ws.id, measure="revenue", month=date(2026, 2, 1),
                                            target=99_999_999), now=date(2026, 3, 10))
    assert (reached["status"], behind["status"], upcoming["status"], missed["status"]) == \
           ("reached", "behind", "upcoming", "missed")


def test_goal_pages(app, owner):
    load(app, week_pattern(date.today() - timedelta(days=80), date.today()))
    month = date.today().strftime("%Y-%m")
    assert post(owner, "/goals", {"measure": "revenue", "month": month, "target": "50,000"}).status_code == 302
    post(owner, "/goals", {"measure": "revenue", "month": month, "target": "60000"})  # the same goal again
    with app.app_context():
        found = db.session().scalars(select(Goal)).all()
        assert [(g.target, g.measure) for g in found] == [(6_000_000, "revenue")]
        goal_id = found[0].id
    page = owner.get("/goals").get_data(as_text=True)
    for text in ("Expected by today", "Forecast for the month", "Pace to the goal", "of the goal", "meter-"):
        assert text in page, text
    bad = post(owner, "/goals", {"measure": "revenue", "month": month, "target": "-5"})
    assert bad.status_code == 400 and "bigger than 0" in bad.get_data(as_text=True)
    post(owner, f"/goals/{goal_id}/delete")
    with app.app_context():
        assert db.session().scalar(select(Goal)) is None


# ---------- Alerts ----------

def test_a_quiet_monday_is_an_alert_but_a_quiet_sunday_is_not(app):
    first = date(2026, 1, 5)  # a Monday
    last = date(2026, 3, 2)   # a Monday too
    load(app, week_pattern(first, last))
    with app.app_context():
        s = db.session()
        rule = AlertRule(workspace_id=workspace(app).id, measure="revenue", direction="drop", percent=20)
        s.add(rule)
        s.commit()
        assert goals.check(s, workspace(app)) == 0  # every day is like its weekday
        s.commit()
    # Next Monday sells half: P500.
    load(app, f"Order,Date,SKU,Qty,Price,Region\nLOW,2026-03-09,X,1,500,North\n")
    with app.app_context():
        alerts = db.session().scalars(select(Alert)).all()
        assert [(a.day, a.value, a.expected) for a in alerts] == [(date(2026, 3, 9), 50000.0, 100000.0)]
        assert alerts[0].change == pytest.approx(-0.5)


def test_rises_regions_and_average_order(app):
    load(app, week_pattern(date(2026, 1, 5), date(2026, 3, 1)) +
         "BIG,2026-03-02,X,1,2500,North\n")
    with app.app_context():
        s = db.session()
        ws = workspace(app)
        rise = AlertRule(workspace_id=ws.id, measure="aov", direction="rise", percent=50)
        s.add(rise)
        s.commit()
        goals.check(s, ws)
        s.commit()
        assert [(a.day, a.value) for a in s.scalars(select(Alert))] == [(date(2026, 3, 2), 250000.0)]


def test_alert_pages_and_the_menu_count(app, owner):
    load(app, week_pattern(date(2026, 1, 5), date(2026, 3, 8)) + "LOW,2026-03-09,X,1,500,North\n")
    response = post(owner, "/alerts/rules", {"measure": "revenue", "direction": "drop", "percent": "20"})
    assert response.status_code == 302
    page = owner.get("/alerts").get_data(as_text=True)
    assert "Monday, Mar 9, 2026" in page and "50% below the usual" in page and 'class="count">1<' in page
    post(owner, "/alerts/seen")
    assert 'class="count"' not in owner.get("/alerts").get_data(as_text=True)
    with app.app_context():
        rule_id = db.session().scalar(select(AlertRule.id))
    post(owner, f"/alerts/rules/{rule_id}", {"action": "toggle"})
    with app.app_context():
        assert db.session().get(AlertRule, rule_id).is_active is False
    post(owner, f"/alerts/rules/{rule_id}", {"action": "delete"})
    with app.app_context():
        assert db.session().scalar(select(Alert)) is None  # its alerts went with it
    bad = post(owner, "/alerts/rules", {"measure": "revenue", "direction": "drop", "percent": "0"},
               follow_redirects=True)
    assert "between 1 and 500" in bad.get_data(as_text=True)


def test_another_workspace_cannot_touch_our_goals_or_rules(app, owner):
    month = date.today().strftime("%Y-%m")
    post(owner, "/goals", {"measure": "orders", "month": month, "target": "100"})
    post(owner, "/alerts/rules", {"measure": "orders", "direction": "drop", "percent": "30"})
    with app.app_context():
        s = db.session()
        goal_id, rule_id = s.scalar(select(Goal.id)), s.scalar(select(AlertRule.id))
        other = Workspace(name="Other")
        s.add(other)
        s.flush()
        user_id = auth.create_user(s, other, "Other", "other@example.com", "correct horse").id
        s.commit()
    stranger = log_in_as(app.test_client(), user_id)
    assert post(stranger, f"/goals/{goal_id}/delete").status_code == 404
    assert post(stranger, f"/alerts/rules/{rule_id}", {"action": "delete"}).status_code == 404
    assert "30% or more" not in stranger.get("/alerts").get_data(as_text=True)


def test_the_timer_checks_alerts(app):
    load(app, week_pattern(date(2026, 1, 5), date(2026, 3, 8)) + "LOW,2026-03-09,X,1,500,North\n")
    with app.app_context():
        s = db.session()
        s.add(AlertRule(workspace_id=workspace(app).id, measure="revenue", direction="drop", percent=20))
        s.commit()
    from tallyho import tick
    with app.test_request_context():
        assert tick.run(db.session(), None)["alerts"] == 1
