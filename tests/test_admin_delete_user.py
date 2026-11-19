"""Delete user tests (B7-2).
Run separately: python -m pytest tests/test_admin_delete_user.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import os
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

# Must run before importing app: point the app at a temporary database
_temp = tempfile.TemporaryDirectory(
    prefix="airost_delete_user_test_", ignore_cleanup_errors=True
)
_db_path = Path(_temp.name) / "delete_user_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, Claim, Event, Payment, User, UserProfile

PASSWORD = "DeletePassword123!"


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
            "admin": User(
                club_id="DEL-A01",
                name="Delete Admin",
                email="a@test.local",
                role="admin",
            ),
            "unused": User(
                club_id="DEL-U01",
                name="Unused Account",
                email="u@test.local",
                role="member",
            ),
            "member": User(
                club_id="DEL-M01",
                name="Member With Claim",
                email="m@test.local",
                role="member",
            ),
            "treasurer": User(
                club_id="DEL-T01",
                name="Treasurer Who Paid",
                email="t@test.local",
                role="treasurer",
            ),
            "active_user": User(
                club_id="DEL-L01",
                name="User With Activity",
                email="l@test.local",
                role="member",
            ),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        db.session.add_all(users.values())

        event = Event(name="Delete Test Event")
        db.session.add(event)
        db.session.flush()

        # member has a claim; treasurer confirmed a payment for it
        claim = Claim(
            title="Paid Claim",
            amount=Decimal("50.00"),
            status="paid",
            member_id=users["member"].id,
            event_id=event.id,
        )
        db.session.add(claim)
        db.session.flush()
        db.session.add(
            Payment(
                claim_id=claim.id,
                amount=Decimal("50.00"),
                payment_method="Cash",
                paid_at=datetime(2026, 11, 1),
                paid_by_id=users["treasurer"].id,
            )
        )

        # active_user has only an audit log (e.g. changed their own profile)
        db.session.add(
            AuditLog(
                user_id=users["active_user"].id,
                action="update_profile",
                target_type="User",
                target_id=users["active_user"].id,
            )
        )

        # unused account has a profile, which should be deleted together with it
        users["unused"].profile = UserProfile(phone="012-0000000")
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


def user_exists(user_id):
    with app.app_context():
        return db.session.get(User, user_id) is not None


def delete_logs():
    with app.app_context():
        return (
            db.session.execute(
                db.select(AuditLog).where(AuditLog.action == "delete_user")
            )
            .scalars()
            .all()
        )


def test_unused_account_can_be_deleted(case):
    client, ids = case
    login(client, ids["admin"])

    response = client.post(f"/admin/users/{ids['unused']}/delete")

    assert response.status_code == 302
    assert ("success", "User DEL-U01 deleted successfully.") in flashes(client)
    assert not user_exists(ids["unused"])
    with app.app_context():
        # cascade removed the profile too
        assert db.session.execute(db.select(UserProfile)).scalars().all() == []


def test_deletion_writes_audit_log(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/users/{ids['unused']}/delete")

    logs = delete_logs()
    assert len(logs) == 1
    assert logs[0].user_id == ids["admin"]
    assert logs[0].target_id == ids["unused"]
    assert logs[0].details == "Club ID: DEL-U01; Name: Unused Account; Role: member"


def test_admin_cannot_delete_self(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/users/{ids['admin']}/delete")

    assert ("danger", "You cannot delete your own account.") in flashes(client)
    assert user_exists(ids["admin"])
    assert delete_logs() == []


@pytest.mark.parametrize("role", ["member", "treasurer", "active_user"])
def test_user_with_history_cannot_be_deleted(case, role):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/users/{ids[role]}/delete")

    messages = [
        message for category, message in flashes(client) if category == "warning"
    ]
    assert len(messages) == 1
    assert "Deactivate the account instead." in messages[0]
    assert user_exists(ids[role])
    assert delete_logs() == []


def test_delete_requires_post(case):
    client, ids = case
    login(client, ids["admin"])

    response = client.get(f"/admin/users/{ids['unused']}/delete")

    assert response.status_code == 405
    assert user_exists(ids["unused"])


@pytest.mark.parametrize("role", ["member", "treasurer"])
def test_non_admin_cannot_delete(case, role):
    client, ids = case
    login(client, ids[role])

    response = client.post(f"/admin/users/{ids['unused']}/delete")

    assert response.status_code == 403
    assert user_exists(ids["unused"])


def test_deleting_missing_user_returns_404(case):
    client, ids = case
    login(client, ids["admin"])

    response = client.post("/admin/users/9999/delete")

    assert response.status_code == 404


def test_users_page_hides_delete_button_for_self(case):
    client, ids = case
    login(client, ids["admin"])

    html = client.get("/admin/users").get_data(as_text=True)

    assert f"/admin/users/{ids['unused']}/delete" in html
    assert f"/admin/users/{ids['admin']}/delete" not in html
