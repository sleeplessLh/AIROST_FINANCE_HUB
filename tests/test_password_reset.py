"""Admin password reset tests (A1): temporary password + forced change at next sign-in.
Run separately: python -m pytest tests/test_password_reset.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Must run before importing app: point the app at a temporary database
_temp = tempfile.TemporaryDirectory(
    prefix="airost_password_reset_test_", ignore_cleanup_errors=True
)
_db_path = Path(_temp.name) / "password_reset_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, PasswordReset, User

PASSWORD = "OriginalPassword123!"
TEMPORARY = "TempPass2026"
NEW_PASSWORD = "BrandNewPassword456!"


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        # Safety check: refuse to run against any database except the temporary one
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "admin": User(club_id="RST-A01", name="Reset Admin", email="a@test.local", role="admin"),
            "member": User(club_id="RST-M01", name="Reset Member", email="m@test.local", role="member"),
            "treasurer": User(club_id="RST-T01", name="Reset Treasurer", email="t@test.local", role="treasurer"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        db.session.add_all(users.values())
        db.session.commit()

        ids = {role: user.id for role, user in users.items()}

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    client = app.test_client()
    yield client, ids

    with app.app_context():
        db.session.remove()


def login(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


def password_matches(user_id, password):
    with app.app_context():
        return db.session.get(User, user_id).check_password(password)


def must_change(user_id):
    with app.app_context():
        return db.session.get(User, user_id).must_change_password


def pending_resets():
    with app.app_context():
        return db.session.execute(db.select(PasswordReset)).scalars().all()


def reset_as_admin(client, ids, temporary=TEMPORARY):
    login(client, ids["admin"])
    return client.post(
        f"/admin/users/{ids['member']}/reset-password",
        data={"temporary_password": temporary},
    )


# ------------------------------------------------------------
# Admin side
# ------------------------------------------------------------

def test_admin_resets_member_password(case):
    client, ids = case

    response = reset_as_admin(client, ids)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/users/{ids['member']}/edit")
    assert password_matches(ids["member"], TEMPORARY)
    assert not password_matches(ids["member"], PASSWORD)
    assert must_change(ids["member"]) is True

    with app.app_context():
        logs = db.session.execute(
            db.select(AuditLog).where(AuditLog.action == "reset_password")
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].user_id == ids["admin"]
        assert logs[0].target_id == ids["member"]
        # The temporary password itself is never stored in the audit log
        assert TEMPORARY not in logs[0].details

    # The admin gets a success message, which never repeats the temporary password
    messages = flashes(client)
    assert any(
        category == "success" and "Temporary password set for RST-M01" in message
        for category, message in messages
    )
    assert all(TEMPORARY not in message for _, message in messages)


def test_resetting_twice_keeps_one_pending_record(case):
    client, ids = case

    reset_as_admin(client, ids)
    reset_as_admin(client, ids, temporary="SecondTemp2026")

    assert len(pending_resets()) == 1
    assert password_matches(ids["member"], "SecondTemp2026")


def test_short_temporary_password_is_rejected(case):
    client, ids = case

    reset_as_admin(client, ids, temporary="short")

    assert ("danger", "Temporary password must contain at least 8 characters.") in flashes(client)
    assert password_matches(ids["member"], PASSWORD)
    assert pending_resets() == []


def test_admin_cannot_reset_own_password_here(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/users/{ids['admin']}/reset-password", data={"temporary_password": TEMPORARY})

    assert ("danger", "Use My Profile to change your own password.") in flashes(client)
    assert password_matches(ids["admin"], PASSWORD)
    assert pending_resets() == []


def test_only_admin_can_reset_passwords(case):
    client, ids = case
    login(client, ids["treasurer"])

    response = client.post(
        f"/admin/users/{ids['member']}/reset-password", data={"temporary_password": TEMPORARY}
    )

    assert response.status_code == 403
    assert password_matches(ids["member"], PASSWORD)


def test_edit_page_shows_reset_form_and_pending_notice(case):
    client, ids = case
    login(client, ids["admin"])

    before = client.get(f"/admin/users/{ids['member']}/edit").get_data(as_text=True)
    reset_as_admin(client, ids)
    after = client.get(f"/admin/users/{ids['member']}/edit").get_data(as_text=True)
    own = client.get(f"/admin/users/{ids['admin']}/edit").get_data(as_text=True)

    assert 'name="temporary_password"' in before
    assert "A reset is pending" not in before
    assert "A reset is pending" in after
    # No reset form on the admin's own account
    assert 'name="temporary_password"' not in own


# ------------------------------------------------------------
# User side: forced password change
# ------------------------------------------------------------

def test_user_with_pending_reset_is_sent_to_new_password_page(case):
    client, ids = case
    reset_as_admin(client, ids)
    login(client, ids["member"])

    for path in ["/dashboard", "/claims/my", "/profile/"]:
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/profile/new-password")

    page = client.get("/profile/new-password")
    assert page.status_code == 200
    assert "Set a new password" in page.get_data(as_text=True)


def test_user_with_pending_reset_can_still_log_out(case):
    client, ids = case
    reset_as_admin(client, ids)
    login(client, ids["member"])

    response = client.post("/logout")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "form, message",
    [
        (
            {"temporary_password": "wrong-temp", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            "Temporary password is incorrect.",
        ),
        (
            {"temporary_password": TEMPORARY, "new_password": "short", "confirm_password": "short"},
            "New password must contain at least 8 characters.",
        ),
        (
            {"temporary_password": TEMPORARY, "new_password": NEW_PASSWORD, "confirm_password": "Different123!"},
            "The new passwords do not match.",
        ),
        (
            {"temporary_password": TEMPORARY, "new_password": TEMPORARY, "confirm_password": TEMPORARY},
            "New password must be different from the temporary password.",
        ),
    ],
)
def test_new_password_rejected(case, form, message):
    client, ids = case
    reset_as_admin(client, ids)
    login(client, ids["member"])

    client.post("/profile/new-password", data=form)

    assert ("danger", message) in flashes(client)
    assert password_matches(ids["member"], TEMPORARY)
    assert len(pending_resets()) == 1


def test_new_password_success_clears_reset(case):
    client, ids = case
    reset_as_admin(client, ids)
    login(client, ids["member"])

    response = client.post(
        "/profile/new-password",
        data={"temporary_password": TEMPORARY, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert password_matches(ids["member"], NEW_PASSWORD)
    assert pending_resets() == []
    assert must_change(ids["member"]) is False

    # Normal pages work again
    assert client.get("/dashboard").status_code == 200
    # The page is no longer needed once the password is set
    assert client.get("/profile/new-password").status_code == 302


def test_users_without_reset_are_not_redirected(case):
    client, ids = case
    login(client, ids["member"])

    assert client.get("/dashboard").status_code == 200
    assert client.get("/profile/new-password").headers["Location"].endswith("/dashboard")


def test_user_with_pending_reset_can_still_be_deleted(case):
    client, ids = case
    reset_as_admin(client, ids)

    # The reset log belongs to the admin, so the member has no activity history
    response = client.post(f"/admin/users/{ids['member']}/delete")

    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(User, ids["member"]) is None
    assert pending_resets() == []
