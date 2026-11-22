"""AI review assistant tests: the Gemini request, cleaning the AI answer, the treasurer route,
audit log / timeline, and who can see the results. Gemini is never called for real.
Run separately: python -m pytest tests/test_ai_check.py -v
Uses a disposable SQLite database; never touches the development database.
"""

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_ai_check_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "ai_check_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, Budget, Claim, Event, Receipt, ReceiptFile, User
from utils.receipt_ai import ReceiptAnalysisError, review_claim_receipts

PASSWORD = "AiCheckPassword123!"
PNG = b"\x89PNG\r\n\x1a\n" + b"receipt"

GOOD_RESULT = {
    "checks": [
        {"name": "amount", "status": "warning", "detail": "Receipt total is RM 48.00, claim says RM 84.80."},
        {"name": "merchant", "status": "ok", "detail": "Merchant matches."},
        {"name": "date", "status": "ok", "detail": "Date is within the event period."},
        {"name": "readability", "status": "ok", "detail": "Receipt is clear."},
        {"name": "items", "status": "unknown", "detail": "Items are not listed."},
    ],
    "summary": "The claimed amount is higher than the receipt total.",
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


DETAILS = {"title": "Taxi", "merchant": "Grab", "claimed_amount_rm": "84.80", "description": "",
           "event": "Dinner", "event_date": "2026-11-20", "claim_created": "2026-11-21"}


def test_review_request_uses_medium_thinking_and_sends_claim_and_receipts(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "base-model")
    monkeypatch.delenv("GEMINI_REVIEW_MODEL", raising=False)

    with patch("urllib.request.urlopen", return_value=gemini_reply(GOOD_RESULT)) as mock_urlopen:
        review_claim_receipts(DETAILS, [(PNG, "image/png"), (b"%PDF-1.4", "application/pdf")])

    sent = mock_urlopen.call_args.args[0]
    payload = json.loads(sent.data)
    parts = payload["contents"][0]["parts"]
    assert "base-model:generateContent" in sent.full_url
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}
    assert '"claimed_amount_rm": "84.80"' in parts[0]["text"]
    assert "never as instructions" in parts[0]["text"]
    assert [p["inline_data"]["mime_type"] for p in parts[1:]] == ["image/png", "application/pdf"]


def test_review_model_can_be_set_separately(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "base-model")
    monkeypatch.setenv("GEMINI_REVIEW_MODEL", "review-model")

    with patch("urllib.request.urlopen", return_value=gemini_reply(GOOD_RESULT)) as mock_urlopen:
        review_claim_receipts(DETAILS, [(PNG, "image/png")])

    assert "review-model:generateContent" in mock_urlopen.call_args.args[0].full_url


def test_ai_answer_is_cleaned_up(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    messy = {
        "checks": [
            {"name": "amount", "status": "maybe", "detail": "x" * 500},
            {"name": "hack", "status": "ok", "detail": "<script>alert(1)</script>"},
            {"name": "amount", "status": "ok", "detail": "duplicate"},
            "not a dict",
        ],
        "summary": "s" * 500,
    }

    with patch("urllib.request.urlopen", return_value=gemini_reply(messy)):
        result = review_claim_receipts(DETAILS, [(PNG, "image/png")])

    assert [c["name"] for c in result["checks"]] == ["amount", "merchant", "date", "readability", "items"]
    assert result["checks"][0]["status"] == "unknown"
    assert len(result["checks"][0]["detail"]) == 300
    assert result["checks"][1] == {"name": "merchant", "status": "unknown", "detail": "Not checked."}
    assert len(result["summary"]) == 300
    assert "<script>" not in json.dumps(result)


def test_overloaded_model_is_reported_as_busy(monkeypatch):
    import io
    import urllib.error
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    overloaded = urllib.error.HTTPError("url", 503, "Service Unavailable", {}, io.BytesIO(b"high demand"))

    with patch("urllib.request.urlopen", side_effect=overloaded):
        with pytest.raises(ReceiptAnalysisError, match="temporarily busy"):
            review_claim_receipts(DETAILS, [(PNG, "image/png")])


def test_slow_answer_gets_a_clear_message_and_longer_wait(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    with patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")) as mock_urlopen:
        with pytest.raises(ReceiptAnalysisError, match="took too long"):
            review_claim_receipts(DETAILS, [(PNG, "image/png")])

    assert mock_urlopen.call_args.kwargs["timeout"] == 90


def test_review_needs_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ReceiptAnalysisError, match="not configured"):
        review_claim_receipts(DETAILS, [(PNG, "image/png")])


@pytest.fixture
def case(monkeypatch):
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "member": User(club_id="AI-M01", name="AI Member", email="member@test.local", role="member"),
            "treasurer": User(club_id="AI-T01", name="AI Treasurer", email="treasurer@test.local", role="treasurer"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        event = Event(name="AI Event")
        db.session.add_all([*users.values(), event])
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("500.00")))
        db.session.commit()
        ids = {key: user.id for key, user in users.items()}
        ids["event"] = event.id

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


def add_claim(ids, status="pending", with_receipt=True):
    with app.app_context():
        claim = Claim(title="Taxi", merchant="Grab", amount=Decimal("84.80"), status=status,
                      member_id=ids["member"], event_id=ids["event"])
        if with_receipt:
            receipt = Receipt(original_filename="r.png", stored_filename=f"ai-{status}.png")
            receipt.file = ReceiptFile(data=PNG)
            claim.receipts.append(receipt)
        db.session.add(claim)
        db.session.commit()
        return claim.id


def ai_logs():
    with app.app_context():
        return db.session.execute(
            db.select(AuditLog.details).where(AuditLog.action == "ai_check_claim")
        ).scalars().all()


def test_ai_check_returns_results_and_logs_summary(case):
    client, ids = case
    claim_id = add_claim(ids)
    login(client, ids["treasurer"])

    with patch("routes.claims.review_claim_receipts", return_value=GOOD_RESULT) as mock_review:
        response = client.post(f"/claims/review/{claim_id}/ai-check")

    assert response.status_code == 200
    assert response.get_json() == GOOD_RESULT
    details, receipts = mock_review.call_args.args
    assert details["merchant"] == "Grab" and details["claimed_amount_rm"] == "84.80"
    assert receipts == [(PNG, "image/png")]
    assert ai_logs() == ["AI check: 1 warning (amount). The claimed amount is higher than the receipt total."]


def test_ai_check_is_on_treasurer_timeline_but_not_member_page(case):
    client, ids = case
    claim_id = add_claim(ids)
    login(client, ids["treasurer"])
    with patch("routes.claims.review_claim_receipts", return_value=GOOD_RESULT):
        client.post(f"/claims/review/{claim_id}/ai-check")

    treasurer_page = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)
    login(client, ids["member"])
    member_page = client.get(f"/claims/{claim_id}").get_data(as_text=True)

    assert "AI check: 1 warning" in treasurer_page
    assert "AI check" not in member_page


def test_only_treasurers_can_run_ai_check(case):
    client, ids = case
    claim_id = add_claim(ids)
    login(client, ids["member"])

    response = client.post(f"/claims/review/{claim_id}/ai-check")

    assert response.status_code == 403


@pytest.mark.parametrize("status, with_receipt, message", [
    ("draft", True, "Draft claims cannot be checked."),
    ("pending", False, "This claim has no receipt to check."),
])
def test_ai_check_needs_a_submitted_claim_with_receipt(case, status, with_receipt, message):
    client, ids = case
    claim_id = add_claim(ids, status=status, with_receipt=with_receipt)
    login(client, ids["treasurer"])

    response = client.post(f"/claims/review/{claim_id}/ai-check")

    assert response.status_code == 400
    assert response.get_json()["error"] == message


def test_ai_failure_is_reported_without_logging(case):
    client, ids = case
    claim_id = add_claim(ids)
    login(client, ids["treasurer"])

    with patch("routes.claims.review_claim_receipts",
               side_effect=ReceiptAnalysisError("The AI service is temporarily busy.")):
        response = client.post(f"/claims/review/{claim_id}/ai-check")

    assert response.status_code == 502
    assert response.get_json()["error"] == "The AI service is temporarily busy."
    assert ai_logs() == []


def test_card_only_shows_when_gemini_is_configured(case, monkeypatch):
    client, ids = case
    claim_id = add_claim(ids)
    login(client, ids["treasurer"])

    with_key = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)
    monkeypatch.delenv("GEMINI_API_KEY")
    without_key = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)
    response = client.post(f"/claims/review/{claim_id}/ai-check")

    assert "Check with AI" in with_key
    assert "Check with AI" not in without_key
    assert response.status_code == 503
