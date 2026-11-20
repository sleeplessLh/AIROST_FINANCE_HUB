"""Unique email tests (C4).
Run separately: python -m pytest tests/test_email_unique.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Must run before importing app: point the app at a temporary database
_temp = tempfile.TemporaryDirectory(
    prefix="airost_email_test_", ignore_cleanup_errors=True
)
_db_path = Path(_temp.name) / "email_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import app, ensure_email_unique_index
from database import db
from models import User

PASSWORD = "EmailPassword123!"


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
                club_id="EML-A01", name="Admin", email="admin@test.local", role="admin"
            ),
            "member": User(
                club_id="EML-M01",
                name="Member",
                email="member@test.local",
                role="member",
            ),
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


def user_count():
    with app.app_context():
        return len(db.session.execute(db.select(User)).scalars().all())


def email_index_exists():
    with app.app_context():
        indexes = inspect(db.engine).get_indexes("user")
        return any(
            index["name"] == "ix_user_email" and index["unique"] for index in indexes
        )


def test_database_rejects_duplicate_email(case):
    with app.app_context():
        db.session.add(
            User(
                club_id="EML-X01",
                name="Copy",
                email="member@test.local",
                role="member",
                password_hash="x",
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_create_user_with_existing_email_is_rejected(case):
    client, ids = case
    login(client, ids["admin"])

    response = client.post(
        "/admin/users/create",
        data={
            "club_id": "EML-M02",
            "name": "New",
            "email": "MEMBER@test.local",
            "password": "NewUserPass123!",
            "role": "member",
        },
    )

    # Emails are lower-cased before saving, so upper-case input is still a duplicate
    # Emails are lower-cased before saving, so upper-case input is still a duplicate
    # The form is re-rendered in the same response, so the message is in the HTML
    assert response.status_code == 400
    assert "Club ID or email already exists." in response.get_data(as_text=True)    
    assert user_count() == 2


def test_edit_user_to_existing_email_is_rejected(case):
    client, ids = case
    login(client, ids["admin"])

    client.post(
        f"/admin/users/{ids['member']}/edit",
        data={
            "name": "Member",
            "email": "admin@test.local",
            "role": "member",
            "active": "on",
        },
    )

    assert ("danger", "Email already exists.") in flashes(client)
    with app.app_context():
        assert db.session.get(User, ids["member"]).email == "member@test.local"


def test_index_is_created_on_existing_database(case):
    # Simulate an older database whose user table was created before C4
    with app.app_context():
        db.session.execute(text("DROP INDEX IF EXISTS ix_user_email"))
        db.session.commit()
    assert not email_index_exists()

    with app.app_context():
        ensure_email_unique_index()
        # Running it twice must be safe
        ensure_email_unique_index()

    assert email_index_exists()


def test_index_is_skipped_when_duplicates_exist(case):
    with app.app_context():
        db.session.execute(text("DROP INDEX IF EXISTS ix_user_email"))
        db.session.add(
            User(
                club_id="EML-X02",
                name="Copy",
                email="member@test.local",
                role="member",
                password_hash="x",
            )
        )
        db.session.commit()

        # Must not crash the app; it only logs a warning
        ensure_email_unique_index()

    assert not email_index_exists()
