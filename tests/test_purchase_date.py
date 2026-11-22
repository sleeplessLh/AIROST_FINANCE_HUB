"""Purchase date tests: required on claim forms, no future dates, shown on pages, exported,
read by AI receipt analysis, compared by the AI review assistant, and the one-time column
migration that leaves existing claims empty.
Run separately: python -m pytest tests/test_purchase_date.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import csv
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

_temp = tempfile.TemporaryDirectory(prefix="airost_purchase_date_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "purchase_date_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

import app as app_module
from app import app
from database import db
from models import Budget, Claim, Event, Receipt, ReceiptFile, User
from utils.receipt_ai import extract_receipt_data
from utils.timezone import MALAYSIA_TZ

PASSWORD = "PurchaseDatePassword123!"
PNG = b"\x89PNG\r\n\x1a\n" + b"receipt"


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "member": User(club_id="PD-M01", name="Date Member", email="member@test.local", role="member"),
            "treasurer": User(club_id="PD-T01", name="Date Treasurer", email="treasurer@test.local", role="treasurer"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        event = Event(name="Date Event")
        db.session.add_all([*users.values(), event])
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("500.00")))
        db.session.commit()
        ids = {key: user.id for key, user in users.items()}
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


def submit(client, ids, **overrides):
    data = {"title": "Stationery", "merchant": "Popular", "purchase_date": "2026-11-10", "description": "",
            "amount": "18.90", "event_id": str(ids["event"]), "action": "submit",
            "receipt": (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf")}
    data.update(overrides)
    with patch("routes.claims.save_receipt", return_value=("receipt.pdf", "stored-date.pdf")):
        return client.post("/claims/submit", data=data)


def claims():
    with app.app_context():
        return db.session.execute(db.select(Claim)).scalars().all()


def test_purchase_date_is_saved_and_shown(case):
    client, ids = case
    login(client, ids["member"])

    submit(client, ids)
    claim = claims()[0]
    member_page = client.get(f"/claims/{claim.id}").get_data(as_text=True)
    login(client, ids["treasurer"])
    review_page = client.get(f"/claims/review/{claim.id}").get_data(as_text=True)

    assert claim.purchase_date == date(2026, 11, 10)
    assert "<dt>Purchase date</dt>" in member_page and "10 Nov 2026" in member_page
    assert "10 Nov 2026" in review_page


@pytest.mark.parametrize("value, message", [
    ("", "Please fill in all required fields."),
    ("not-a-date", "Please enter a valid purchase date."),
    ("2026-02-30", "Please enter a valid purchase date."),
])
def test_purchase_date_must_be_a_real_date(case, value, message):
    client, ids = case
    login(client, ids["member"])

    response = submit(client, ids, purchase_date=value)

    assert message in response.get_data(as_text=True)
    assert claims() == []


def test_purchase_date_cannot_be_in_the_future(case):
    client, ids = case
    login(client, ids["member"])
    tomorrow = (datetime.now(MALAYSIA_TZ).date() + timedelta(days=1)).isoformat()

    response = submit(client, ids, purchase_date=tomorrow)

    assert "Purchase date cannot be in the future." in response.get_data(as_text=True)
    assert claims() == []


def test_forms_have_purchase_date_field(case):
    client, ids = case
    login(client, ids["member"])

    page = client.get("/claims/submit").get_data(as_text=True)

    assert 'type="date"' in page and 'name="purchase_date"' in page


def test_old_claim_without_date_must_get_one_when_edited(case):
    client, ids = case
    with app.app_context():
        claim = Claim(title="Old", merchant="KY Enterprise", amount=Decimal("10.00"), status="rejected",
                      member_id=ids["member"], event_id=ids["event"])
        claim.receipts.append(Receipt(original_filename="r.pdf", stored_filename="old-date.pdf"))
        db.session.add(claim)
        db.session.commit()
        claim_id = claim.id
    login(client, ids["member"])
    form = {"title": "Old", "merchant": "KY Enterprise", "description": "", "amount": "10.00",
            "event_id": str(ids["event"]), "action": "submit"}

    detail = client.get(f"/claims/{claim_id}").get_data(as_text=True)
    missing = client.post(f"/claims/{claim_id}/edit", data=form)
    fixed = client.post(f"/claims/{claim_id}/edit", data={**form, "purchase_date": "2026-10-05"})

    assert "<dt>Purchase date</dt>\n                    <dd>-</dd>" in detail
    assert "Please fill in all required fields." in missing.get_data(as_text=True)
    assert fixed.status_code == 302
    with app.app_context():
        assert db.session.get(Claim, claim_id).purchase_date == date(2026, 10, 5)


def test_purchase_date_in_csv_exports(case):
    client, ids = case
    login(client, ids["member"])
    submit(client, ids)
    login(client, ids["treasurer"])

    export = list(csv.reader(io.StringIO(client.get("/claims/export").get_data(as_text=True).lstrip("﻿"))))
    report = list(csv.reader(io.StringIO(
        client.get(f"/claims/budgets/{ids['event']}/report.csv").get_data(as_text=True).lstrip("﻿"))))

    assert export[0][-1] == "Purchase Date" and export[1][-1] == "2026-11-10"
    header = next(row for row in report if row and row[0] == "Claim ID")
    claim_row = next(row for row in report if row and row[0].isdigit())
    assert header[4] == "Purchase Date" and claim_row[4] == "2026-11-10"


def test_ai_check_sends_the_claimed_purchase_date(case, monkeypatch):
    client, ids = case
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with app.app_context():
        claim = Claim(title="Taxi", merchant="Grab", purchase_date=date(2026, 11, 3), amount=Decimal("20.00"),
                      status="pending", member_id=ids["member"], event_id=ids["event"])
        receipt = Receipt(original_filename="r.png", stored_filename="ai-date.png")
        receipt.file = ReceiptFile(data=PNG)
        claim.receipts.append(receipt)
        db.session.add(claim)
        db.session.commit()
        claim_id = claim.id
    login(client, ids["treasurer"])
    result = {"checks": [], "summary": ""}

    with patch("routes.claims.review_claim_receipts", return_value=result) as mock_review:
        client.post(f"/claims/review/{claim_id}/ai-check")

    assert mock_review.call_args.args[0]["claimed_purchase_date"] == "2026-11-03"


def gemini_reply(payload):
    body = json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}).encode()

    class Reply:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return Reply()


@pytest.mark.parametrize("ai_date, expected", [
    ("2026-11-15", "2026-11-15"),
    ("15/11/2026", ""),
    ("", ""),
])
def test_ai_receipt_analysis_returns_only_valid_dates(monkeypatch, ai_date, expected):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    reply = {"title": "Lunch", "merchant": "Cafe", "purchase_date": ai_date, "amount": 10, "description": ""}

    with patch("urllib.request.urlopen", return_value=gemini_reply(reply)):
        result = extract_receipt_data(PNG, "image/png")

    assert result["purchase_date"] == expected


def test_migration_adds_column_without_filling_old_claims(case):
    _, ids = case
    with app.app_context():
        db.session.execute(text("ALTER TABLE claims DROP COLUMN purchase_date"))
        db.session.execute(text(
            "INSERT INTO claims (title, merchant, amount, status, member_id, event_id) "
            "VALUES ('Old claim', 'KY Enterprise', 10, 'paid', :member, :event)"
        ), {"member": ids["member"], "event": ids["event"]})
        db.session.commit()

        app_module.ensure_claim_purchase_date_column()
        app_module.ensure_claim_purchase_date_column()

        assert db.session.execute(text("SELECT purchase_date FROM claims")).scalars().all() == [None]
