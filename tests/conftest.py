import os

import pytest

from tallyho import auth, create_app, db, workspaces
from tallyho.models import User

# Tests use a fresh SQLite file per test. To check everything on PostgreSQL
# too, set TEST_DATABASE_URL to an EMPTY Neon branch (its tables are dropped
# and made again for every test, so never point it at the live database).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CSRF = "test-csrf-token"
PASSWORD = "correct horse"
OWNER_EMAIL = "owner@example.com"


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.sqlite"),
        "DATABASE_URL": TEST_DATABASE_URL,
        "SERVER_NAME": "localhost",
    })
    with app.app_context():
        if TEST_DATABASE_URL:
            db.drop_tables()
        db.create_tables()
        s = db.session()
        workspace = workspaces.create_main_workspace(s, "Test Shop")
        auth.create_user(s, workspace, "Olivia Owner", OWNER_EMAIL, PASSWORD)
        s.commit()
    return app


@pytest.fixture
def ctx(app):
    """An app context, for tests that use db.session() directly."""
    with app.app_context():
        yield db.session()


@pytest.fixture
def client(app):
    client = app.test_client()
    # Every POST needs the session's CSRF token. Put a known one in the session.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client


@pytest.fixture
def owner(app, client):
    """A client logged in as the owner."""
    return log_in_as(client, owner_id(app))


def post(client, url, data=None, **kwargs):
    """POST a form with the right CSRF token, like a real page would."""
    # Logging in or out starts a new session (and a new token), so set it every time.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client.post(url, data={"csrf_token": CSRF, **(data or {})}, **kwargs)


def owner_id(app):
    with app.app_context():
        s = db.session()
        return auth.find_user(s, workspaces.main_workspace(s), OWNER_EMAIL).id


def main_workspace_id(app):
    with app.app_context():
        return workspaces.main_workspace(db.session()).id


def log_in_as(client, user_id):
    """Skip the login form: put the user straight into the session."""
    with client.application.app_context():
        version = db.session().get(User, user_id).session_version
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["session_version"] = version
        session["csrf"] = CSRF
    return client


def is_logged_in(client):
    """The real check: load_user() runs on every request and drops a session that
    is no longer valid, so ask after a request what is left in the cookie."""
    client.get("/settings")
    with client.session_transaction() as session:
        return "user_id" in session
