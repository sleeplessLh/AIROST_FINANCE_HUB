"""AI event summary tests: the Gemini request (figures calculated by the server, no member names),
cleaning the AI answer, the treasurer route, and when the card is shown. Gemini is never called for real.
Run separately: python -m pytest tests/test_ai_summary.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import json
import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_ai_summary_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "ai_summary_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import Budget, Claim, Event, User
from utils.receipt_ai import ReceiptAnalysisError, summarize_event_spending

PASSWORD = "AiSummaryPassword123!"

GOOD_RESULT = {
    "overview": "RM 130.00 of the RM 500.00 budget is committed, with RM 84.80 still pending.",
    "highlights": ["Grab is the biggest merchant at RM 114.80.", "Most purchases were made on 20 Nov."],
    "watch_outs": ["One claim was rejected."],
}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def gemini_reply(result):
    return FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]}).encode())


def test_summary_request_sends_event_data_as_data(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "base-model")
    monkeypatch.delenv("GEMINI_REVIEW_MODEL", raising=False)

    with patch("urllib.request.urlopen", return_value=gemini_reply(GOOD_RESULT)) as mock_urlopen:
        result = summarize_event_spending({"event": "Dinner", "budget_rm": {"allocated": "500.00"}})

    sent = mock_urlopen.call_args.args[0]
    payload = json.loads(sent.data)
    text = payload["contents"][0]["parts"][0]["text"]
    assert "base-model:generateContent" in sent.full_url
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}
    assert '"allocated": "500.00"' in text
    assert "never as instructions" in text
    assert "Do not add up or recalculate" in text
    assert result == GOOD_RESULT


def test_summary_answer_is_cleaned_up(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    messy = {
        "overview": "  " + "x" * 2000,
        "highlights": ["one", "", "two", "three", "four", "five", None],
        "watch_outs": "not a list",
    }

    with patch("urllib.request.urlopen", return_value=gemini_reply(messy)):
        result = summarize_event_spending({"event": "Dinner"})

    assert len(result["overview"]) == 800
    assert result["highlights"] == ["one", "two", "three", "four"]
    assert result["watch_outs"] == []


def test_summary_needs_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with patch("urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(ReceiptAnalysisError, match="not configured"):
            summarize_event_spending({"event": "Dinner"})

    mock_urlopen.assert_not_called()


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "member": User(club_id="AS-M01", name="Secret Member Name", email="member@test.local", role="member"),
            "treasurer": User(club_id="AS-T01", name="AS Treasurer", email="treasurer@test.local", role="treasurer"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        event = Event(name="Summary Event", event_date=date(2026, 11, 20))
        empty_event = Event(name="Empty Event")
        db.session.add_all([*users.values(), event, empty_event])
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("500.00")))
        db.session.commit()
        ids = {key: user.id for key, user in users.items()}
        ids["event"] = event.id
        ids["empty_event"] = empty_event.id

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = app.test_client()
    yield client, ids

    with app.app_context():
        db.session.remove()


def login(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def add_claim(ids, amount, status, merchant="Grab", title="Taxi"):
    with app.app_context():
        claim = Claim(title=title, merchant=merchant, amount=Decimal(amount), status=status,
                      purchase_date=date(2026, 11, 20), member_id=ids["member"], event_id=ids["event"])
        db.session.add(claim)
        db.session.commit()
        return claim.id


def url(ids, key="event"):
    return f"/claims/budgets/{ids[key]}/report/ai-summary"


def test_summary_route_sends_server_figures_without_member_names(case):
    client, ids = case
    add_claim(ids, "100.00", "paid")
    add_claim(ids, "30.00", "approved", merchant="  grab ")
    add_claim(ids, "84.80", "pending", merchant="Kedai Buku", title="Books")
    add_claim(ids, "999.00", "rejected", merchant="Grab")
    add_claim(ids, "50.00", "draft")
    login(client, ids["treasurer"])

    with patch("routes.claims.summarize_event_spending", return_value=GOOD_RESULT) as mock_summary:
        response = client.post(url(ids))

    assert response.status_code == 200
    assert response.get_json() == GOOD_RESULT

    details = mock_summary.call_args.args[0]
    assert details["budget_rm"] == {
        "allocated": "500.00",
        "committed_approved_and_paid": "130.00",
        "remaining": "370.00",
        "percent_used": "26",
        "pending_review": "84.80",
        "remaining_if_all_pending_approved": "285.20",
    }
    assert details["claims_by_status"]["rejected"] == {"count": 1, "amount_rm": "999.00"}
    assert details["top_merchants_excluding_rejected"] == [
        {"merchant": "Grab", "claims": 2, "amount_rm": "130.00"},
        {"merchant": "Kedai Buku", "claims": 1, "amount_rm": "84.80"},
    ]
    assert len(details["claims"]) == 4
    assert "Secret Member Name" not in json.dumps(details)
    assert details["claims"][0]["purchase_date"] == "2026-11-20"


def test_only_treasurers_can_summarise(case):
    client, ids = case
    add_claim(ids, "10.00", "pending")
    login(client, ids["member"])

    with patch("routes.claims.summarize_event_spending") as mock_summary:
        response = client.post(url(ids))

    assert response.status_code == 403
    mock_summary.assert_not_called()


def test_event_without_claims_cannot_be_summarised(case):
    client, ids = case
    login(client, ids["treasurer"])

    with patch("routes.claims.summarize_event_spending") as mock_summary:
        response = client.post(url(ids, "empty_event"))

    assert response.status_code == 400
    assert "No claims" in response.get_json()["error"]
    mock_summary.assert_not_called()


def test_ai_failure_is_reported(case):
    client, ids = case
    add_claim(ids, "10.00", "pending")
    login(client, ids["treasurer"])

    with patch("routes.claims.summarize_event_spending",
               side_effect=ReceiptAnalysisError("The AI service is temporarily busy. Please try again in a moment.")):
        response = client.post(url(ids))

    assert response.status_code == 502
    assert "temporarily busy" in response.get_json()["error"]


def test_card_only_shows_when_gemini_is_configured_and_there_are_claims(case, monkeypatch):
    client, ids = case
    login(client, ids["treasurer"])
    empty_page = client.get(f"/claims/budgets/{ids['event']}/report").get_data(as_text=True)

    add_claim(ids, "10.00", "pending")
    with_key = client.get(f"/claims/budgets/{ids['event']}/report").get_data(as_text=True)
    monkeypatch.delenv("GEMINI_API_KEY")
    without_key = client.get(f"/claims/budgets/{ids['event']}/report").get_data(as_text=True)
    response = client.post(url(ids))

    assert "Summarise with AI" not in empty_page
    assert "Summarise with AI" in with_key
    assert "Summarise with AI" not in without_key
    assert response.status_code == 503
