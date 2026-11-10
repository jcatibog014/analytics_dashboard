"""Logging in and out, Settings, and protection for every form.

Tally Ho! has one owner (made on the command line with
"flask --app tallyho create-owner") and visitors who click Try Demo
(demo.py). There is no sign-up: the numbers are the owner's business.

Passwords are stored only as hashes. The login cookie holds the user's id
and their session_version; load_user() checks both on every request, so an
old cookie (after a password change) is logged out straight away.
"""
import functools
import hmac
import re
import secrets
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, workspaces
from .dates import utcnow
from .models import User

bp = Blueprint("auth", __name__)

NAME_MAX_LENGTH = 80
EMAIL_MAX_LENGTH = 254
PASSWORD_MIN_LENGTH = 8
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# After 5 wrong passwords in a row, the account waits 15 minutes.
MAX_FAILED_LOGINS = 5
LOCK_TIME = timedelta(minutes=15)

WRONG_LOGIN = "Wrong email or password."
LOCKED = "Too many failed attempts. Please wait 15 minutes and try again."


# ---------- Checking what people type ----------

def clean_email(text):
    return (text or "").strip().lower()[:EMAIL_MAX_LENGTH + 1]


def email_problem(email):
    if not email:
        return "Enter your email."
    if len(email) > EMAIL_MAX_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        return "That doesn't look like an email address."
    return None


def name_problem(name):
    if not name:
        return "Enter your name."
    if len(name) > NAME_MAX_LENGTH:
        return f"Use at most {NAME_MAX_LENGTH} characters."
    return None


def password_problem(password, confirm):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if password != confirm:
        return "The two passwords are not the same."
    return None


# ---------- Users in the database ----------

def find_user(s, workspace, email):
    if workspace is None:
        return None
    return s.scalar(select(User).where(User.workspace_id == workspace.id, User.email == clean_email(email)))


def create_user(s, workspace, name, email, password):
    """Add a user to the workspace. The caller commits."""
    user = User(workspace_id=workspace.id, name=name, email=clean_email(email),
                password_hash=generate_password_hash(password))
    user.workspace = workspace
    s.add(user)
    s.flush()
    return user


def set_password(user, password):
    user.password_hash = generate_password_hash(password)
    user.failed_logins = 0
    user.locked_until = None
    user.session_version += 1  # every other device is logged out


def check_login(s, workspace, email, password, moment):
    """Return (user, None) if the password is right, else (None, message).
    The message never says whether the email has an account."""
    user = find_user(s, workspace, email)
    if user is None or not user.is_active:
        return None, WRONG_LOGIN
    if user.locked_until and user.locked_until > moment:
        return None, LOCKED
    if not check_password_hash(user.password_hash, password or ""):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.failed_logins, user.locked_until = 0, moment + LOCK_TIME
        s.commit()
        return None, WRONG_LOGIN
    user.failed_logins, user.locked_until = 0, None
    s.commit()
    return user, None


def safe_next(target, default=None):
    """Only go back to a page on this site after logging in.

    "/reports" is fine. "https://evil.example" and "//evil.example" are not:
    a fake link could send people there after a real login. Browsers drop tabs
    and newlines and read a backslash as a slash, so those are out too.
    """
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not any(ord(c) < 32 for c in target)):
        return target
    return default or url_for("main.home")


def log_in(user):
    session.clear()  # a new session for the new user (old cookies can't be reused)
    session.permanent = True  # stay logged in for PERMANENT_SESSION_LIFETIME
    session["user_id"] = user.id
    session["session_version"] = user.session_version


# ---------- Every request ----------

@bp.before_app_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        user = db.session().get(User, user_id)
        expired = (user is not None and user.workspace.is_demo and user.workspace.demo_expires_at
                   and user.workspace.demo_expires_at <= utcnow())
        if user is None or session.get("session_version") != user.session_version or not user.is_active or expired:
            # Keep the CSRF token: only the login part of this cookie is out of date.
            session.pop("user_id", None)
            session.pop("session_version", None)
        else:
            g.user = user
    g.workspace = g.user.workspace if g.user else None


def csrf_token():
    """A secret per session, put into every form as a hidden field.

    Another website could make your browser send a form to this site (your
    login cookie goes with it). It can't read this secret, so its form fails.
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if request.method != "POST" or request.endpoint in current_app.config.get("CSRF_EXEMPT", ()):
        return
    sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    # compare_digest takes the same time for every wrong guess.
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "This form has expired. Please go back, reload the page and try again.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            # Only a GET can be repeated after logging in.
            next_page = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=next_page))
        return view(*args, **kwargs)
    return wrapped


def not_for_demo(message="The demo can't change this."):
    if g.user is not None and g.user.is_demo:
        abort(403, message)


# ---------- Pages ----------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("main.home"))
    next_page = request.values.get("next", "")
    error = None
    email = ""
    if request.method == "POST":
        s = db.session()
        workspace = workspaces.main_workspace(s)
        if workspace is None:
            abort(503, "Tally Ho! isn't set up yet. The owner runs: flask --app tallyho create-owner")
        email = clean_email(request.form.get("email", ""))
        user, error = check_login(s, workspace, email, request.form.get("password", ""), utcnow())
        if user:
            log_in(user)
            return redirect(safe_next(next_page))
    status = 401 if error else 200
    return render_template("login.html", error=error, email=email, next=next_page), status


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("auth.login"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    errors = {}
    if request.method == "POST":
        not_for_demo("The demo can't change its account.")
        action = request.form.get("action")
        s = db.session()
        if action == "name":
            name = request.form.get("name", "").strip()
            problem = name_problem(name)
            if problem:
                errors["name"] = problem
            else:
                g.user.name = name
                s.commit()
                flash("Your name has been changed.")
                return redirect(url_for("auth.settings"))
        elif action == "business":
            name = request.form.get("business", "").strip()
            problem = name_problem(name)
            if problem:
                errors["business"] = problem.replace("your name", "the name")
            else:
                g.workspace.name = name
                s.commit()
                flash("The business name has been changed.")
                return redirect(url_for("auth.settings"))
        elif action == "password":
            if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
                errors["password_current"] = "That isn't your current password."
            new = request.form.get("new_password", "")
            problem = password_problem(new, request.form.get("confirm_password", ""))
            if problem:
                errors["new_password"] = problem
            if not errors:
                set_password(g.user, new)
                s.commit()
                # This device stays logged in; every other one is logged out.
                session["session_version"] = g.user.session_version
                flash("Your password has been changed.")
                return redirect(url_for("auth.settings"))
        else:
            abort(400, "Choose something to change.")
    return render_template("settings.html", errors=errors), 400 if errors else 200


# ---------- Command line ----------

def ask_password():
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise click.ClickException(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    return password


@click.command("create-owner")
@with_appcontext
@click.option("--name", prompt="Your name")
@click.option("--email", prompt="Your email")
@click.option("--business", default=workspaces.MAIN_NAME, help="Your business's name (you can change it in Settings).")
def create_owner_command(name, email, business):
    """Make the workspace (the first time) and the owner's login."""
    s = db.session()
    email = clean_email(email)
    problem = name_problem(name.strip()) or email_problem(email)
    if problem:
        raise click.ClickException(problem)
    workspace = workspaces.main_workspace(s) or workspaces.create_main_workspace(s, business.strip() or workspaces.MAIN_NAME)
    if find_user(s, workspace, email):
        raise click.ClickException(f"{email} already has an account. Use set-password to change the password.")
    create_user(s, workspace, name.strip(), email, ask_password())
    s.commit()
    click.echo(f"Made the owner {email} for {workspace.name}. Log in at /login.")


@click.command("set-password")
@with_appcontext
@click.argument("email")
def set_password_command(email):
    """Change the owner's password (and unlock the account). Also ends their sessions."""
    s = db.session()
    user = find_user(s, workspaces.main_workspace(s), email)
    if user is None:
        raise click.ClickException(f"There is no account for {email}.")
    set_password(user, ask_password())
    s.commit()
    click.echo("The password is changed.")


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    for command in (create_owner_command, set_password_command):
        app.cli.add_command(command)
