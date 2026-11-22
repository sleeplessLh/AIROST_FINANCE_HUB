"""First sign-in profile completion tests: accounts created by an admin must add email,
phone and (for members) bank details before using the site.
Run separately: python -m pytest tests/test_profile_setup.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import os
import tempfile
from pathlib import Path

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_profile_setup_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "profile_setup_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, PasswordReset, ProfileSetup, User

PASSWORD = "SetupPassword123!"

MEMBER_DETAILS = {
    "email": "New.Member@Example.com",
    "phone": "012-3456789",
    "bank_name": "Maybank",
    "bank_account_name": "New Member",
    "bank_account_number": "1234 5678 9012",
}


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "admin": User(club_id="PS-A01", name="Setup Admin", email="admin@test.local", role="admin"),
            "existing": User(club_id="PS-M00", name="Existing Member", email="existing@test.local", role="member"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        db.session.add_all(users.values())
        db.session.commit()
        ids = {key: user.id for key, user in users.items()}

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


def create_account(client, ids, role="member", club_id="PS-M01", email="new.member@test.local"):
    login(client, ids["admin"])
    client.post("/admin/users/create", data={
        "club_id": club_id, "name": "New Person", "email": email, "password": PASSWORD, "role": role,
    })
    with app.app_context():
        return db.session.execute(db.select(User.id).where(User.club_id == club_id)).scalar_one()


def pending_setups():
    with app.app_context():
        return db.session.execute(db.select(ProfileSetup)).scalars().all()


def details(**overrides):
    data = dict(MEMBER_DETAILS)
    data.update(overrides)
    return data


def test_admin_created_account_is_marked_for_setup(case):
    client, ids = case

    new_id = create_account(client, ids)

    setups = pending_setups()
    assert [s.user_id for s in setups] == [new_id]


def test_new_account_is_sent_to_complete_profile(case):
    client, ids = case
    new_id = create_account(client, ids)
    login(client, new_id)

    for path in ["/dashboard", "/claims/my", "/claims/submit", "/profile/"]:
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/profile/complete")

    page = client.get("/profile/complete").get_data(as_text=True)
    assert "Complete your profile" in page
    assert 'value="new.member@test.local"' in page
    assert 'name="bank_account_number"' in page


def test_new_account_can_still_log_out(case):
    client, ids = case
    new_id = create_account(client, ids)
    login(client, new_id)

    response = client.post("/logout")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_existing_accounts_are_not_affected(case):
    client, ids = case
    login(client, ids["existing"])

    assert client.get("/dashboard").status_code == 200
    assert client.get("/profile/complete").headers["Location"].endswith("/dashboard")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"bank_account_number": ""}, "Please fill in all three bank fields so treasurers can pay your claims."),
        ({"bank_name": "", "bank_account_name": "", "bank_account_number": ""},
         "Please fill in all three bank fields so treasurers can pay your claims."),
        ({"phone": ""}, "Phone number is required."),
        ({"phone": "abc"}, "Phone number may only contain digits, spaces, +, - and ( ), 7 to 20 characters."),
        ({"email": "not-an-email"}, "Please enter a valid email address."),
        ({"email": "existing@test.local"}, "This email is already used by another account."),
    ],
)
def test_member_setup_rejects_missing_or_invalid_details(case, overrides, message):
    client, ids = case
    new_id = create_account(client, ids)
    login(client, new_id)

    response = client.post("/profile/complete", data=details(**overrides))

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert len(pending_setups()) == 1


def test_member_completes_profile(case):
    client, ids = case
    new_id = create_account(client, ids)
    login(client, new_id)

    response = client.post("/profile/complete", data=details())

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert ("success", "Thanks! Your profile is complete.") in flashes(client)
    assert pending_setups() == []

    with app.app_context():
        user = db.session.get(User, new_id)
        assert user.email == "new.member@example.com"
        assert user.profile.phone == "012-3456789"
        assert user.bank_name == "Maybank"
        assert user.bank_account_name == "New Member"
        assert user.bank_account_number == "1234 5678 9012"
        log = db.session.execute(
            db.select(AuditLog).where(AuditLog.action == "complete_profile")
        ).scalar_one()
        assert log.user_id == new_id
        assert "1234" not in log.details

    assert client.get("/dashboard").status_code == 200


def test_treasurer_can_skip_bank_details(case):
    client, ids = case
    new_id = create_account(client, ids, role="treasurer", club_id="PS-T01", email="t.new@test.local")
    login(client, new_id)

    page = client.get("/profile/complete").get_data(as_text=True)
    response = client.post("/profile/complete", data=details(
        email="t.new@test.local", bank_name="", bank_account_name="", bank_account_number=""))

    assert "Optional." in page
    assert response.status_code == 302
    assert pending_setups() == []


def test_treasurer_partial_bank_details_rejected(case):
    client, ids = case
    new_id = create_account(client, ids, role="treasurer", club_id="PS-T01", email="t.new@test.local")
    login(client, new_id)

    response = client.post("/profile/complete", data=details(email="t.new@test.local", bank_account_number=""))

    assert response.status_code == 400
    assert "Please complete all three bank fields, or leave all of them empty." in response.get_data(as_text=True)


def test_password_reset_comes_before_profile_setup(case):
    client, ids = case
    new_id = create_account(client, ids)
    with app.app_context():
        db.session.add(PasswordReset(user_id=new_id))
        db.session.commit()
    login(client, new_id)

    assert client.get("/dashboard").headers["Location"].endswith("/profile/new-password")
    assert client.get("/profile/complete").headers["Location"].endswith("/profile/new-password")
