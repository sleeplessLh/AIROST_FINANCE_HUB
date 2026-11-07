"""AIROST Day 10: Budget, dashboard statistics, CSV export and security.
Run separately: python -m pytest tests/test_budget_export.py -v
Only a disposable SQLite database is used. No production database is modified.
"""
import csv
import io
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

# IMPORTANT: Set DB target BEFORE importing the Flask application.
_test_dir = tempfile.TemporaryDirectory(prefix="airost_budget_test_")
_db_path = Path(_test_dir.name) / "budget_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app  # noqa: E402
from database import db  # noqa: E402
from models import Budget, Claim, Event, User  # noqa: E402
from utils.budget import calculate_event_budget  # noqa: E402
from utils.dashboard import calculate_treasurer_statistics  # noqa: E402

D = Decimal


@pytest.fixture(scope="module", autouse=True)
def close_test_database():
    yield
    # Release SQLite handles before Windows attempts to remove the temporary directory.
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    _test_dir.cleanup()


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe database target: {actual}"
        db.session.remove()
        db.drop_all()
        db.create_all()
        member = User(club_id="B-M01", name="Member", email="bm@test.local", role="member", active=True)
        treasurer = User(club_id="B-T01", name="Treasurer", email="bt@test.local", role="treasurer", active=True)
        admin = User(club_id="B-A01", name="Admin", email="ba@test.local", role="admin", active=True)
        for user in (member, treasurer, admin):
            user.set_password("BudgetTestPassword123!")
        event_a = Event(name="Alpha Event")
        event_b = Event(name="Beta Event")
        db.session.add_all([member, treasurer, admin, event_a, event_b])
        db.session.commit()
        ids = {"member": member.id, "treasurer": treasurer.id, "admin": admin.id,
               "a": event_a.id, "b": event_b.id}
    client = app.test_client()
    yield client, ids
    with app.app_context():
        db.session.remove()


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def budget(event_id, amount):
    obj = Budget(event_id=event_id, allocated_amount=D(amount))
    db.session.add(obj)
    db.session.commit()
    return obj


def claim(ids, event="a", amount="10.00", status="pending", title="Expense"):
    obj = Claim(title=title, description="Test expense", amount=D(amount), status=status,
                member_id=ids["member"], event_id=ids[event])
    db.session.add(obj)
    db.session.commit()
    return obj


def stats(event_id):
    return calculate_event_budget(db.session.get(Event, event_id))


def update(client, event_id, value):
    return client.post(f"/claims/budgets/{event_id}/update", data={"allocated_amount": value})


def read_csv(response):
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))


# --- Per-event calculation ---
def test_event_without_budget_or_claims(case):
    _, ids = case
    with app.app_context():
        assert stats(ids["a"]) == {
            "allocated": D("0.00"), "committed": D("0.00"),
            "remaining": D("0.00"), "utilization_percentage": D("0.00")}


def test_budget_without_claims(case):
    _, ids = case
    with app.app_context():
        budget(ids["a"], "100.00")
        s = stats(ids["a"])
        assert (s["allocated"], s["committed"], s["remaining"], s["utilization_percentage"]) == (
            D("100.00"), D("0.00"), D("100.00"), D("0.00"))


@pytest.mark.parametrize("status,committed", [
    ("pending", "0.00"), ("rejected", "0.00"),
    ("approved", "25.25"), ("paid", "25.25")])
def test_claim_status_budget_effect(case, status, committed):
    _, ids = case
    with app.app_context():
        budget(ids["a"], "100.00")
        claim(ids, amount="25.25", status=status)
        s = stats(ids["a"])
        assert s["committed"] == D(committed)
        assert s["remaining"] == D("100.00") - D(committed)


def test_budget_sum_and_utilization(case):
    _, ids = case
    with app.app_context():
        budget(ids["a"], "200.00")
        for amount, status in [("25.25", "approved"), ("74.75", "paid"),
                               ("100", "pending"), ("100", "rejected")]:
            claim(ids, amount=amount, status=status)
        s = stats(ids["a"])
        assert s["committed"] == D("100.00")
        assert s["remaining"] == D("100.00")
        assert s["utilization_percentage"] == D("50.00")


def test_event_budget_isolation(case):
    _, ids = case
    with app.app_context():
        budget(ids["a"], "100")
        budget(ids["b"], "500")
        claim(ids, "a", "40", "approved")
        claim(ids, "b", "300", "paid")
        assert stats(ids["a"])["remaining"] == D("60.00")
        assert stats(ids["b"])["remaining"] == D("200.00")


def test_zero_allocation_avoids_division_by_zero(case):
    _, ids = case
    with app.app_context():
        budget(ids["a"], "0")
        claim(ids, amount="1", status="approved")  # Legacy/inconsistent data: utility must not crash.
        s = stats(ids["a"])
        assert s["remaining"] == D("-1.00")
        assert s["utilization_percentage"] == D("0.00")


# --- System-wide dashboard statistics ---
def test_dashboard_empty(case):
    with app.app_context():
        s = calculate_treasurer_statistics()
        assert s == {"total_allocated": D("0.00"), "total_committed": D("0.00"),
                     "total_remaining": D("0.00"), "pending_claims": 0}


def test_dashboard_multiple_events_and_statuses(case):
    _, ids = case
    with app.app_context():
        budget(ids["a"], "100.50")
        budget(ids["b"], "200.25")
        for event, amount, status in [
            ("a", "20.10", "approved"), ("b", "30.20", "paid"),
            ("a", "10", "pending"), ("b", "5", "pending"), ("b", "6", "rejected")]:
            claim(ids, event, amount, status)
        s = calculate_treasurer_statistics()
        assert s["total_allocated"] == D("300.75")
        assert s["total_committed"] == D("50.30")
        assert s["total_remaining"] == D("250.45")
        assert s["pending_claims"] == 2


def test_dashboard_pending_count_updates(case):
    _, ids = case
    with app.app_context():
        c = claim(ids, status="pending")
        assert calculate_treasurer_statistics()["pending_claims"] == 1
        c.status = "approved"
        db.session.commit()
        assert calculate_treasurer_statistics()["pending_claims"] == 0


# --- Budget HTTP routes ---
def test_treasurer_budget_page(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    response = client.get("/claims/budgets")
    assert response.status_code == 200
    assert b"Alpha Event" in response.data and b"Beta Event" in response.data


def test_treasurer_can_create_budget(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], "123.45").status_code == 302
    with app.app_context():
        obj = db.session.scalar(db.select(Budget).where(Budget.event_id == ids["a"]))
        assert obj is not None and obj.allocated_amount == D("123.45")


def test_treasurer_can_update_existing_budget(case):
    client, ids = case
    with app.app_context():
        budget(ids["a"], "100")
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], "150.25").status_code == 302
    with app.app_context():
        records = db.session.scalars(db.select(Budget).where(Budget.event_id == ids["a"])).all()
        assert len(records) == 1
        assert records[0].allocated_amount == D("150.25")


def test_zero_budget_is_allowed(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], "0").status_code == 302
    with app.app_context():
        assert stats(ids["a"])["allocated"] == D("0.00")


@pytest.mark.parametrize("value", [
    "", "not-a-number", "-1", "-0.01", "1.001", "0.001", "NaN",
    "Infinity", "-Infinity", "100000000", "999999999.99"])
def test_invalid_budget_rejected_without_creating_record(case, value):
    client, ids = case
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], value).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Budget).where(Budget.event_id == ids["a"])) is None


def test_maximum_valid_budget(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], "99999999.99").status_code == 302
    with app.app_context():
        assert stats(ids["a"])["allocated"] == D("99999999.99")


def test_budget_cannot_fall_below_committed(case):
    client, ids = case
    with app.app_context():
        budget(ids["a"], "200")
        claim(ids, amount="100.50", status="approved")
        claim(ids, amount="50.25", status="paid")
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], "150.74").status_code == 302
    with app.app_context():
        assert stats(ids["a"])["allocated"] == D("200.00")


def test_budget_can_equal_committed(case):
    client, ids = case
    with app.app_context():
        budget(ids["a"], "200")
        claim(ids, amount="75.50", status="approved")
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], "75.50").status_code == 302
    with app.app_context():
        assert stats(ids["a"])["remaining"] == D("0.00")


def test_budget_reduction_ignores_pending_and_rejected(case):
    client, ids = case
    with app.app_context():
        budget(ids["a"], "200")
        claim(ids, amount="190", status="pending")
        claim(ids, amount="190", status="rejected")
    sign_in(client, ids["treasurer"])
    assert update(client, ids["a"], "0").status_code == 302
    with app.app_context():
        assert stats(ids["a"])["allocated"] == D("0.00")


def test_budget_nonexistent_event_returns_404(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    assert update(client, 999999, "100").status_code == 404


@pytest.mark.parametrize("role", ["member", "admin"])
def test_non_treasurer_cannot_view_or_update_budget(case, role):
    client, ids = case
    sign_in(client, ids[role])
    assert client.get("/claims/budgets").status_code == 403
    assert update(client, ids["a"], "100").status_code == 403
    with app.app_context():
        assert db.session.scalar(db.select(Budget)) is None


def test_guest_cannot_view_or_update_budget(case):
    client, ids = case
    assert client.get("/claims/budgets").status_code == 302
    assert update(client, ids["a"], "100").status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Budget)) is None


def test_budget_update_get_not_allowed(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    assert client.get(f"/claims/budgets/{ids['a']}/update").status_code == 405


def test_budget_update_requires_csrf(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    app.config["WTF_CSRF_ENABLED"] = True
    assert update(client, ids["a"], "100").status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(Budget)) is None


# --- CSV export ---
def test_csv_empty_export_has_header_and_bom(case):
    client, ids = case
    sign_in(client, ids["treasurer"])
    response = client.get("/claims/export")
    rows = read_csv(response)
    assert rows == [["Claim ID", "Member", "Event", "Amount (RM)", "Status", "Submitted At"]]
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "claims_report.csv" in response.headers["Content-Disposition"]


def test_csv_export_records_sorted_and_formatted(case):
    client, ids = case
    with app.app_context():
        first = claim(ids, "b", "12.30", "pending", "First")
        second = claim(ids, "a", "5.00", "approved", "Second")
        first_id, second_id = first.id, second.id
    sign_in(client, ids["treasurer"])
    rows = read_csv(client.get("/claims/export"))
    assert len(rows) == 3
    assert rows[1][:5] == [str(first_id), "Member", "Beta Event", "12.30", "pending"]
    assert rows[2][:5] == [str(second_id), "Member", "Alpha Event", "5.00", "approved"]
    assert len(rows[1][5]) == 16 and len(rows[2][5]) == 16


def test_csv_export_utf8_and_csv_quoting(case):
    client, ids = case
    with app.app_context():
        member = db.session.get(User, ids["member"])
        event = db.session.get(Event, ids["a"])
        member.name = '张三, "Finance"'
        event.name = "活动\n第一场"
        db.session.commit()
        claim(ids, "a", "8.80", "paid")
    sign_in(client, ids["treasurer"])
    rows = read_csv(client.get("/claims/export"))
    assert rows[1][1:5] == ['张三, "Finance"', "活动\n第一场", "8.80", "paid"]


def test_csv_export_does_not_mutate_claims(case):
    client, ids = case
    with app.app_context():
        c = claim(ids, amount="15", status="pending")
        claim_id = c.id
    sign_in(client, ids["treasurer"])
    read_csv(client.get("/claims/export"))
    with app.app_context():
        assert db.session.get(Claim, claim_id).status == "pending"


@pytest.mark.parametrize("role", ["member", "admin"])
def test_csv_export_forbidden_to_non_treasurer(case, role):
    client, ids = case
    sign_in(client, ids[role])
    assert client.get("/claims/export").status_code == 403


def test_csv_export_guest_redirected(case):
    client, _ = case
    response = client.get("/claims/export")
    assert response.status_code == 302
    assert "/login" in response.location


@pytest.mark.xfail(strict=True, reason="CSV export currently writes spreadsheet formula prefixes without neutralization")
@pytest.mark.parametrize("dangerous", ["=1+1", "+SUM(1,1)", "-1+2", "@SUM(1,1)"])
def test_csv_formula_injection_is_neutralized(case, dangerous):
    client, ids = case
    with app.app_context():
        db.session.get(User, ids["member"]).name = dangerous
        db.session.commit()
        claim(ids)
    sign_in(client, ids["treasurer"])
    rows = read_csv(client.get("/claims/export"))
    exported_name = rows[1][1]
    assert not exported_name.startswith(("=", "+", "-", "@"))
