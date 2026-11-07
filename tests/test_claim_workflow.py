"""Claim submission and review integration tests, using a disposable SQLite DB.
Run in a separate pytest process: python -m pytest tests/test_claim_workflow.py -v
"""
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

# Must precede importing app; this file is designed for an isolated pytest invocation.
_test_dir = tempfile.TemporaryDirectory(prefix="airost_claim_tests_")
_db_path = Path(_test_dir.name) / "claims_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import Budget, Claim, Event, Receipt, User


@pytest.fixture
def case(monkeypatch):
    # Prevent destructive schema operations against an unexpected database.
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe database target: {actual}"
        db.session.remove()
        db.drop_all()
        db.create_all()
        member = User(club_id="CW-M001", name="Claim Member", email="cw-member@test.local", role="member", active=True)
        other = User(club_id="CW-M002", name="Other Member", email="cw-other@test.local", role="member", active=True)
        treasurer = User(club_id="CW-T001", name="Claim Treasurer", email="cw-treasurer@test.local", role="treasurer", active=True)
        for user in (member, other, treasurer):
            user.set_password("TestPassword123!")
        event = Event(name="Claim Workflow Test Event")
        db.session.add_all([member, other, treasurer, event])
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("1000.00")))
        db.session.commit()
        ids = {"member": member.id, "other": other.id, "treasurer": treasurer.id, "event": event.id}

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    client = app.test_client()
    login(client, ids["member"])
    yield client, ids
    with app.app_context():
        db.session.remove()


def login(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def create_claim(ids, *, amount="100.00", status="pending", member="member"):
    with app.app_context():
        claim = Claim(title="Existing Claim", description="For review", amount=Decimal(amount),
                      status=status, member_id=ids[member], event_id=ids["event"])
        db.session.add(claim)
        db.session.commit()
        return claim.id


def submit(client, ids, **overrides):
    # Stub file storage: these tests exercise the route, not file_upload.py.
    data = {"title": "Transport Expense", "description": "Taxi receipt",
            "amount": "45.50", "event_id": str(ids["event"])}
    data.update(overrides)
    with patch("routes.claims.save_receipt", return_value=("receipt.pdf", "stored-receipt.pdf")):
        return client.post("/claims/submit", data=data, follow_redirects=False)


# 从最近一次 Response 中读取已经渲染的 Flash Message
def flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


def claim_count():
    with app.app_context():
        return len(db.session.execute(db.select(Claim)).scalars().all())


def assert_status(claim_id, expected):
    with app.app_context():
        assert db.session.get(Claim, claim_id).status == expected


# --- Submission ---
def test_member_can_open_submit_page(case):
    client, _ = case
    response = client.get("/claims/submit")
    assert response.status_code == 200
    assert b"Submit Claim" in response.data


def test_valid_claim_submission_persists_claim_and_receipt(case):
    client, ids = case
    response = submit(client, ids)
    assert response.status_code == 302
    assert ("success", "Claim and receipt submitted successfully!") in flashes(client)
    with app.app_context():
        claim = db.session.execute(db.select(Claim)).scalar_one()
        assert claim.title == "Transport Expense"
        assert claim.description == "Taxi receipt"
        assert claim.amount == Decimal("45.50")
        assert claim.status == "pending"
        assert claim.member_id == ids["member"]
        assert claim.event_id == ids["event"]
        assert len(claim.receipts) == 1
        assert claim.receipts[0].original_filename == "receipt.pdf"
        assert claim.receipts[0].stored_filename == "stored-receipt.pdf"
        assert f"/claims/{claim.id}" in response.headers["Location"]


@pytest.mark.parametrize("overrides, message", [
    ({"title": ""}, "Please fill in all required fields."),
    ({"amount": ""}, "Please fill in all required fields."),
    ({"event_id": ""}, "Please fill in all required fields."),
    ({"title": "X" * 151}, "Claim title must not exceed 150 characters."),
    ({"amount": "abc"}, "Invalid claim amount."),
    ({"amount": "0"}, "Amount must be positive and have at most 2 decimal places."),
    ({"amount": "-1"}, "Amount must be positive and have at most 2 decimal places."),
    ({"amount": "1.234"}, "Amount must be positive and have at most 2 decimal places."),
    ({"amount": "NaN"}, "Amount must be positive and have at most 2 decimal places."),
    ({"amount": "Infinity"}, "Amount must be positive and have at most 2 decimal places."),
    ({"amount": "100000000"}, "Claim amount exceeds the allowed limit."),
    ({"event_id": "abc"}, "Invalid event selection."),
    ({"event_id": "999999"}, "Selected event does not exist."),
])
def test_invalid_submission_rejected(case, overrides, message):
    client, ids = case
    response = submit(client, ids, **overrides)
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    assert claim_count() == 0


def test_title_150_and_max_amount_accepted(case):
    client, ids = case
    response = submit(client, ids, title="T" * 150, amount="99999999.99")
    assert response.status_code == 302
    with app.app_context():
        claim = db.session.execute(db.select(Claim)).scalar_one()
        assert len(claim.title) == 150
        assert claim.amount == Decimal("99999999.99")


def test_receipt_validation_error_rejected(case):
    client, ids = case
    data = {"title": "Valid", "amount": "10.00", "event_id": str(ids["event"])}
    with patch("routes.claims.save_receipt", side_effect=ValueError("Invalid receipt file.")):
        response = client.post("/claims/submit", data=data)
    assert response.status_code == 200
    assert "Invalid receipt file." in response.get_data(as_text=True)
    assert claim_count() == 0


def test_treasurer_cannot_submit_claim(case):
    client, ids = case
    login(client, ids["treasurer"])
    assert client.post("/claims/submit", data={"title": "Blocked"}).status_code == 403
    assert claim_count() == 0


def test_guest_cannot_submit_claim(case):
    client, _ = case
    with client.session_transaction() as session:
        session.clear()
    response = client.post("/claims/submit")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert claim_count() == 0


# --- Review ---
def test_treasurer_can_open_review_pages(case):
    client, ids = case
    claim_id = create_claim(ids)
    login(client, ids["treasurer"])
    assert client.get("/claims/review").status_code == 200
    response = client.get(f"/claims/review/{claim_id}")
    assert response.status_code == 200
    assert b"Existing Claim" in response.data


def test_approve_pending_claim(case):
    client, ids = case
    claim_id = create_claim(ids)
    login(client, ids["treasurer"])
    response = client.post(f"/claims/review/{claim_id}/approve")
    assert response.status_code == 302
    assert ("success", "Claim approved successfully.") in flashes(client)
    assert_status(claim_id, "approved")


def test_reject_pending_claim(case):
    client, ids = case
    claim_id = create_claim(ids)
    login(client, ids["treasurer"])
    response = client.post(f"/claims/review/{claim_id}/reject")
    assert response.status_code == 302
    assert ("success", "Claim rejected successfully!") in flashes(client)
    assert_status(claim_id, "rejected")


@pytest.mark.parametrize("status", ["approved", "rejected", "paid"])
def test_nonpending_claim_cannot_be_approved(case, status):
    client, ids = case
    claim_id = create_claim(ids, status=status)
    login(client, ids["treasurer"])
    response = client.post(f"/claims/review/{claim_id}/approve")
    assert response.status_code == 302
    assert ("danger", "Only pending claims can be approved.") in flashes(client)
    assert_status(claim_id, status)


@pytest.mark.parametrize("status", ["approved", "rejected", "paid"])
def test_nonpending_claim_cannot_be_rejected(case, status):
    client, ids = case
    claim_id = create_claim(ids, status=status)
    login(client, ids["treasurer"])
    response = client.post(f"/claims/review/{claim_id}/reject")
    assert response.status_code == 302
    assert ("warning", "This claim has already been reviewed.") in flashes(client)
    assert_status(claim_id, status)


def test_approval_rejected_when_budget_insufficient(case):
    client, ids = case
    claim_id = create_claim(ids, amount="1000.01")
    login(client, ids["treasurer"])
    response = client.post(f"/claims/review/{claim_id}/approve")
    assert response.status_code == 302
    assert any(category == "danger" and "Insufficient event budget." in message
               for category, message in flashes(client))
    assert_status(claim_id, "pending")


def test_approval_at_exact_budget_limit(case):
    client, ids = case
    claim_id = create_claim(ids, amount="1000.00")
    login(client, ids["treasurer"])
    response = client.post(f"/claims/review/{claim_id}/approve")
    assert response.status_code == 302
    assert_status(claim_id, "approved")


def test_approved_claim_reserves_budget(case):
    client, ids = case
    first = create_claim(ids, amount="800.00")
    second = create_claim(ids, amount="300.00")
    login(client, ids["treasurer"])
    client.post(f"/claims/review/{first}/approve")
    client.post(f"/claims/review/{second}/approve")
    assert_status(first, "approved")
    assert_status(second, "pending")


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_member_cannot_review_claim(case, action):
    client, ids = case
    claim_id = create_claim(ids)
    response = client.post(f"/claims/review/{claim_id}/{action}")
    assert response.status_code == 403
    assert_status(claim_id, "pending")


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_guest_cannot_review_claim(case, action):
    client, ids = case
    claim_id = create_claim(ids)
    with client.session_transaction() as session:
        session.clear()
    response = client.post(f"/claims/review/{claim_id}/{action}")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert_status(claim_id, "pending")


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_nonexistent_claim_returns_404(case, action):
    client, ids = case
    login(client, ids["treasurer"])
    assert client.post(f"/claims/review/999999/{action}").status_code == 404


def test_member_cannot_open_treasurer_review(case):
    client, ids = case
    claim_id = create_claim(ids)
    assert client.get("/claims/review").status_code == 403
    assert client.get(f"/claims/review/{claim_id}").status_code == 403


def test_member_cannot_view_another_members_claim(case):
    client, ids = case
    claim_id = create_claim(ids, member="other")
    assert client.get(f"/claims/{claim_id}").status_code == 404


def test_member_can_view_own_claim(case):
    client, ids = case
    claim_id = create_claim(ids)
    response = client.get(f"/claims/{claim_id}")
    assert response.status_code == 200
    assert b"Existing Claim" in response.data


def test_missing_claim_details_return_404(case):
    client, ids = case
    assert client.get("/claims/999999").status_code == 404
    login(client, ids["treasurer"])
    assert client.get("/claims/review/999999").status_code == 404


def test_member_my_claims_excludes_other_member(case):
    client, ids = case
    create_claim(ids, member="member")
    other_id = create_claim(ids, member="other")
    with app.app_context():
        db.session.get(Claim, other_id).title = "OTHER_MEMBER_SECRET_CLAIM"
        db.session.commit()
    response = client.get("/claims/my")
    assert response.status_code == 200
    assert b"OTHER_MEMBER_SECRET_CLAIM" not in response.data


# Keep the temp DB alive until process exit; Windows file handles are released first.
import atexit

def _cleanup():
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    _test_dir.cleanup()

atexit.register(_cleanup)
