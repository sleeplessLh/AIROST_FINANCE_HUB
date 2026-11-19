"""Draft / receipt deletion and orphan cleanup tests (B6).
Run separately: python -m pytest tests/test_claim_deletion.py -v
Uses a disposable SQLite database and a temporary upload folder.
"""

import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

# Must run before importing app: point the app at a temporary database
_temp = tempfile.TemporaryDirectory(
    prefix="airost_deletion_test_", ignore_cleanup_errors=True
)
_db_path = Path(_temp.name) / "deletion_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

from app import app
from database import db
from models import AuditLog, Claim, Event, Receipt, ReceiptFile, User

PASSWORD = "DeletionPassword123!"
PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


@pytest.fixture
def case(monkeypatch, tmp_path):
    # Every test gets its own empty upload folder, so real receipts are never touched
    monkeypatch.setattr(app, "instance_path", str(tmp_path))

    with app.app_context():
        # Safety check: refuse to run against any database except the temporary one
        actual = Path(db.engine.url.database).resolve()
        assert actual == _db_path.resolve(), f"Unsafe DB target: {actual}"

        db.session.remove()
        db.drop_all()
        db.create_all()

        users = {
            "member": User(
                club_id="DEL-M01", name="Owner", email="m@test.local", role="member"
            ),
            "other": User(
                club_id="DEL-M02",
                name="Other Member",
                email="o@test.local",
                role="member",
            ),
            "treasurer": User(
                club_id="DEL-T01",
                name="Treasurer",
                email="t@test.local",
                role="treasurer",
            ),
        }
        for user in users.values():
            user.active = True
            user.set_password(PASSWORD)
        db.session.add_all(users.values())

        event = Event(name="Deletion Event")
        db.session.add(event)
        db.session.commit()

        ids = {role: user.id for role, user in users.items()}
        ids["event"] = event.id

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    client = app.test_client()
    yield client, ids, tmp_path / "uploads" / "receipts"

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


def create_claim_with_receipt(
    ids, upload_dir, status="draft", member="member", filename="r1.png"
):
    """Create a claim with one receipt stored both on disk and in the database."""
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / filename).write_bytes(PNG)
    with app.app_context():
        claim = Claim(
            title="Test Claim",
            amount=Decimal("10.00"),
            status=status,
            member_id=ids[member],
            event_id=ids["event"],
        )
        receipt = Receipt(original_filename="original.png", stored_filename=filename)
        receipt.file = ReceiptFile(data=PNG)
        claim.receipts.append(receipt)
        db.session.add(claim)
        db.session.commit()
        return claim.id, receipt.id


def count(model):
    with app.app_context():
        return len(db.session.execute(db.select(model)).scalars().all())


def logs_for(action):
    with app.app_context():
        return (
            db.session.execute(db.select(AuditLog).where(AuditLog.action == action))
            .scalars()
            .all()
        )


# ============================================================
# Delete draft claim
# ============================================================
def test_delete_draft_removes_claim_receipts_and_file(case):
    client, ids, upload_dir = case
    claim_id, _ = create_claim_with_receipt(ids, upload_dir)
    login(client, ids["member"])

    response = client.post(f"/claims/{claim_id}/delete")

    assert response.status_code == 302
    assert ("success", "Draft claim deleted.") in flashes(client)
    # Database: cascade removed claim → receipt → receipt_file
    assert count(Claim) == 0
    assert count(Receipt) == 0
    assert count(ReceiptFile) == 0
    # Disk: file removed after commit
    assert not (upload_dir / "r1.png").exists()

    logs = logs_for("delete_draft_claim")
    assert len(logs) == 1
    assert logs[0].target_id == claim_id
    assert logs[0].details == "Title: Test Claim; Receipts: 1"


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "paid"])
def test_non_draft_claim_cannot_be_deleted(case, status):
    client, ids, upload_dir = case
    claim_id, _ = create_claim_with_receipt(ids, upload_dir, status=status)
    login(client, ids["member"])

    client.post(f"/claims/{claim_id}/delete")

    assert ("danger", "Only draft claims can be deleted.") in flashes(client)
    assert count(Claim) == 1
    assert (upload_dir / "r1.png").exists()
    assert logs_for("delete_draft_claim") == []


def test_cannot_delete_another_members_draft(case):
    client, ids, upload_dir = case
    claim_id, _ = create_claim_with_receipt(ids, upload_dir, member="other")
    login(client, ids["member"])

    response = client.post(f"/claims/{claim_id}/delete")

    assert response.status_code == 404
    assert count(Claim) == 1


def test_treasurer_cannot_delete_draft(case):
    client, ids, upload_dir = case
    claim_id, _ = create_claim_with_receipt(ids, upload_dir)
    login(client, ids["treasurer"])

    assert client.post(f"/claims/{claim_id}/delete").status_code == 403
    assert count(Claim) == 1


def test_delete_draft_requires_post(case):
    client, ids, upload_dir = case
    claim_id, _ = create_claim_with_receipt(ids, upload_dir)
    login(client, ids["member"])

    assert client.get(f"/claims/{claim_id}/delete").status_code == 405
    assert count(Claim) == 1


# ============================================================
# Delete single receipt
# ============================================================
@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_receipt_can_be_removed_from_editable_claim(case, status):
    client, ids, upload_dir = case
    claim_id, receipt_id = create_claim_with_receipt(ids, upload_dir, status=status)
    login(client, ids["member"])

    response = client.post(f"/claims/receipts/{receipt_id}/delete")

    assert response.status_code == 302
    assert ("success", "Receipt original.png removed.") in flashes(client)
    # Only the receipt is removed; the claim stays
    assert count(Claim) == 1
    assert count(Receipt) == 0
    assert count(ReceiptFile) == 0
    assert not (upload_dir / "r1.png").exists()

    logs = logs_for("delete_receipt")
    assert len(logs) == 1
    assert logs[0].target_type == "Claim"
    assert logs[0].target_id == claim_id


@pytest.mark.parametrize("status", ["pending", "approved", "paid"])
def test_receipt_cannot_be_removed_from_locked_claim(case, status):
    client, ids, upload_dir = case
    _, receipt_id = create_claim_with_receipt(ids, upload_dir, status=status)
    login(client, ids["member"])

    client.post(f"/claims/receipts/{receipt_id}/delete")

    assert (
        "danger",
        "Receipts can only be removed from draft or rejected claims.",
    ) in flashes(client)
    assert count(Receipt) == 1
    assert (upload_dir / "r1.png").exists()


def test_cannot_remove_another_members_receipt(case):
    client, ids, upload_dir = case
    _, receipt_id = create_claim_with_receipt(ids, upload_dir, member="other")
    login(client, ids["member"])

    assert client.post(f"/claims/receipts/{receipt_id}/delete").status_code == 404
    assert count(Receipt) == 1


def test_removing_receipt_works_when_disk_file_already_missing(case):
    # e.g. after a Render redeploy wiped the disk; only the database copy remains
    client, ids, upload_dir = case
    _, receipt_id = create_claim_with_receipt(ids, upload_dir)
    (upload_dir / "r1.png").unlink()
    login(client, ids["member"])

    response = client.post(f"/claims/receipts/{receipt_id}/delete")

    assert response.status_code == 302
    assert count(Receipt) == 0


# ============================================================
# Buttons on the claim details page
# ============================================================
@pytest.mark.parametrize(
    "status, show_delete_draft, show_remove",
    [
        ("draft", True, True),
        ("rejected", False, True),
        ("pending", False, False),
        ("paid", False, False),
    ],
)
def test_details_page_buttons_follow_status(
    case, status, show_delete_draft, show_remove
):
    client, ids, upload_dir = case
    claim_id, receipt_id = create_claim_with_receipt(ids, upload_dir, status=status)
    login(client, ids["member"])

    html = client.get(f"/claims/{claim_id}").get_data(as_text=True)

    assert (f"/claims/{claim_id}/delete" in html) == show_delete_draft
    assert (f"/claims/receipts/{receipt_id}/delete" in html) == show_remove


# ============================================================
# flask cleanup-receipts
# ============================================================
def test_cleanup_dry_run_lists_orphans_without_deleting(case):
    client, ids, upload_dir = case
    create_claim_with_receipt(ids, upload_dir, filename="used.png")
    (upload_dir / "orphan.png").write_bytes(PNG)

    result = app.test_cli_runner().invoke(args=["cleanup-receipts"])

    assert "orphan.png" in result.output
    assert "used.png" not in result.output
    assert "1 orphan file(s) found" in result.output
    assert (upload_dir / "orphan.png").exists()


def test_cleanup_delete_removes_only_orphans(case):
    client, ids, upload_dir = case
    create_claim_with_receipt(ids, upload_dir, filename="used.png")
    (upload_dir / "orphan.png").write_bytes(PNG)

    result = app.test_cli_runner().invoke(args=["cleanup-receipts", "--delete"])

    assert "Deleted 1 orphan file(s)." in result.output
    assert not (upload_dir / "orphan.png").exists()
    assert (upload_dir / "used.png").exists()


def test_cleanup_without_upload_folder(case):
    client, ids, upload_dir = case

    result = app.test_cli_runner().invoke(args=["cleanup-receipts"])

    assert "No receipt folder found" in result.output
