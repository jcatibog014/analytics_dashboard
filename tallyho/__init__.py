"""Tally Ho! An analytics dashboard: import your sales, then see KPIs, trends
and reports over any date range. "Every number, at a glance."

Flask builds the app inside create_app() (the "app factory" pattern).
Tests call it with their own settings, so each test gets a fresh app and database.
"""
import mimetypes
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

# Python doesn't know this one, and browsers want the right type for the app manifest.
mimetypes.add_type("application/manifest+json", ".webmanifest")

APP_NAME = "Tally Ho!"
TAGLINE = "Every number, at a glance."
DEV_SECRET_KEY = "dev-only-not-secret"
# Imports up to this size go through the web page; bigger files use "flask import-sales".
MAX_UPLOAD_MB = 20

# Content-Security-Policy: the browser may only load scripts, styles, fonts and
# images from this site, and no inline scripts or style="" attributes at all.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    on_render = bool(os.environ.get("RENDER"))  # Render sets RENDER=true on its servers
    app.config.from_mapping(
        # Signs the login cookie. On Render it comes from an environment variable.
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        # Local SQLite file, in the instance/ folder (git ignores it).
        DATABASE=str(Path(app.instance_path) / "tallyho.sqlite"),
        # Set on the live site: then we use PostgreSQL (Neon) instead of SQLite.
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        # Cookie safety: not readable by JavaScript (Flask's default), not sent
        # with forms from other sites, and only over HTTPS on the live site.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_render,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
        # Optional Gmail (an app password) for the weekly summary email.
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        # The secret the GitHub Actions timer sends to POST /tasks/tick (tick.py).
        TICK_KEY=os.environ.get("TICK_KEY"),
        BASE_URL=os.environ.get("BASE_URL"),
    )
    if test_config:
        app.config.update(test_config)
    if on_render:
        # Render's proxy talks HTTPS to the visitor and HTTP to us.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    if on_render and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Set the SECRET_KEY environment variable on Render.")
    if on_render and not app.config["DATABASE_URL"]:
        # Without it we'd use a SQLite file, and Render forgets files on every restart.
        raise RuntimeError("Set the DATABASE_URL environment variable on Render (Neon's connection string).")

    from . import auth, db, formatting, goals, ranges, rollups, tick
    from .views import data, explore, main, reports
    from .views import goals as goal_views

    db.init_app(app)
    auth.init_app(app)
    formatting.init_app(app)
    ranges.init_app(app)
    rollups.init_app(app)
    data.init_app(app)
    tick.init_app(app)
    goals.init_app(app)
    # auth comes first: its before_request (load_user) must run before the others'.
    for blueprint in (auth.bp, main.bp, explore.bp, reports.bp, goal_views.bp, data.bp, tick.bp):
        app.register_blueprint(blueprint)

    app.jinja_env.globals.update(
        APP_NAME=APP_NAME, TAGLINE=TAGLINE, MAX_UPLOAD_MB=MAX_UPLOAD_MB,
        # The menu shows a page only once it exists.
        has_page=lambda endpoint: endpoint in app.view_functions,
    )

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/favicon.ico")
    def favicon():
        # Some browsers ask for it at the root, whatever the page says.
        return app.send_static_file("icons/favicon.ico")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")  # trust our file types
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")  # no showing us inside other sites
        if on_render:
            # Always use HTTPS for this site from now on (for a year).
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return error_page(400, error.description or "Something in that request was wrong.")

    @app.errorhandler(403)
    def forbidden(error):
        return error_page(403, error.description or "You can't do that.")

    @app.errorhandler(404)
    def not_found(error):
        return error_page(404, "We couldn't find that page.")

    @app.errorhandler(409)
    def conflict(error):
        return error_page(409, error.description or "That can't be done right now.")

    @app.errorhandler(413)
    def too_big(error):
        return error_page(413, f"That file is too big. The limit is {MAX_UPLOAD_MB} MB; "
                               "for bigger files, ask the owner to use flask import-sales.")

    @app.errorhandler(503)
    def unavailable(error):
        return error_page(503, error.description or "Please try again in a minute.")

    return app
