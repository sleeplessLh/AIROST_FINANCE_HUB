"""Claim submission and review integration tests, using a disposable SQLite DB.
Run in a separate pytest process: python -m pytest tests/test_claim_workflow.py -v
"""
import io
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from utils.dashboard import calculate_member_statistics
import pytest

_test_dir = tempfile.TemporaryDirectory(prefix="airost_claim_tests_", ignore_cleanup_errors=True)
_db_path = Path(_test_dir.name) / "claims_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import Budget, Claim, Event, Receipt, User, AuditLog


@pytest.fixture
def case(monkeypatch):
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
    data = {
        "title": "Transport Expense",
        "description": "Taxi receipt",
        "amount": "45.50",
        "event_id": str(ids["event"]),
        "receipt": (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf"),
    }
    data.update(overrides)
    with patch("routes.claims.save_receipt", return_value=("receipt.pdf", "stored-receipt.pdf")):
        return client.post("/claims/submit", data=data, follow_redirects=False)


def flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


def claim_count():
    with app.app_context():
        return len(db.session.execute(db.select(Claim)).scalars().all())


def assert_status(claim_id, expected):
    with app.app_context():
        assert db.session.get(Claim, claim_id).status == expected


def test_member_can_open_submit_page(case):
    client, _ = case
    response = client.get("/claims/submit")
    assert response.status_code == 200
    assert b"Submit Claim" in response.data


def test_valid_claim_submission_persists_claim_and_receipt(case):
    client, ids = case
    response = submit(client, ids)
    assert response.status_code == 302
    assert ("success", "Claim submitted for review.") in flashes(client)
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
    data = {"title": "Valid", "amount": "10.00", "event_id": str(ids["event"]),
            "receipt": (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf")}
    with patch("routes.claims.save_receipt", side_effect=ValueError("Invalid receipt file.")):
        response = client.post("/claims/submit", data=data)
    assert response.status_code == 200
    assert "Invalid receipt file." in response.get_data(as_text=True)
    assert claim_count() == 0


def test_reject_pending_claim(case):
    client, ids = case
    claim_id = create_claim(ids)
    login(client, ids["treasurer"])
    response = client.post(
        f"/claims/review/{claim_id}/reject",
        data={"rejection_reason": "Receipt is unclear."},
    )
    assert response.status_code == 302
    assert ("success", "Claim rejected successfully!") in flashes(client)
    assert_status(claim_id, "rejected")


@pytest.mark.parametrize("reason", ["", "   "])
def test_reject_requires_reason(case, reason):
    client, ids = case
    claim_id = create_claim(ids)
    login(client, ids["treasurer"])
    response = client.post(
        f"/claims/review/{claim_id}/reject",
        data={"rejection_reason": reason},
    )
    assert response.status_code == 302
    assert ("danger", "Please provide a reason for rejecting this claim.") in flashes(
        client
    )
    assert_status(claim_id, "pending")
    with app.app_context():
        logs = (
            db.session.execute(
                db.select(AuditLog).where(AuditLog.action == "reject_claim")
            )
            .scalars()
            .all()
        )
        assert logs == []


def test_reject_saves_reason_in_audit_log(case):
    client, ids = case
    claim_id = create_claim(ids)
    login(client, ids["treasurer"])
    client.post(
        f"/claims/review/{claim_id}/reject",
        data={"rejection_reason": "  Missing receipt date.  "},
    )
    with app.app_context():
        log = db.session.execute(
            db.select(AuditLog)
            .where(AuditLog.action == "reject_claim")
            .where(AuditLog.target_id == claim_id)
        ).scalar_one()
        assert log.details == "Missing receipt date."
        assert log.user_id == ids["treasurer"]
        assert log.target_type == "Claim"


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


import atexit

def _cleanup():
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    _test_dir.cleanup()

atexit.register(_cleanup)


def test_member_statistics_counts_only_own_claims(case):
    client, ids = case
    create_claim(ids, status="pending")
    create_claim(ids, status="pending")
    create_claim(ids, status="approved")
    create_claim(ids, status="paid", amount="84.80")
    create_claim(ids, status="paid", amount="15.20")
    create_claim(ids, status="rejected")
    create_claim(ids, status="paid", amount="999.00", member="other")

    with app.app_context():
        stats = calculate_member_statistics(ids["member"])

    assert stats["total"] == 6
    assert stats["draft"] == 0
    assert stats["pending"] == 2
    assert stats["approved"] == 1
    assert stats["rejected"] == 1
    assert stats["paid"] == 2
    assert stats["paid_amount"] == Decimal("100.00")


def test_member_statistics_with_no_claims(case):
    client, ids = case
    with app.app_context():
        stats = calculate_member_statistics(ids["member"])

    assert stats["total"] == 0
    assert stats["pending"] == 0
    assert stats["paid"] == 0
    assert stats["paid_amount"] == Decimal("0.00")


def test_member_dashboard_shows_real_counts(case):
    client, ids = case
    create_claim(ids, status="paid", amount="84.80")
    login(client, ids["member"])

    response = client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "RM 84.80 received" in html


def test_submit_page_has_ai_button_without_inline_script(case):
    client, ids = case
    login(client, ids["member"])

    html = client.get("/claims/submit").get_data(as_text=True)

    assert 'id="analyse-receipt-button"' in html
    assert 'data-analyse-url="/claims/analyse-receipt"' in html
    assert "await fetch(" not in html


def test_edit_page_has_ai_button(case):
    client, ids = case
    claim_id = create_claim(ids, status="draft")
    login(client, ids["member"])

    html = client.get(f"/claims/{claim_id}/edit").get_data(as_text=True)

    assert 'id="claim-form"' in html
    assert 'id="analyse-receipt-button"' in html
    assert 'data-analyse-url="/claims/analyse-receipt"' in html
    assert 'id="receipt-analysis-message"' in html


def test_identical_resubmission_creates_only_one_claim(case):
    client, ids = case
    login(client, ids["member"])

    first = submit(client, ids)
    second = submit(client, ids)

    assert claim_count() == 1
    assert ("info", "This claim was already submitted.") in flashes(client)
    assert second.headers["Location"] == first.headers["Location"]


def test_different_claims_are_not_treated_as_duplicates(case):
    client, ids = case
    login(client, ids["member"])

    stored_names = [("a.pdf", "stored-a.pdf"), ("b.pdf", "stored-b.pdf")]
    with patch("routes.claims.save_receipt", side_effect=stored_names):
        for amount in ("45.50", "99.00"):
            client.post(
                "/claims/submit",
                data={
                    "title": "Transport Expense",
                    "amount": amount,
                    "event_id": str(ids["event"]),
                    "receipt": (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf"),
                },
            )

    assert claim_count() == 2


def test_duplicate_guard_does_not_save_receipt_twice(case):
    client, ids = case
    login(client, ids["member"])

    with patch(
        "routes.claims.save_receipt", return_value=("receipt.pdf", "stored-receipt.pdf")
    ) as mock_save:
        data = {
            "title": "Taxi",
            "amount": "12.00",
            "event_id": str(ids["event"]),
            "receipt": (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf"),
        }
        client.post("/claims/submit", data=data)
        data["receipt"] = (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf")
        client.post("/claims/submit", data=data)

    assert mock_save.call_count == 1

def test_review_list_hides_member_drafts(case):
    client, ids = case
    create_claim(ids, status="draft")
    login(client, ids["treasurer"])

    html = client.get("/claims/review").get_data(as_text=True)

    assert "Existing Claim" not in html


def test_review_list_still_shows_submitted_claims(case):
    client, ids = case
    create_claim(ids, status="pending")
    login(client, ids["treasurer"])

    html = client.get("/claims/review").get_data(as_text=True)

    assert "Existing Claim" in html


def test_review_list_has_search_and_status_filter(case):
    client, ids = case
    create_claim(ids, status="pending")
    login(client, ids["treasurer"])

    html = client.get("/claims/review").get_data(as_text=True)

    assert 'id="claim-search"' in html
    assert 'id="claim-status-filter"' in html
    assert 'id="claimRows"' in html
    assert "data-filter-empty" in html
    assert '<option value="draft">' not in html
