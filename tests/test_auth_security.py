"""AIROST Day 9 authentication and authorization tests.
Run separately: python -m pytest tests/test_auth_security.py -v
Uses a disposable SQLite database; never touches the project's production database.
"""
import os
import tempfile
from pathlib import Path

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_auth_test_")
_db_path = Path(_temp.name) / "auth_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app, load_user
from database import db
from models import User

PASSWORD = "CorrectPassword123!"


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"
        db.session.remove()
        db.drop_all()
        db.create_all()
        users = [
            User(club_id="AUTH-M01", name="Member", email="m@test.local", role="member", active=True),
            User(club_id="AUTH-T01", name="Treasurer", email="t@test.local", role="treasurer", active=True),
            User(club_id="AUTH-A01", name="Admin", email="a@test.local", role="admin", active=True),
            User(club_id="AUTH-D01", name="Disabled", email="d@test.local", role="member", active=False),
            User(club_id="AUTH-X01", name="Unknown Role", email="x@test.local", role="unexpected", active=True),
        ]
        for user in users:
            user.set_password(PASSWORD)
        db.session.add_all(users)
        db.session.commit()
        ids = {u.role if u.active else "disabled": u.id for u in users}
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    client = app.test_client()
    yield client, ids
    with app.app_context():
        db.session.remove()


def login_form(client, club_id="AUTH-M01", password=PASSWORD, **extra):
    return client.post("/login", data={"club_id": club_id, "password": password, **extra})


def login_session(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def logged_in_id(client):
    with client.session_transaction() as session:
        return session.get("_user_id")


# Login and password verification

def test_login_page_accessible_to_guest(case):
    client, _ = case
    assert client.get("/login").status_code == 200


def test_correct_password_authenticates(case):
    client, ids = case
    response = login_form(client)
    assert response.status_code == 302
    assert response.location.endswith("/dashboard")
    assert logged_in_id(client) == str(ids["member"])


@pytest.mark.parametrize("club_id", ["auth-m01", "AUTH-M01", "  AuTh-M01  "])
def test_club_id_case_and_whitespace_normalized(case, club_id):
    client, ids = case
    assert login_form(client, club_id).status_code == 302
    assert logged_in_id(client) == str(ids["member"])


@pytest.mark.parametrize("club_id,password", [
    ("AUTH-M01", "wrong"),
    ("AUTH-UNKNOWN", PASSWORD),
    ("AUTH-M01", ""),
    ("AUTH-M01", "correctpassword123!"),
])
def test_invalid_credentials_rejected(case, club_id, password):
    client, _ = case
    response = login_form(client, club_id, password)
    assert response.status_code == 200
    assert b"Invalid AIROST ID or password." in response.data
    assert logged_in_id(client) is None


def test_disabled_account_cannot_login(case):
    client, _ = case
    response = login_form(client, "AUTH-D01")
    assert response.status_code == 200
    assert logged_in_id(client) is None


def test_password_stored_as_hash(case):
    _, ids = case
    with app.app_context():
        user = db.session.get(User, ids["member"])
        assert user.password_hash != PASSWORD
        assert user.check_password(PASSWORD)
        assert not user.check_password("wrong")


def test_login_isolated_between_clients(case):
    client, ids = case
    other_client = app.test_client()
    login_form(client)
    assert logged_in_id(client) == str(ids["member"])
    assert logged_in_id(other_client) is None
    assert other_client.get("/dashboard").status_code == 302


def test_already_logged_in_redirected_from_login(case):
    client, ids = case
    login_session(client, ids["member"])
    response = client.get("/login")
    assert response.status_code == 302
    assert response.location.endswith("/dashboard")


def test_remember_login_sets_cookie(case):
    client, _ = case
    response = login_form(client, remember="on")
    assert response.status_code == 302
    assert any("remember_token=" in cookie for cookie in response.headers.getlist("Set-Cookie"))


def test_login_without_remember_has_no_remember_cookie(case):
    client, _ = case
    response = login_form(client)
    assert response.status_code == 302
    assert not any("remember_token=" in cookie for cookie in response.headers.getlist("Set-Cookie"))


def test_logout_clears_authentication(case):
    client, _ = case
    login_form(client)
    assert logged_in_id(client) is not None
    response = client.post("/logout")
    assert response.status_code == 302
    assert response.location.endswith("/login")
    assert logged_in_id(client) is None
    assert client.get("/dashboard").status_code == 302


def test_guest_logout_redirects_to_login(case):
    client, _ = case
    response = client.post("/logout")
    assert response.status_code == 302
    assert "/login" in response.location


def test_missing_user_session_cannot_access_dashboard(case):
    client, _ = case
    login_session(client, 999999)
    assert client.get("/dashboard").status_code == 302


def test_disabled_user_session_cannot_access_dashboard(case):
    client, ids = case
    login_session(client, ids["disabled"])
    assert client.get("/dashboard").status_code == 302


def test_user_loader_restores_user(case):
    _, ids = case
    with app.app_context():
        assert load_user(str(ids["member"])).id == ids["member"]
        assert load_user("999999") is None


# Role-based access controls
@pytest.mark.parametrize("role,path,expected", [
    ("member", "/admin", 403), ("member", "/treasurer", 403),
    ("treasurer", "/admin", 403), ("treasurer", "/treasurer", 200),
    ("admin", "/admin", 200), ("admin", "/treasurer", 403),
    ("unexpected", "/admin", 403), ("unexpected", "/treasurer", 403),
])
def test_role_protected_routes(case, role, path, expected):
    client, ids = case
    login_session(client, ids[role])
    assert client.get(path).status_code == expected


@pytest.mark.parametrize("path", ["/", "/dashboard", "/admin", "/treasurer", "/claims/submit", "/claims/review"])
def test_guest_redirected_from_protected_routes(case, path):
    client, _ = case
    response = client.get(path)
    assert response.status_code == 302
    assert "/login" in response.location


@pytest.mark.parametrize("role", ["member", "treasurer", "admin"])
def test_known_roles_can_access_dashboard(case, role):
    client, ids = case
    login_session(client, ids[role])
    assert client.get("/dashboard").status_code == 200


def test_unknown_role_dashboard_denied(case):
    client, ids = case
    login_session(client, ids["unexpected"])
    assert client.get("/dashboard").status_code == 403


def test_member_cannot_open_review(case):
    client, ids = case
    login_session(client, ids["member"])
    assert client.get("/claims/review").status_code == 403


def test_treasurer_cannot_open_member_submit(case):
    client, ids = case
    login_session(client, ids["treasurer"])
    assert client.get("/claims/submit").status_code == 403


# CSRF protection: enabled explicitly, even though other tests disable it.
def test_csrf_rejects_login_without_token(case):
    client, _ = case
    app.config["WTF_CSRF_ENABLED"] = True
    response = login_form(client)
    assert response.status_code == 400
    assert logged_in_id(client) is None


def test_csrf_rejects_claim_submission_without_token(case):
    client, ids = case
    login_session(client, ids["member"])
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post("/claims/submit", data={"title": "Expense", "amount": "10", "event_id": "1"})
    assert response.status_code == 400


def test_csrf_rejects_review_action_without_token(case):
    client, ids = case
    login_session(client, ids["treasurer"])
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/claims/review/999999/approve").status_code == 400


# A GET request must never log the user out.
def test_logout_should_not_be_state_changing_get(case):
    client, ids = case
    login_session(client, ids["member"])
    response = client.get("/logout")
    assert response.status_code == 405
    assert logged_in_id(client) == str(ids["member"])
    assert client.get("/dashboard").status_code == 200


def test_csrf_rejects_logout_without_token(case):
    client, ids = case
    login_session(client, ids["member"])
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post("/logout")
    assert response.status_code == 400
    assert logged_in_id(client) == str(ids["member"])


# Development SECRET_KEY risk deliberately deferred.
@pytest.mark.xfail(strict=True, reason="app.py currently falls back to a predictable development SECRET_KEY")
def test_secret_key_should_not_use_development_default(case):
    assert app.config["SECRET_KEY"] != "dev-airost-change-me"


@pytest.fixture(scope="session", autouse=True)
def cleanup_auth_test_database():
    yield
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    _temp.cleanup()
