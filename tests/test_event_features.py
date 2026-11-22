"""Event tools tests: closing events, claim history timeline, event expense report,
dashboard pending bars, daily pending-claim reminders and the site icon.
Run separately: python -m pytest tests/test_event_features.py -v
Uses a disposable SQLite database and never calls the real Brevo API.
"""

import csv
import io
import os
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_event_features_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "event_features_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, Budget, Claim, Event, EventClosure, Payment, Receipt, User, utc_now
import utils.notifications as notifications

PASSWORD = "EventPassword123!"


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
            "admin": User(club_id="EV-A01", name="Event Admin", email="admin@test.local", role="admin"),
            "member": User(club_id="EV-M01", name="Event Member", email="member@test.local", role="member"),
            "treasurer": User(club_id="EV-T01", name="Event Treasurer", email="treasurer@test.local", role="treasurer"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        event = Event(name="Annual Dinner")
        db.session.add_all([*users.values(), event])
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("100.00")))
        db.session.commit()
        ids = {key: user.id for key, user in users.items()}
        ids["event"] = event.id

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setitem(app.config, "MAIL_SUPPRESS", False)
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("MAIL_FROM", "club@example.com")
    sent = []
    monkeypatch.setattr(notifications, "deliver", lambda payload, api_key, logger: sent.append(payload))
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


def flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


def add_claim(ids, status="pending", amount="40.00", title="Taxi", created_days_ago=0, receipt=True):
    with app.app_context():
        claim = Claim(title=title, description="", amount=Decimal(amount), status=status,
                      member_id=ids["member"], event_id=ids["event"],
                      created_at=utc_now() - timedelta(days=created_days_ago))
        if receipt:
            claim.receipts.append(Receipt(original_filename="r.pdf", stored_filename=f"{title}-{status}-{amount}.pdf"))
        db.session.add(claim)
        db.session.commit()
        return claim.id


def close_event(ids):
    with app.app_context():
        db.session.add(EventClosure(event_id=ids["event"]))
        db.session.commit()


def claim_form(ids, action="submit"):
    return {"merchant": "Test Shop", "title": "Taxi", "description": "", "amount": "40.00", "event_id": str(ids["event"]), "action": action}


def test_admin_can_close_and_reopen_event(case):
    client, ids, _ = case
    login(client, ids["admin"])

    client.post(f"/admin/events/{ids['event']}/close")
    with app.app_context():
        assert db.session.get(Event, ids["event"]).is_closed is True
    page = client.get("/admin/events").get_data(as_text=True)
    assert "Closed" in page and "Reopen" in page

    client.post(f"/admin/events/{ids['event']}/reopen")
    with app.app_context():
        assert db.session.get(Event, ids["event"]).is_closed is False
        actions = db.session.execute(db.select(AuditLog.action)).scalars().all()
        assert actions == ["close_event", "reopen_event"]


def test_only_admin_can_close_events(case):
    client, ids, _ = case
    login(client, ids["treasurer"])

    response = client.post(f"/admin/events/{ids['event']}/close")

    assert response.status_code == 403


def test_closed_event_is_hidden_from_new_claims(case):
    client, ids, _ = case
    close_event(ids)
    login(client, ids["member"])

    page = client.get("/claims/submit").get_data(as_text=True)
    response = client.post("/claims/submit", data=claim_form(ids, "draft"))

    assert "Annual Dinner" not in page
    assert "This event is closed for new claims." in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.execute(db.select(Claim)).scalars().all() == []


def test_rejected_claim_of_closed_event_cannot_be_resubmitted(case):
    client, ids, _ = case
    claim_id = add_claim(ids, status="rejected")
    close_event(ids)
    login(client, ids["member"])

    page = client.get(f"/claims/{claim_id}/edit").get_data(as_text=True)
    submit = client.post(f"/claims/{claim_id}/edit", data=claim_form(ids, "submit"))
    draft = client.post(f"/claims/{claim_id}/edit", data=claim_form(ids, "draft"))

    assert "Annual Dinner (closed)" in page
    assert "This event is closed for new claims." in submit.get_data(as_text=True)
    assert draft.status_code == 302
    with app.app_context():
        assert db.session.get(Claim, claim_id).status == "draft"


def test_pending_claim_of_closed_event_can_still_be_approved(case):
    client, ids, _ = case
    claim_id = add_claim(ids, status="pending")
    close_event(ids)
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/approve")

    with app.app_context():
        assert db.session.get(Claim, claim_id).status == "approved"


def test_timeline_shows_submit_reject_resubmit_approve(case):
    client, ids, _ = case
    claim_id = add_claim(ids, status="draft")
    login(client, ids["member"])
    client.post(f"/claims/{claim_id}/edit", data=claim_form(ids, "submit"))
    login(client, ids["treasurer"])
    client.post(f"/claims/review/{claim_id}/reject", data={"rejection_reason": "Receipt is blurry"})
    login(client, ids["member"])
    client.post(f"/claims/{claim_id}/edit", data=claim_form(ids, "submit"))
    login(client, ids["treasurer"])
    client.post(f"/claims/review/{claim_id}/approve")

    login(client, ids["member"])
    page = client.get(f"/claims/{claim_id}").get_data(as_text=True)
    page = page[page.index('class="timeline"'):]

    order = ["Created", "Submitted for review", "Rejected", "Receipt is blurry",
             "Resubmitted for review", "Approved"]
    positions = [page.index(label) for label in order]
    assert positions == sorted(positions)
    assert "Event Treasurer" in page


def test_new_claim_submission_is_logged_for_the_timeline(case):
    client, ids, _ = case
    login(client, ids["member"])
    data = claim_form(ids, "submit")
    data["receipt"] = (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf")

    from unittest.mock import patch
    with patch("routes.claims.save_receipt", return_value=("receipt.pdf", "stored-timeline.pdf")):
        client.post("/claims/submit", data=data)

    with app.app_context():
        assert db.session.execute(db.select(AuditLog.action)).scalars().all() == ["submit_claim"]


def test_treasurer_detail_page_has_timeline(case):
    client, ids, _ = case
    claim_id = add_claim(ids)
    login(client, ids["treasurer"])

    page = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)

    assert "History" in page and "Created" in page


def test_event_report_page_and_csv(case):
    client, ids, _ = case
    add_claim(ids, status="paid", amount="30.00", title="Food")
    add_claim(ids, status="pending", amount="20.00", title="Taxi")
    add_claim(ids, status="draft", amount="99.00", title="Secret Draft")
    login(client, ids["treasurer"])

    page = client.get(f"/claims/budgets/{ids['event']}/report").get_data(as_text=True)
    response = client.get(f"/claims/budgets/{ids['event']}/report.csv")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("﻿"))))

    assert "Annual Dinner" in page and "Food" in page and "Taxi" in page
    assert "Secret Draft" not in page
    assert "RM 20.00" in page
    assert response.headers["Content-Disposition"].endswith(f"event_{ids['event']}_report.csv")
    assert ["Allocated (RM)", "100.00"] in rows
    assert ["Committed (RM)", "30.00"] in rows
    assert ["Pending (RM)", "20.00"] in rows
    assert [row[2] for row in rows if row and row[0].isdigit()] == ["Food", "Taxi"]


def test_event_report_csv_neutralizes_formulas(case):
    client, ids, _ = case
    add_claim(ids, status="pending", title="=HYPERLINK(1)")
    login(client, ids["treasurer"])

    text = client.get(f"/claims/budgets/{ids['event']}/report.csv").get_data(as_text=True)

    assert "'=HYPERLINK(1)" in text


def test_only_treasurer_can_see_reports(case):
    client, ids, _ = case
    login(client, ids["member"])

    assert client.get(f"/claims/budgets/{ids['event']}/report").status_code == 403
    assert client.get(f"/claims/budgets/{ids['event']}/report.csv").status_code == 403


def test_dashboard_shows_pending_part_and_warning(case):
    client, ids, _ = case
    add_claim(ids, status="approved", amount="70.00", title="Venue")
    add_claim(ids, status="pending", amount="40.00", title="Taxi")
    login(client, ids["treasurer"])

    page = client.get("/dashboard").get_data(as_text=True)

    assert "progress-bar is-pending" in page
    assert "+ RM 40.00 pending" in page
    assert "would exceed the budget if all approved" in page
    assert f"/claims/budgets/{ids['event']}/report" in page


def test_reminder_url_is_hidden_without_task_token(case, monkeypatch):
    client, _, _ = case
    monkeypatch.delenv("TASK_TOKEN", raising=False)

    assert client.post("/tasks/pending-reminders").status_code == 404


def test_reminder_rejects_wrong_token(case, monkeypatch):
    client, _, _ = case
    monkeypatch.setenv("TASK_TOKEN", "right-token")

    response = client.post("/tasks/pending-reminders", headers={"X-Task-Token": "wrong"})

    assert response.status_code == 403


def test_reminder_emails_treasurers_about_old_pending_claims(case, monkeypatch):
    client, ids, sent = case
    monkeypatch.setenv("TASK_TOKEN", "right-token")
    add_claim(ids, status="pending", title="Old Claim", created_days_ago=5)
    add_claim(ids, status="pending", title="New Claim", created_days_ago=1)

    response = client.post("/tasks/pending-reminders", headers={"X-Task-Token": "right-token"})

    assert response.get_json() == {"ok": True, "reminded_claims": 1}
    assert len(sent) == 1
    assert [r["email"] for r in sent[0]["to"]] == ["treasurer@test.local"]
    assert "Old Claim" in sent[0]["textContent"]
    assert "New Claim" not in sent[0]["textContent"]
    assert "waiting 5 days" in sent[0]["textContent"]


def test_recent_resubmission_restarts_the_wait(case, monkeypatch):
    client, ids, sent = case
    monkeypatch.setenv("TASK_TOKEN", "right-token")
    claim_id = add_claim(ids, status="pending", created_days_ago=10)
    with app.app_context():
        db.session.add(AuditLog(user_id=ids["member"], action="resubmit_claim", target_type="Claim",
                                target_id=claim_id, details="", created_at=utc_now() - timedelta(days=1)))
        db.session.commit()

    response = client.post("/tasks/pending-reminders", headers={"X-Task-Token": "right-token"})

    assert response.get_json()["reminded_claims"] == 0
    assert sent == []


def test_site_icon(case):
    client, _, _ = case

    page = client.get("/login").get_data(as_text=True)
    icon = client.get("/favicon.ico")

    assert 'rel="icon"' in page and "favicon.svg" in page
    assert icon.status_code == 301
    assert icon.headers["Location"].endswith("/static/favicon.svg")
    assert client.get("/static/favicon.svg").status_code == 200
