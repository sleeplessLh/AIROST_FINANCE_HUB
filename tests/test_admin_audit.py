"""Audit log tests (B3): payment confirmation and admin user/event management.
Run separately: python -m pytest tests/test_admin_audit.py -v
Uses a disposable SQLite database; never touches the development database.
"""
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_audit_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "audit_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, Budget, Claim, Event, User

PASSWORD = "AuditPassword123!"


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "admin": User(club_id="AUD-A01", name="Audit Admin", email="a@test.local", role="admin"),
            "treasurer": User(club_id="AUD-T01", name="Audit Treasurer", email="t@test.local", role="treasurer"),
            "member": User(club_id="AUD-M01", name="Audit Member", email="m@test.local", role="member"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        db.session.add_all(users.values())

        event = Event(name="Audit Event", description="Original description")
        db.session.add(event)
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("1000.00")))
        db.session.commit()

        ids = {role: user.id for role, user in users.items()}
        ids["event"] = event.id

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


def logs_for(action):
    with app.app_context():
        return db.session.execute(
            db.select(AuditLog).where(AuditLog.action == action)
        ).scalars().all()


def create_claim(ids, status):
    with app.app_context():
        claim = Claim(
            title="Audit Claim", amount=Decimal("200.00"), status=status,
            member_id=ids["member"], event_id=ids["event"],
        )
        db.session.add(claim)
        db.session.commit()
        return claim.id


NEW_USER_FORM = {
    "club_id": "AUD-M99",
    "name": "New Member",
    "email": "new@test.local",
    "password": "NewUserPass123!",
    "role": "member",
}


def test_mark_paid_writes_audit_log(case):
    client, ids = case
    claim_id = create_claim(ids, "approved")
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/pay", data={
        "payment_method": "Bank Transfer",
        "reference_number": "REF-001",
    })

    logs = logs_for("mark_claim_paid")
    assert len(logs) == 1
    assert logs[0].user_id == ids["treasurer"]
    assert logs[0].target_type == "Claim"
    assert logs[0].target_id == claim_id
    assert logs[0].details == "Amount: RM 200.00; Method: Bank Transfer; Reference: REF-001"


def test_mark_paid_without_reference_logs_na(case):
    client, ids = case
    claim_id = create_claim(ids, "approved")
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/pay", data={"payment_method": "Cash"})

    assert logs_for("mark_claim_paid")[0].details.endswith("Reference: N/A")


def test_failed_payment_writes_no_audit_log(case):
    client, ids = case
    claim_id = create_claim(ids, "pending")
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/pay", data={"payment_method": "Cash"})

    assert logs_for("mark_claim_paid") == []


def test_create_user_writes_audit_log_without_password(case):
    client, ids = case
    login(client, ids["admin"])

    client.post("/admin/users/create", data=NEW_USER_FORM)

    logs = logs_for("create_user")
    assert len(logs) == 1
    assert logs[0].user_id == ids["admin"]
    assert logs[0].target_id is not None
    assert logs[0].details == "Club ID: AUD-M99; Role: member"
    assert "NewUserPass123!" not in logs[0].details


def test_duplicate_club_id_writes_no_audit_log(case):
    client, ids = case
    login(client, ids["admin"])

    client.post("/admin/users/create", data={**NEW_USER_FORM, "club_id": "AUD-M01"})

    assert logs_for("create_user") == []


def test_role_change_is_recorded_from_and_to(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/users/{ids['member']}/edit", data={
        "name": "Audit Member",
        "email": "m@test.local",
        "role": "treasurer",
        "active": "on",
    })

    logs = logs_for("update_user")
    assert len(logs) == 1
    assert logs[0].target_id == ids["member"]
    assert logs[0].details == "role: member -> treasurer"


def test_deactivation_is_recorded(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/users/{ids['member']}/edit", data={
        "name": "Audit Member",
        "email": "m@test.local",
        "role": "member",
    })

    assert logs_for("update_user")[0].details == "active: True -> False"


def test_edit_user_without_changes_writes_no_audit_log(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/users/{ids['member']}/edit", data={
        "name": "Audit Member",
        "email": "m@test.local",
        "role": "member",
        "active": "on",
    })

    assert logs_for("update_user") == []


def test_create_event_writes_audit_log(case):
    client, ids = case
    login(client, ids["admin"])

    client.post("/admin/events/create", data={"name": "New Event", "description": "", "event_date": ""})

    logs = logs_for("create_event")
    assert len(logs) == 1
    assert logs[0].target_type == "Event"
    assert logs[0].target_id is not None
    assert logs[0].details == "Name: New Event"


def test_edit_event_records_changed_fields(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/events/{ids['event']}/edit", data={
        "name": "Renamed Event",
        "description": "Original description",
        "event_date": "2026-12-01",
    })

    logs = logs_for("update_event")
    assert len(logs) == 1
    assert logs[0].details == "Updated fields: name, event_date"


def test_edit_event_without_changes_writes_no_audit_log(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(f"/admin/events/{ids['event']}/edit", data={
        "name": "Audit Event",
        "description": "Original description",
        "event_date": "",
    })

    assert logs_for("update_event") == []


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"password": "short"}, "Password must contain at least 8 characters."),
        ({"role": "superuser"}, "Invalid user role."),
        ({"club_id": "AUD-M01"}, "Club ID or email already exists."),
    ],
)
def test_create_user_error_keeps_typed_values(case, overrides, message):
    client, ids = case
    login(client, ids["admin"])

    response = client.post("/admin/users/create", data={**NEW_USER_FORM, **overrides})
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert message in html
    assert 'value="New Member"' in html
    assert 'value="new@test.local"' in html
    assert "NewUserPass123!" not in html


def test_create_user_error_keeps_selected_role(case):
    client, ids = case
    login(client, ids["admin"])

    response = client.post(
        "/admin/users/create",
        data={**NEW_USER_FORM, "role": "treasurer", "password": "short"},
    )

    assert '<option value="treasurer" selected>' in response.get_data(as_text=True)
