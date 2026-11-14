"""Logging in and out, the lock after wrong passwords, Settings, and the command line."""
from datetime import timedelta

from tallyho import auth, db, workspaces
from tallyho.dates import utcnow
from tallyho.models import User, Workspace

from .conftest import OWNER_EMAIL, PASSWORD, is_logged_in, owner_id, post


def test_pages_need_a_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_and_logout(app, client):
    response = post(client, "/login", {"email": "  Owner@Example.com ", "password": PASSWORD})
    assert response.status_code == 302
    assert is_logged_in(client)
    page = client.get("/").get_data(as_text=True)
    assert "Olivia" in page  # in the menu, next to Settings
    post(client, "/logout")
    assert not is_logged_in(client)


def test_wrong_password_says_nothing_about_the_account(client):
    wrong = post(client, "/login", {"email": OWNER_EMAIL, "password": "nope nope"})
    nobody = post(client, "/login", {"email": "nobody@example.com", "password": "nope nope"})
    assert wrong.status_code == nobody.status_code == 401
    assert auth.WRONG_LOGIN in wrong.get_data(as_text=True)
    assert auth.WRONG_LOGIN in nobody.get_data(as_text=True)


def test_five_wrong_passwords_lock_the_account(app, client):
    for _ in range(auth.MAX_FAILED_LOGINS):
        post(client, "/login", {"email": OWNER_EMAIL, "password": "wrong password"})
    response = post(client, "/login", {"email": OWNER_EMAIL, "password": PASSWORD})
    assert auth.LOCKED in response.get_data(as_text=True)
    with app.app_context():
        user = db.session().get(User, owner_id(app))
        user.locked_until = utcnow() - timedelta(seconds=1)  # the 15 minutes are over
        db.session().commit()
    assert post(client, "/login", {"email": OWNER_EMAIL, "password": PASSWORD}).status_code == 302


def test_login_only_goes_back_to_pages_on_this_site(client):
    for bad in ("https://evil.example", "//evil.example", "/\\evil.example"):
        response = post(client, "/login", {"email": OWNER_EMAIL, "password": PASSWORD, "next": bad})
        assert "evil" not in response.headers["Location"]
        post(client, "/logout")
    response = post(client, "/login", {"email": OWNER_EMAIL, "password": PASSWORD, "next": "/settings"})
    assert response.headers["Location"].endswith("/settings")


def test_forms_need_the_csrf_token(client):
    response = client.post("/login", data={"email": OWNER_EMAIL, "password": PASSWORD})
    assert response.status_code == 400


def test_security_headers(client):
    response = client.get("/login")
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"


def test_change_password_logs_out_other_devices(app, owner):
    other = app.test_client()
    from .conftest import log_in_as
    log_in_as(other, owner_id(app))
    response = post(owner, "/settings", {"action": "password", "current_password": PASSWORD,
                                         "new_password": "a brand new one", "confirm_password": "a brand new one"})
    assert response.status_code == 302
    assert is_logged_in(owner)
    assert not is_logged_in(other)


def test_change_password_needs_the_current_one(owner):
    response = post(owner, "/settings", {"action": "password", "current_password": "wrong",
                                         "new_password": "a brand new one", "confirm_password": "a brand new one"})
    assert response.status_code == 400
    assert "isn&#39;t your current password" in response.get_data(as_text=True)


def test_rename_the_business_and_yourself(app, owner):
    post(owner, "/settings", {"action": "profile", "business": "Kape Corner", "name": "Olivia Cruz"})
    page = owner.get("/settings").get_data(as_text=True)
    assert "Kape Corner" in page and "Olivia Cruz" in page and "Save profile" in page
    assert post(owner, "/settings", {"action": "profile", "business": "Kape Corner", "name": ""}).status_code == 400


def test_settings_has_theme_and_log_out(owner):
    page = owner.get("/settings").get_data(as_text=True)
    assert "data-theme-settings" in page
    assert 'action="/logout"' in page


def test_a_demo_cookie_stops_working_when_the_demo_expires(app, client):
    with app.app_context():
        s = db.session()
        workspace = Workspace(name="Demo", is_demo=True, demo_expires_at=utcnow() + timedelta(hours=2))
        s.add(workspace)
        s.flush()
        user_id = auth.create_user(s, workspace, "Demo Visitor", "visitor@demo.invalid", PASSWORD).id
        s.commit()
    from .conftest import log_in_as
    log_in_as(client, user_id)
    assert is_logged_in(client)
    with app.app_context():
        s = db.session()
        s.get(User, user_id).workspace.demo_expires_at = utcnow() - timedelta(seconds=1)
        s.commit()
    assert not is_logged_in(client)


def test_demo_cannot_change_settings(app, client):
    with app.app_context():
        s = db.session()
        workspace = Workspace(name="Demo", is_demo=True, demo_expires_at=utcnow() + timedelta(hours=2))
        s.add(workspace)
        s.flush()
        user_id = auth.create_user(s, workspace, "Demo Visitor", "visitor@demo.invalid", PASSWORD).id
        s.commit()
    from .conftest import log_in_as
    log_in_as(client, user_id)
    assert post(client, "/settings", {"action": "profile", "business": "X", "name": "Hacker"}).status_code == 403
    # Developer mode is only a display choice, so the demo may switch it.
    assert post(client, "/settings", {"action": "developer", "developer_mode": "1"}).status_code == 302
    assert "disabled" in client.get("/settings").get_data(as_text=True)


def test_create_owner_command(tmp_path):
    from tallyho import create_app
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "cli.sqlite"), "DATABASE_URL": None})
    runner = app.test_cli_runner()
    assert runner.invoke(args=["upgrade-db"]).exit_code == 0
    result = runner.invoke(args=["create-owner", "--name", "Olivia", "--email", "o@example.com",
                                 "--business", "Kape Corner"], input="long enough\nlong enough\n")
    assert result.exit_code == 0, result.output
    again = runner.invoke(args=["create-owner", "--name", "Olivia", "--email", "o@example.com"],
                          input="long enough\nlong enough\n")
    assert again.exit_code != 0
    with app.app_context():
        workspace = workspaces.main_workspace(db.session())
        assert workspace.name == "Kape Corner"
    result = runner.invoke(args=["set-password", "o@example.com"], input="even longer\neven longer\n")
    assert result.exit_code == 0, result.output


def test_login_before_setup_says_what_to_do(tmp_path):
    from tallyho import create_app
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "empty.sqlite"), "DATABASE_URL": None})
    with app.app_context():
        db.create_tables()
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = "x"
    response = client.post("/login", data={"csrf_token": "x", "email": "a@b.co", "password": "whatever1"})
    assert response.status_code == 503
    assert "create-owner" in response.get_data(as_text=True)
