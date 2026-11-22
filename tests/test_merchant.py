"""Merchant field tests: required on claim forms, shown on pages, searchable, exported,
and the one-time backfill that fills existing claims with KY Enterprise.
Run separately: python -m pytest tests/test_merchant.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import csv
import io
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

_temp = tempfile.TemporaryDirectory(prefix="airost_merchant_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "merchant_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

import app as app_module
from app import app
from database import db
from models import Budget, Claim, Event, Receipt, User

PASSWORD = "MerchantPassword123!"


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "member": User(club_id="MC-M01", name="Merchant Member", email="member@test.local", role="member"),
            "treasurer": User(club_id="MC-T01", name="Merchant Treasurer", email="treasurer@test.local", role="treasurer"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        event = Event(name="Merchant Event")
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


def flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


def submit(client, ids, **overrides):
    data = {"title": "Stationery", "merchant": "Popular Bookstore", "purchase_date": "2026-11-01",
            "description": "", "amount": "18.90",
            "event_id": str(ids["event"]), "action": "submit",
            "receipt": (io.BytesIO(b"%PDF-1.4 test"), "receipt.pdf")}
    data.update(overrides)
    with patch("routes.claims.save_receipt", return_value=("receipt.pdf", "stored-merchant.pdf")):
        return client.post("/claims/submit", data=data)


def only_claim():
    with app.app_context():
        claim = db.session.execute(db.select(Claim)).scalar_one()
        db.session.expunge(claim)
        return claim


def test_merchant_is_saved_and_shown(case):
    client, ids = case
    login(client, ids["member"])

    submit(client, ids)
    claim = only_claim()
    member_page = client.get(f"/claims/{claim.id}").get_data(as_text=True)
    login(client, ids["treasurer"])
    review_page = client.get(f"/claims/review/{claim.id}").get_data(as_text=True)

    assert claim.merchant == "Popular Bookstore"
    assert "<dt>Merchant</dt>" in member_page and "Popular Bookstore" in member_page
    assert "Popular Bookstore" in review_page


@pytest.mark.parametrize("merchant", ["", "   "])
def test_merchant_is_required(case, merchant):
    client, ids = case
    login(client, ids["member"])

    response = submit(client, ids, merchant=merchant)

    assert "Please fill in all required fields." in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.execute(db.select(Claim)).scalars().all() == []


def test_merchant_length_limit(case):
    client, ids = case
    login(client, ids["member"])

    response = submit(client, ids, merchant="M" * 151)

    assert "Merchant must not exceed 150 characters." in response.get_data(as_text=True)


def test_forms_have_merchant_field(case):
    client, ids = case
    login(client, ids["member"])

    page = client.get("/claims/submit").get_data(as_text=True)

    assert 'id="merchant"' in page and 'name="merchant"' in page


def test_edit_updates_merchant(case):
    client, ids = case
    with app.app_context():
        claim = Claim(title="Taxi", merchant="Old Shop", amount=Decimal("10.00"), status="rejected",
                      member_id=ids["member"], event_id=ids["event"])
        claim.receipts.append(Receipt(original_filename="r.pdf", stored_filename="edit-merchant.pdf"))
        db.session.add(claim)
        db.session.commit()
        claim_id = claim.id
    login(client, ids["member"])

    page = client.get(f"/claims/{claim_id}/edit").get_data(as_text=True)
    client.post(f"/claims/{claim_id}/edit", data={
        "title": "Taxi", "merchant": "Grab", "purchase_date": "2026-11-01", "description": "", "amount": "10.00",
        "event_id": str(ids["event"]), "action": "submit",
    })

    assert 'value="Old Shop"' in page
    with app.app_context():
        assert db.session.get(Claim, claim_id).merchant == "Grab"


def test_search_finds_merchant_and_csv_exports_it(case):
    client, ids = case
    login(client, ids["member"])
    submit(client, ids)
    login(client, ids["treasurer"])

    found = client.get("/claims/review?q=popular").get_data(as_text=True)
    export = list(csv.reader(io.StringIO(client.get("/claims/export").get_data(as_text=True).lstrip("﻿"))))
    report = list(csv.reader(io.StringIO(
        client.get(f"/claims/budgets/{ids['event']}/report.csv").get_data(as_text=True).lstrip("﻿"))))

    assert "Stationery" in found
    assert export[0][-2] == "Merchant" and export[1][-2] == "Popular Bookstore"
    header = next(row for row in report if row and row[0] == "Claim ID")
    claim_row = next(row for row in report if row and row[0].isdigit())
    assert header[3] == "Merchant" and claim_row[3] == "Popular Bookstore"


def test_backfill_runs_once_and_fills_existing_claims(case):
    _, ids = case
    with app.app_context():
        db.session.execute(text("ALTER TABLE claims DROP COLUMN merchant"))
        for title, status in [("Draft one", "draft"), ("Paid one", "paid"), ("Pending one", "pending")]:
            db.session.execute(text(
                "INSERT INTO claims (title, amount, status, member_id, event_id) "
                "VALUES (:title, 10, :status, :member, :event)"
            ), {"title": title, "status": status, "member": ids["member"], "event": ids["event"]})
        db.session.commit()

        app_module.ensure_claim_merchant_column()
        merchants = db.session.execute(text("SELECT merchant FROM claims")).scalars().all()
        assert merchants == ["KY Enterprise"] * 3

        db.session.execute(text("UPDATE claims SET merchant = 'Changed Later' WHERE title = 'Paid one'"))
        db.session.commit()
        app_module.ensure_claim_merchant_column()
        assert db.session.execute(
            text("SELECT merchant FROM claims WHERE title = 'Paid one'")
        ).scalar_one() == "Changed Later"
