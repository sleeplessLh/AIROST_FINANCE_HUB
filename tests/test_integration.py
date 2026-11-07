"""AIROST Day 11 — integration and edge-case tests.
Run alone: python -m pytest tests/test_integration.py -v
Uses a disposable SQLite DB and isolated receipt folder, not project data.
"""
import csv
import io
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

# Must be set before importing app, which creates DB tables on import.
_temp = tempfile.TemporaryDirectory(prefix="airost_integration_")
_db_file = Path(_temp.name) / "integration.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file.as_posix()}"

from app import app  # noqa: E402
from database import db  # noqa: E402
from models import Budget, Claim, Event, Payment, Receipt, User  # noqa: E402
from utils.budget import calculate_event_budget  # noqa: E402
from utils.dashboard import calculate_treasurer_statistics  # noqa: E402

D = Decimal
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


@pytest.fixture(scope="module", autouse=True)
def release_database():
    yield
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    _temp.cleanup()


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_file.resolve(), f"Unsafe database target: {actual}"
        db.session.remove()
        db.drop_all()
        db.create_all()
        users = [
            User(club_id="I-M1", name="Member One", email="m1@test.local", role="member", active=True),
            User(club_id="I-M2", name="Member Two", email="m2@test.local", role="member", active=True),
            User(club_id="I-T1", name="Treasurer", email="t@test.local", role="treasurer", active=True),
            User(club_id="I-A1", name="Admin", email="a@test.local", role="admin", active=True),
        ]
        for user in users:
            user.set_password("IntegrationPassword123!")
        events = [Event(name="Integration Alpha"), Event(name="Integration Beta")]
        db.session.add_all(users + events)
        db.session.flush()
        db.session.add_all([
            Budget(event_id=events[0].id, allocated_amount=D("150.00")),
            Budget(event_id=events[1].id, allocated_amount=D("80.00")),
        ])
        db.session.commit()
        ids = {"m1": users[0].id, "m2": users[1].id, "treasurer": users[2].id,
               "admin": users[3].id, "alpha": events[0].id, "beta": events[1].id}
    # save_receipt() uses current_app.instance_path; isolate that path for all tests.
    receipt_root = Path(_temp.name) / "receipts" / str(ids["alpha"])
    receipt_root.mkdir(parents=True, exist_ok=True)
    # Flask current_app resolves to app during requests; change the real app's
    # instance path temporarily instead of patching its LocalProxy.
    monkeypatch.setattr(app, "instance_path", str(receipt_root))
    client = app.test_client()
    yield client, ids, receipt_root
    with app.app_context():
        db.session.remove()


def login(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def submit(client, event_id, amount="30.25", title="Integration expense", filename="receipt.pdf", content=PDF):
    return client.post("/claims/submit", data={
        "event_id": str(event_id), "title": title, "description": "Test receipt",
        "amount": amount, "receipt": (io.BytesIO(content), filename),
    }, content_type="multipart/form-data")


def create_claim(ids, amount="20.00", status="pending", event="alpha", member="m1"):
    with app.app_context():
        obj = Claim(title="Seed claim", description="Seed", amount=D(amount), status=status,
                    member_id=ids[member], event_id=ids[event])
        db.session.add(obj)
        db.session.commit()
        return obj.id


def get_claim(claim_id):
    obj = db.session.get(Claim, claim_id)
    return obj


def amounts(event_id):
    return calculate_event_budget(db.session.get(Event, event_id))


def export_rows(client):
    response = client.get("/claims/export")
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))


# Full workflow: HTTP request -> persisted records -> budget -> payment -> CSV.
def test_full_claim_approval_payment_export_workflow(case):
    client, ids, root = case
    login(client, ids["m1"])
    response = submit(client, ids["alpha"], amount="30.25")
    assert response.status_code == 302
    with app.app_context():
        claim = db.session.scalar(db.select(Claim))
        assert claim is not None and claim.status == "pending"
        assert claim.amount == D("30.25")
        assert len(claim.receipts) == 1
        claim_id = claim.id
        receipt_id = claim.receipts[0].id
        stored_name = claim.receipts[0].stored_filename
        assert amounts(ids["alpha"])["committed"] == D("0.00")
    assert (root / "uploads" / "receipts" / stored_name).is_file()
    assert client.get(f"/claims/receipts/{receipt_id}/download").status_code == 200
    login(client, ids["treasurer"])
    assert client.post(f"/claims/review/{claim_id}/approve").status_code == 302
    with app.app_context():
        assert get_claim(claim_id).status == "approved"
        assert amounts(ids["alpha"])["committed"] == D("30.25")
        assert amounts(ids["alpha"])["remaining"] == D("119.75")
    assert client.post(f"/claims/review/{claim_id}/pay", data={
        "payment_method": "Bank Transfer", "reference_number": "INT-001"}).status_code == 302
    with app.app_context():
        claim = get_claim(claim_id)
        assert claim.status == "paid"
        assert claim.payment is not None
        assert claim.payment.amount == D("30.25")
        assert claim.payment.paid_by_id == ids["treasurer"]
        assert amounts(ids["alpha"])["committed"] == D("30.25")  # no double count
        totals = calculate_treasurer_statistics()
        assert totals["total_allocated"] == D("230.00")
        assert totals["total_committed"] == D("30.25")
        assert totals["total_remaining"] == D("199.75")
    rows = export_rows(client)
    assert len(rows) == 2
    assert rows[1][0] == str(claim_id)
    assert rows[1][1:5] == ["Member One", "Integration Alpha", "30.25", "paid"]


def test_rejected_claim_does_not_consume_budget(case):
    client, ids, _ = case
    claim_id = create_claim(ids, amount="100.00")
    login(client, ids["treasurer"])
    assert client.post(f"/claims/review/{claim_id}/reject").status_code == 302
    with app.app_context():
        assert get_claim(claim_id).status == "rejected"
        assert amounts(ids["alpha"])["remaining"] == D("150.00")


def test_budget_isolated_between_events(case):
    client, ids, _ = case
    a = create_claim(ids, amount="40.00", event="alpha")
    b = create_claim(ids, amount="60.00", event="beta")
    login(client, ids["treasurer"])
    assert client.post(f"/claims/review/{a}/approve").status_code == 302
    assert client.post(f"/claims/review/{b}/approve").status_code == 302
    with app.app_context():
        assert amounts(ids["alpha"])["remaining"] == D("110.00")
        assert amounts(ids["beta"])["remaining"] == D("20.00")
        assert calculate_treasurer_statistics()["total_committed"] == D("100.00")


def test_budget_exhaustion_blocks_next_approval(case):
    client, ids, _ = case
    first = create_claim(ids, amount="150.00")
    second = create_claim(ids, amount="0.01")
    login(client, ids["treasurer"])
    assert client.post(f"/claims/review/{first}/approve").status_code == 302
    assert client.post(f"/claims/review/{second}/approve").status_code == 302
    with app.app_context():
        assert get_claim(first).status == "approved"
        assert get_claim(second).status == "pending"
        assert amounts(ids["alpha"])["remaining"] == D("0.00")


def test_budget_cannot_be_reduced_below_approved_claim(case):
    client, ids, _ = case
    claim_id = create_claim(ids, amount="90.00", status="approved")
    login(client, ids["treasurer"])
    response = client.post(f"/claims/budgets/{ids['alpha']}/update", data={"allocated_amount": "89.99"})
    assert response.status_code == 302
    with app.app_context():
        assert get_claim(claim_id).status == "approved"
        assert db.session.scalar(db.select(Budget).where(Budget.event_id == ids["alpha"])).allocated_amount == D("150.00")


def test_budget_can_be_reduced_to_exact_committed_amount(case):
    client, ids, _ = case
    create_claim(ids, amount="90.00", status="approved")
    login(client, ids["treasurer"])
    response = client.post(f"/claims/budgets/{ids['alpha']}/update", data={"allocated_amount": "90.00"})
    assert response.status_code == 302
    with app.app_context():
        assert amounts(ids["alpha"])["remaining"] == D("0.00")


def test_pending_claim_count_changes_after_review(case):
    client, ids, _ = case
    first = create_claim(ids, amount="10.00")
    second = create_claim(ids, amount="20.00")
    login(client, ids["treasurer"])
    with app.app_context():
        assert calculate_treasurer_statistics()["pending_claims"] == 2
    client.post(f"/claims/review/{first}/approve")
    client.post(f"/claims/review/{second}/reject")
    with app.app_context():
        assert calculate_treasurer_statistics()["pending_claims"] == 0


def test_duplicate_approval_does_not_double_commit(case):
    client, ids, _ = case
    claim_id = create_claim(ids, amount="22.00")
    login(client, ids["treasurer"])
    for _ in range(2):
        assert client.post(f"/claims/review/{claim_id}/approve").status_code == 302
    with app.app_context():
        assert amounts(ids["alpha"])["committed"] == D("22.00")


def test_duplicate_payment_does_not_create_second_payment(case):
    client, ids, _ = case
    claim_id = create_claim(ids, amount="22.00", status="approved")
    login(client, ids["treasurer"])
    for _ in range(2):
        assert client.post(f"/claims/review/{claim_id}/pay", data={
            "payment_method": "Cash", "reference_number": ""}).status_code == 302
    with app.app_context():
        assert get_claim(claim_id).status == "paid"
        assert db.session.scalar(db.select(db.func.count(Payment.id))) == 1


@pytest.mark.parametrize("role", ["m1", "m2", "admin"])
def test_non_treasurer_cannot_approve_or_pay(case, role):
    client, ids, _ = case
    claim_id = create_claim(ids, status="approved")
    login(client, ids[role])
    assert client.post(f"/claims/review/{claim_id}/approve").status_code == 403
    assert client.post(f"/claims/review/{claim_id}/pay", data={"payment_method": "Cash"}).status_code == 403
    with app.app_context():
        assert get_claim(claim_id).payment is None


def test_member_cannot_view_other_members_claim_or_receipt(case):
    client, ids, _ = case
    login(client, ids["m1"])
    assert submit(client, ids["alpha"]).status_code == 302
    with app.app_context():
        claim = db.session.scalar(db.select(Claim))
        claim_id, receipt_id = claim.id, claim.receipts[0].id
    login(client, ids["m2"])
    assert client.get(f"/claims/{claim_id}").status_code == 404
    assert client.get(f"/claims/receipts/{receipt_id}/view").status_code == 404
    assert client.get(f"/claims/receipts/{receipt_id}/download").status_code == 404


def test_treasurer_can_view_members_receipt(case):
    client, ids, _ = case
    login(client, ids["m1"])
    assert submit(client, ids["alpha"]).status_code == 302
    with app.app_context():
        receipt_id = db.session.scalar(db.select(Receipt.id))
    login(client, ids["treasurer"])
    response = client.get(f"/claims/receipts/{receipt_id}/view")
    assert response.status_code == 200
    assert response.data == PDF


def test_guest_cannot_export_or_review(case):
    client, ids, _ = case
    assert client.get("/claims/export").status_code == 302
    assert client.get("/claims/review").status_code == 302
    assert client.post(f"/claims/budgets/{ids['alpha']}/update", data={"allocated_amount": "1"}).status_code == 302


def test_member_cannot_change_budget(case):
    client, ids, _ = case
    login(client, ids["m1"])
    assert client.post(f"/claims/budgets/{ids['alpha']}/update", data={"allocated_amount": "999"}).status_code == 403
    with app.app_context():
        assert amounts(ids["alpha"])["allocated"] == D("150.00")


def test_bad_receipt_does_not_create_claim(case):
    client, ids, _ = case
    login(client, ids["m1"])
    response = submit(client, ids["alpha"], filename="receipt.pdf", content=b"not a PDF")
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Claim.id))) == 0
        assert db.session.scalar(db.select(db.func.count(Receipt.id))) == 0


def test_missing_receipt_does_not_create_claim(case):
    client, ids, _ = case
    login(client, ids["m1"])
    response = client.post("/claims/submit", data={
        "event_id": str(ids["alpha"]), "title": "No receipt", "amount": "10.00"})
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Claim.id))) == 0


@pytest.mark.parametrize("amount", ["-1", "0", "NaN", "Infinity", "1.001", "100000000"])
def test_invalid_amount_rejected_before_receipt_save(case, amount):
    client, ids, _ = case
    login(client, ids["m1"])
    response = submit(client, ids["alpha"], amount=amount)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Claim.id))) == 0


def test_export_reflects_latest_status(case):
    client, ids, _ = case
    claim_id = create_claim(ids, amount="11.50")
    login(client, ids["treasurer"])
    assert export_rows(client)[1][4] == "pending"
    client.post(f"/claims/review/{claim_id}/approve")
    assert export_rows(client)[1][4] == "approved"
    client.post(f"/claims/review/{claim_id}/pay", data={"payment_method": "Cash"})
    assert export_rows(client)[1][4] == "paid"


def test_export_includes_both_events_and_all_claims(case):
    client, ids, _ = case
    create_claim(ids, amount="12.34", event="alpha")
    create_claim(ids, amount="56.78", event="beta")
    login(client, ids["treasurer"])
    rows = export_rows(client)
    assert len(rows) == 3
    assert [r[2] for r in rows[1:]] == ["Integration Alpha", "Integration Beta"]
    assert [r[3] for r in rows[1:]] == ["12.34", "56.78"]


def test_receipt_filename_is_unique_across_submissions(case):
    client, ids, _ = case
    login(client, ids["m1"])
    assert submit(client, ids["alpha"], title="First").status_code == 302
    assert submit(client, ids["alpha"], title="Second").status_code == 302
    with app.app_context():
        names = db.session.scalars(db.select(Receipt.stored_filename)).all()
        assert len(names) == 2
        assert names[0] != names[1]
        assert all(name.endswith(".pdf") for name in names)


def test_payment_does_not_change_original_claim_amount(case):
    client, ids, _ = case
    claim_id = create_claim(ids, amount="12.35", status="approved")
    login(client, ids["treasurer"])
    client.post(f"/claims/review/{claim_id}/pay", data={"payment_method": "Cash"})
    with app.app_context():
        claim = get_claim(claim_id)
        assert claim.amount == D("12.35")
        assert claim.payment.amount == claim.amount
        assert amounts(ids["alpha"])["committed"] == D("12.35")
