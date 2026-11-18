"""Profile page tests (B2): contact/bank details, change password, treasurer Pay To card.
Run separately: python -m pytest tests/test_profile.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

# Must run before importing app: point the app at a temporary database
_temp = tempfile.TemporaryDirectory(prefix="airost_profile_test_")
_db_path = Path(_temp.name) / "profile_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, Budget, Claim, Event, User, UserProfile

PASSWORD = "ProfilePassword123!"

VALID_DETAILS = {
    "phone": "012-3456789",
    "bank_name": "Maybank",
    "bank_account_name": "Profile Member",
    "bank_account_number": "1234 5678 9012",
}


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
            "member": User(
                club_id="PRO-M01",
                name="Profile Member",
                email="m@test.local",
                role="member",
            ),
            "treasurer": User(
                club_id="PRO-T01",
                name="Profile Treasurer",
                email="t@test.local",
                role="treasurer",
            ),
            "admin": User(
                club_id="PRO-A01",
                name="Profile Admin",
                email="a@test.local",
                role="admin",
            ),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        db.session.add_all(users.values())

        event = Event(name="Profile Test Event")
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


def flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


def audit_actions(action):
    with app.app_context():
        return (
            db.session.execute(db.select(AuditLog).where(AuditLog.action == action))
            .scalars()
            .all()
        )


def create_claim(ids, status):
    with app.app_context():
        claim = Claim(
            title="Profile Claim",
            amount=Decimal("84.80"),
            status=status,
            member_id=ids["member"],
            event_id=ids["event"],
        )
        db.session.add(claim)
        db.session.commit()
        return claim.id


# ============================================================
# Access
# ============================================================
def test_guest_is_redirected_to_login(case):
    client, ids = case
    response = client.get("/profile/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("role", ["member", "treasurer", "admin"])
def test_every_role_can_open_profile(case, role):
    client, ids = case
    login(client, ids[role])
    response = client.get("/profile/")
    assert response.status_code == 200
    assert "My Profile" in response.get_data(as_text=True)


# ============================================================
# Contact & Bank Details
# ============================================================
def test_update_profile_saves_details_and_audit_log(case):
    client, ids = case
    login(client, ids["member"])

    response = client.post("/profile/", data=VALID_DETAILS)

    assert response.status_code == 302
    assert ("success", "Profile updated successfully.") in flashes(client)

    with app.app_context():
        user = db.session.get(User, ids["member"])
        # Phone lives in the new user_profiles table, created on first save
        assert user.profile is not None
        assert user.profile.phone == "012-3456789"
        assert user.bank_name == "Maybank"
        assert user.bank_account_number == "1234 5678 9012"

    logs = audit_actions("update_profile")
    assert len(logs) == 1
    assert "bank_account_number" in logs[0].details
    # Only field names are logged, never the account number itself
    assert "1234 5678 9012" not in logs[0].details


def test_partial_bank_details_are_rejected_and_input_kept(case):
    client, ids = case
    login(client, ids["member"])

    response = client.post(
        "/profile/", data={**VALID_DETAILS, "bank_account_number": ""}
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Please complete all three bank fields" in html
    # The form is re-rendered with what the user typed
    assert 'value="Maybank"' in html

    with app.app_context():
        user = db.session.get(User, ids["member"])
        assert user.bank_name == ""
        assert user.profile is None
    assert audit_actions("update_profile") == []


@pytest.mark.parametrize("phone", ["abc1234567", "12", "012345678901234567890"])
def test_invalid_phone_is_rejected(case, phone):
    client, ids = case
    login(client, ids["member"])

    response = client.post("/profile/", data={**VALID_DETAILS, "phone": phone})

    assert response.status_code == 400
    with app.app_context():
        assert db.session.get(User, ids["member"]).profile is None


def test_saving_without_changes_does_not_write_audit_log(case):
    client, ids = case
    login(client, ids["member"])

    client.post("/profile/", data=VALID_DETAILS)
    client.post("/profile/", data=VALID_DETAILS)

    assert ("info", "No changes to save.") in flashes(client)
    assert len(audit_actions("update_profile")) == 1


# ============================================================
# Change Password
# ============================================================
def test_change_password_success(case):
    client, ids = case
    login(client, ids["member"])

    response = client.post(
        "/profile/password",
        data={
            "current_password": PASSWORD,
            "new_password": "BrandNewPass456!",
            "confirm_password": "BrandNewPass456!",
        },
    )

    assert response.status_code == 302
    assert ("success", "Password changed successfully.") in flashes(client)
    with app.app_context():
        user = db.session.get(User, ids["member"])
        assert user.check_password("BrandNewPass456!")
        assert not user.check_password(PASSWORD)
    assert len(audit_actions("change_password")) == 1


@pytest.mark.parametrize(
    "form, message",
    [
        (
            {
                "current_password": "WrongPassword!",
                "new_password": "BrandNewPass456!",
                "confirm_password": "BrandNewPass456!",
            },
            "Current password is incorrect.",
        ),
        (
            {
                "current_password": PASSWORD,
                "new_password": "short",
                "confirm_password": "short",
            },
            "New password must contain at least 8 characters.",
        ),
        (
            {
                "current_password": PASSWORD,
                "new_password": "BrandNewPass456!",
                "confirm_password": "Different456!",
            },
            "The new passwords do not match.",
        ),
        (
            {
                "current_password": PASSWORD,
                "new_password": PASSWORD,
                "confirm_password": PASSWORD,
            },
            "New password must be different from the current password.",
        ),
    ],
)
def test_change_password_rejected(case, form, message):
    client, ids = case
    login(client, ids["member"])

    client.post("/profile/password", data=form)

    assert ("danger", message) in flashes(client)
    with app.app_context():
        # The original password must still work
        assert db.session.get(User, ids["member"]).check_password(PASSWORD)
    assert audit_actions("change_password") == []


# ============================================================
# Treasurer "Pay To" card (B2-5)
# ============================================================
def test_treasurer_sees_bank_details_on_approved_claim(case):
    client, ids = case
    login(client, ids["member"])
    client.post("/profile/", data=VALID_DETAILS)
    claim_id = create_claim(ids, "approved")

    login(client, ids["treasurer"])
    html = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)

    assert "Pay To" in html
    assert "Maybank" in html
    assert "1234 5678 9012" in html
    assert "012-3456789" in html


def test_treasurer_warned_when_bank_details_missing(case):
    client, ids = case
    claim_id = create_claim(ids, "approved")

    login(client, ids["treasurer"])
    html = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)

    assert "has not provided complete bank details" in html
    assert "Not provided" in html


def test_bank_details_hidden_while_claim_pending(case):
    client, ids = case
    login(client, ids["member"])
    client.post("/profile/", data=VALID_DETAILS)
    claim_id = create_claim(ids, "pending")

    login(client, ids["treasurer"])
    html = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)

    # Need-to-know: account number only appears once the claim is approved
    assert "1234 5678 9012" not in html
