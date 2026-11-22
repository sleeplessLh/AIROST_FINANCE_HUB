"""Payment proof tests: optional upload when marking a claim paid, later upload or replace,
and who can view or download it.
Run separately: python -m pytest tests/test_payment_proof.py -v
Uses a disposable SQLite database and never calls the real Brevo API.
"""

import io
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

_temp = tempfile.TemporaryDirectory(prefix="airost_payment_proof_test_", ignore_cleanup_errors=True)
_db_path = Path(_temp.name) / "payment_proof_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, Budget, Claim, Event, PaymentProof, User
import utils.notifications as notifications

PASSWORD = "ProofPassword123!"
PNG = b"\x89PNG\r\n\x1a\n" + b"proof-image"


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
            "member": User(club_id="PP-M01", name="Proof Member", email="member@test.local", role="member"),
            "other": User(club_id="PP-M02", name="Other Member", email="other@test.local", role="member"),
            "treasurer": User(club_id="PP-T01", name="Proof Treasurer", email="treasurer@test.local", role="treasurer"),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        event = Event(name="Proof Event")
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


def approved_claim(ids):
    with app.app_context():
        claim = Claim(title="Taxi", description="", amount=Decimal("45.50"), status="approved",
                      member_id=ids["member"], event_id=ids["event"])
        db.session.add(claim)
        db.session.commit()
        return claim.id


def pay(client, ids, claim_id, proof=None):
    login(client, ids["treasurer"])
    data = {"payment_method": "Bank Transfer", "reference_number": "REF-1"}
    if proof is not None:
        data["payment_proof"] = proof
    return client.post(f"/claims/review/{claim_id}/pay", data=data, content_type="multipart/form-data")


def proofs():
    with app.app_context():
        return db.session.execute(db.select(PaymentProof)).scalars().all()


def test_pay_with_proof_saves_it_and_mentions_it_in_email(case):
    client, ids, sent = case
    claim_id = approved_claim(ids)

    pay(client, ids, claim_id, (io.BytesIO(PNG), "transfer.png"))

    saved = proofs()
    assert len(saved) == 1
    assert saved[0].original_filename == "transfer.png"
    assert saved[0].stored_filename.endswith(".png")
    assert saved[0].data == PNG
    assert "Payment proof: attached on the claim page." in sent[0]["textContent"]


def test_pay_without_proof_still_works(case):
    client, ids, sent = case
    claim_id = approved_claim(ids)

    pay(client, ids, claim_id)

    with app.app_context():
        assert db.session.get(Claim, claim_id).status == "paid"
    assert proofs() == []
    assert "Payment proof" not in sent[0]["textContent"]

    login(client, ids["member"])
    assert client.get(f"/claims/{claim_id}/payment-proof").status_code == 404


def test_invalid_proof_blocks_the_payment(case):
    client, ids, sent = case
    claim_id = approved_claim(ids)

    pay(client, ids, claim_id, (io.BytesIO(b"hello"), "notes.txt"))

    assert ("danger", "Only JPG, PNG and PDF files are allowed.") in flashes(client)
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "approved"
        assert claim.payment is None
    assert sent == []


def test_fake_image_is_rejected_with_proof_wording(case):
    client, ids, _ = case
    claim_id = approved_claim(ids)

    pay(client, ids, claim_id, (io.BytesIO(b"not really a png"), "fake.png"))

    assert ("danger", "Unsupported or invalid payment proof file.") in flashes(client)


def test_who_can_view_and_download_the_proof(case):
    client, ids, _ = case
    claim_id = approved_claim(ids)
    pay(client, ids, claim_id, (io.BytesIO(PNG), "transfer.png"))

    login(client, ids["member"])
    view = client.get(f"/claims/{claim_id}/payment-proof")
    download = client.get(f"/claims/{claim_id}/payment-proof?download=1")
    page = client.get(f"/claims/{claim_id}").get_data(as_text=True)

    assert view.status_code == 200
    assert view.data == PNG
    assert view.mimetype == "image/png"
    assert "attachment" not in view.headers.get("Content-Disposition", "")
    assert view.headers["Cache-Control"] == "private, no-store"
    assert "attachment" in download.headers["Content-Disposition"]
    assert "transfer.png" in download.headers["Content-Disposition"]
    assert f"/claims/{claim_id}/payment-proof" in page

    login(client, ids["other"])
    assert client.get(f"/claims/{claim_id}/payment-proof").status_code == 404

    login(client, ids["treasurer"])
    assert client.get(f"/claims/{claim_id}/payment-proof").status_code == 200


def test_treasurer_can_add_and_replace_proof_later(case):
    client, ids, _ = case
    claim_id = approved_claim(ids)
    pay(client, ids, claim_id)

    page = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)
    client.post(f"/claims/review/{claim_id}/payment-proof",
                data={"payment_proof": (io.BytesIO(PNG), "first.png")}, content_type="multipart/form-data")
    client.post(f"/claims/review/{claim_id}/payment-proof",
                data={"payment_proof": (io.BytesIO(b"%PDF-1.4 receipt"), "second.pdf")},
                content_type="multipart/form-data")

    assert "Not uploaded" in page and "Add payment proof" in page
    saved = proofs()
    assert len(saved) == 1
    assert saved[0].original_filename == "second.pdf"
    with app.app_context():
        details = db.session.execute(
            db.select(AuditLog.details).where(AuditLog.action == "upload_payment_proof").order_by(AuditLog.id)
        ).scalars().all()
    assert details == ["Added payment proof: first.png", "Replaced payment proof: second.pdf"]

    timeline = client.get(f"/claims/review/{claim_id}").get_data(as_text=True)
    assert "Payment proof uploaded" in timeline


def test_proof_cannot_be_added_before_payment(case):
    client, ids, _ = case
    claim_id = approved_claim(ids)
    login(client, ids["treasurer"])

    client.post(f"/claims/review/{claim_id}/payment-proof",
                data={"payment_proof": (io.BytesIO(PNG), "early.png")}, content_type="multipart/form-data")

    assert ("danger", "Payment proof can only be added to a paid claim.") in flashes(client)
    assert proofs() == []


def test_member_cannot_upload_proof(case):
    client, ids, _ = case
    claim_id = approved_claim(ids)
    pay(client, ids, claim_id)
    login(client, ids["member"])

    response = client.post(f"/claims/review/{claim_id}/payment-proof",
                           data={"payment_proof": (io.BytesIO(PNG), "mine.png")},
                           content_type="multipart/form-data")

    assert response.status_code == 403
    assert proofs() == []
