"""Email notification tests: submit, resubmit, approve and reject send the right emails.
Run separately: python -m pytest tests/test_notifications.py -v
Uses a disposable SQLite database and never calls the real Brevo API.
"""

import io
import os
import tempfile
import urllib.error
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_notify_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "notify_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import Budget, Claim, Event, Receipt, User
import utils.notifications as notifications

PASSWORD = "NotifyPassword123!"


class InlineThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "member": User(club_id="NT-M01", name="Notify Member", email="member@test.local", role="member"),
            "treasurer": User(club_id="NT-T01", name="Notify Treasurer", email="treasurer@test.local", role="treasurer"),
            "treasurer2": User(club_id="NT-T02", name="Second Treasurer", email="treasurer2@test.local", role="treasurer"),
            "inactive": User(club_id="NT-T03", name="Inactive Treasurer", email="inactive@test.local", role="treasurer"),
        }
        for key, user in users.items():
            user.active = key != "inactive"
            user.set_password(PASSWORD)
        event = Event(name="Notify Event")
        db.session.add_all([*users.values(), event])
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("1000.00")))
        db.session.commit()
        ids = {key: user.id for key, user in users.items()}
        ids["event"] = event.id

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setitem(app.config, "MAIL_SUPPRESS", False)
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("MAIL_FROM", "club@example.com")

    sent = []
    monkeypatch.setattr(notifications, "deliver", lambda payload, api_key, logger: sent.append((payload, api_key)))
    monkeypatch.setattr(notifications.threading, "Thread", InlineThread)

    client = app.test_client()
    yield client, ids, sent

    with app.app_context():
        db.session.remove()


def login(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def create_claim(ids, status, with_receipt=True):
    with app.app_context():
        claim = Claim(title="Taxi", description="Airport", amount=Decimal("45.50"),
                      status=status, member_id=ids["member"], event_id=ids["event"])
        if with_receipt:
            claim.receipts.append(Receipt(original_filename="r.pdf", stored_filename=f"stored-{status}.pdf"))
        db.session.add(claim)
        db.session.commit()
        return claim.id


def claim_form(ids, action):
    return {"title": "Taxi", "description": "Airport", "amount": "45.50",
            "event_id": str(ids["event"]), "action": action}


def recipients(payload):
    return sorted(r["email"] for r in payload["to"])


def test_submit_emails_active_treasurers(case):
    client, ids, sent = case
    login(client, ids["member"])
    data = claim_form(ids, "submit")
    data["receipt"] = (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf")

    with patch("routes.claims.save_receipt", return_value=("receipt.pdf", "stored-new.pdf")):
        client.post("/claims/submit", data=data)

    assert len(sent) == 1
    payload, api_key = sent[0]
    assert api_key == "test-key"
    assert payload["sender"]["email"] == "club@example.com"
    assert recipients(payload) == ["treasurer2@test.local", "treasurer@test.local"]
    assert payload["subject"] == "Claim submitted: Taxi"
    assert "/claims/review/" in payload["textContent"]
    assert "RM 45.50" in payload["textContent"]


def test_saving_a_draft_sends_nothing(case):
    client, ids, sent = case
    login(client, ids["member"])

    client.post("/claims/submit", data=claim_form(ids, "draft"))

    assert sent == []


def test_first_submit_of_a_draft_says_submitted(case):
    client, ids, sent = case
    claim_id = create_claim(ids, "draft")
    login(client, ids["member"])

    client.post(f"/claims/{claim_id}/edit", data=claim_form(ids, "submit"))

    assert [p["subject"] for p, _ in sent] == ["Claim submitted: Taxi"]


def test_resubmitting_a_rejected_claim_says_resubmitted(case):
    client, ids, sent = case
    claim_id = create_claim(ids, "rejected")
    login(client, ids["member"])

    client.post(f"/claims/{claim_id}/edit", data=claim_form(ids, "submit"))

    assert [p["subject"] for p, _ in sent] == ["Claim resubmitted: Taxi"]
    assert recipients(sent[0][0]) == ["treasurer2@test.local", "treasurer@test.local"]


def test_approve_emails_the_member(case):
    client, ids, sent = case
    claim_id = create_claim(ids, "pending")
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/approve")

    assert len(sent) == 1
    assert recipients(sent[0][0]) == ["member@test.local"]
    assert sent[0][0]["subject"] == "Claim approved: Taxi"


def test_reject_emails_the_member_with_reason(case):
    client, ids, sent = case
    claim_id = create_claim(ids, "pending")
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/reject", data={"rejection_reason": "Receipt is blurry"})

    assert len(sent) == 1
    assert recipients(sent[0][0]) == ["member@test.local"]
    assert sent[0][0]["subject"] == "Claim rejected: Taxi"
    assert "Reason: Receipt is blurry" in sent[0][0]["textContent"]


def test_mark_paid_emails_the_member_with_payment_details(case):
    client, ids, sent = case
    claim_id = create_claim(ids, "approved")
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/pay",
                data={"payment_method": "Bank Transfer", "reference_number": "REF-2026-01"})

    assert len(sent) == 1
    payload = sent[0][0]
    assert recipients(payload) == ["member@test.local"]
    assert payload["subject"] == "Claim paid: Taxi"
    assert "Method: Bank Transfer" in payload["textContent"]
    assert "Reference: REF-2026-01" in payload["textContent"]
    assert "Paid amount: RM 45.50" in payload["textContent"]


def test_nothing_is_sent_when_email_is_not_configured(case, monkeypatch):
    client, ids, sent = case
    monkeypatch.delenv("BREVO_API_KEY")
    claim_id = create_claim(ids, "pending")
    login(client, ids["treasurer"])

    response = client.post(f"/claims/review/{claim_id}/approve")

    assert response.status_code == 302
    assert sent == []
    with app.app_context():
        assert db.session.get(Claim, claim_id).status == "approved"


def test_emails_are_suppressed_in_tests_by_default(case, monkeypatch):
    client, ids, sent = case
    monkeypatch.delitem(app.config, "MAIL_SUPPRESS")
    claim_id = create_claim(ids, "pending")
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/approve")

    assert sent == []


def test_delivery_failure_is_logged_not_raised():
    payload = {"to": [{"email": "member@test.local"}], "subject": "x", "textContent": "x",
               "sender": {"email": "club@example.com", "name": "Club"}}
    logged = []

    class Logger:
        def warning(self, *args):
            logged.append(args)

    with patch.object(notifications.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")):
        real_deliver = notifications.__dict__["deliver"]
        real_deliver(payload, "test-key", Logger())

    assert len(logged) == 1
