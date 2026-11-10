"""The timer: what should happen even when nobody opens the site.

Each run:
  1. the Monday summary: last week's numbers by email, for owners who asked for it
     in Settings (once per week: users.last_summary_week remembers);
  2. the alert rules (goals.py, from stage 7);
  3. finished demos are deleted (demo.py, from stage 9).

Render's free plan has no timer and sleeps when idle, so a free GitHub Actions
schedule (.github/workflows/tick.yml) calls POST /tasks/tick every hour with a
secret key (TICK_KEY). From the computer: flask --app tallyho tick.
"""
import hmac
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, jsonify, request, url_for
from flask.cli import with_appcontext
from sqlalchemy import select

from . import analytics, db, mailer
from .dates import today, utcnow
from .formatting import date_range, kpi
from .models import User, Workspace

bp = Blueprint("tick", __name__)

# Extra jobs other modules add (alerts, demo clean-up): each is job(s, now) -> {name: count}.
JOBS = []


def last_full_week(day):
    """Monday..Sunday of the week before day's week."""
    monday = day - timedelta(days=day.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def summary_text(workspace, first, last, now, before):
    lines = [f"Hello! Here is {workspace.name}'s week, {date_range(first, last)}.", ""]
    for key, label, kind, _good, _meaning in analytics.KPIS:
        value = now.get(key)
        old = before.get(key)
        change = ""
        if kind != "percent":
            moved = analytics.change(value, old)
            if moved is not None:
                change = f" ({moved * 100:+.0f}% on the week before)"
        elif value is not None and old is not None:
            change = f" ({(value - old) * 100:+.1f} points on the week before)"
        lines.append(f"{label}: {kpi(value, kind)}{change}")
    link = url_for("main.home", range="custom", _external=True, **{"from": first.isoformat(), "to": last.isoformat()})
    lines += ["", f"See everything: {link}", "",
              "You get this because the weekly email is on in Settings. Turn it off there any time."]
    return "\n".join(lines)


def send_summaries(s, day):
    """Email last week's numbers to everyone who asked. Returns how many were sent."""
    first, last = last_full_week(day)
    sent = 0
    people = s.scalars(select(User).join(Workspace).where(
        User.weekly_email.is_(True), User.is_active.is_(True), Workspace.is_demo.is_(False),
        (User.last_summary_week.is_(None)) | (User.last_summary_week < first))).all()
    for person in people:
        workspace = person.workspace
        f = analytics.Filters(workspace.id, workspace.data_version or 0, first, last)
        now = analytics.kpis(s, f)
        before = analytics.kpis(s, analytics.Filters(workspace.id, workspace.data_version or 0,
                                                     first - timedelta(days=7), last - timedelta(days=7)))
        try:
            mailer.send(person.email, f"{workspace.name}: your week in numbers ({date_range(first, last)})",
                        summary_text(workspace, first, last, now, before))
        except Exception:  # noqa: BLE001 (any mail problem: try again next hour)
            current_app.logger.exception("The weekly summary to %s failed; it will be tried again.", person.email)
            continue
        person.last_summary_week = first
        s.commit()
        sent += 1
    return sent


def run(s, moment):
    report = {"summaries": send_summaries(s, today())}
    for job in JOBS:
        report.update(job(s, moment))
    s.commit()
    return report


@bp.post("/tasks/tick")
def tick():
    key = current_app.config.get("TICK_KEY") or ""
    sent = request.headers.get("X-Tick-Key", "")
    if not key or not hmac.compare_digest(sent, key):
        abort(404)
    return jsonify(run(db.session(), utcnow()))


@click.command("tick")
@with_appcontext
def tick_command():
    """Run the timer once (weekly emails, alerts, old demos)."""
    base = current_app.config.get("BASE_URL") or "http://127.0.0.1:5011"
    with current_app.test_request_context(base_url=base):  # links in emails need the site's address
        report = run(db.session(), utcnow())
    click.echo(", ".join(f"{name}: {value}" for name, value in report.items()))


def init_app(app):
    app.config.setdefault("CSRF_EXEMPT", set()).add("tick.tick")  # it has its own key instead
    app.cli.add_command(tick_command)
