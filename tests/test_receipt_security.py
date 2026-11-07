"""Receipt upload/security tests. Run separately: python -m pytest tests/test_receipt_security.py -v"""
import io
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage

# IMPORTANT: point the app to a disposable database BEFORE importing app.
_TEST_DIR = tempfile.TemporaryDirectory(prefix="airost_receipt_security_")
_DB_PATH = Path(_TEST_DIR.name) / "receipt_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"

from app import app
from database import db
from models import Budget, Claim, Event, Receipt, User
from utils.file_upload import MAX_RECEIPT_SIZE, detect_file_type, save_receipt

PNG = b"\x89PNG\r\n\x1a\n" + b"sample-png-data"
JPG = b"\xff\xd8\xff" + b"sample-jpeg-data"
PDF = b"%PDF-1.4\n" + b"sample-pdf-data"


@pytest.fixture
def isolated_app(monkeypatch, tmp_path):
    # All saved files are confined to pytest's temporary directory.
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    with app.app_context():
        actual = Path(db.engine.url.database).resolve()
        assert actual == _DB_PATH.resolve(), f"Unsafe database target: {actual}"
        db.session.remove()
        db.drop_all()
        db.create_all()
        member = User(club_id="RS-M001", name="Receipt Owner", email="owner@receipt.test", role="member", active=True)
        other = User(club_id="RS-M002", name="Other Member", email="other@receipt.test", role="member", active=True)
        treasurer = User(club_id="RS-T001", name="Treasurer", email="treasurer@receipt.test", role="treasurer", active=True)
        for user in (member, other, treasurer):
            user.set_password("TestPassword123!")
        event = Event(name="Receipt Security Event")
        db.session.add_all([member, other, treasurer, event])
        db.session.flush()
        db.session.add(Budget(event_id=event.id, allocated_amount=Decimal("1000.00")))
        db.session.commit()
        ids = {"member": member.id, "other": other.id, "treasurer": treasurer.id, "event": event.id}
    client = app.test_client()
    login(client, ids["member"])
    yield client, ids, tmp_path
    with app.app_context():
        db.session.remove()


def login(client, user_id=None):
    with client.session_transaction() as session:
        session.clear()
        if user_id is not None:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True


def upload(name, content):
    return FileStorage(stream=io.BytesIO(content), filename=name, content_type="application/octet-stream")


def receipt_dir(root):
    return root / "uploads" / "receipts"


def submit(client, ids, filename="receipt.pdf", content=PDF):
    data = {"title": "Receipt Security Claim", "description": "Test", "amount": "12.50",
            "event_id": str(ids["event"]), "receipt": (io.BytesIO(content), filename)}
    return client.post("/claims/submit", data=data, content_type="multipart/form-data", follow_redirects=False)


def counts():
    with app.app_context():
        return (db.session.query(Claim).count(), db.session.query(Receipt).count())


def create_receipt(ids, root):
    with app.app_context():
        claim = Claim(title="Existing", amount=Decimal("12.50"), status="pending",
                      member_id=ids["member"], event_id=ids["event"])
        db.session.add(claim)
        db.session.flush()
        stored = "receipt-existing.pdf"
        db.session.add(Receipt(claim_id=claim.id, original_filename="original.pdf", stored_filename=stored))
        db.session.commit()
        receipt_id = db.session.execute(db.select(Receipt).where(Receipt.claim_id == claim.id)).scalar_one().id
    folder = receipt_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / stored).write_bytes(PDF)
    return receipt_id


# --- Unit tests: signature detection ---
@pytest.mark.parametrize("content, expected", [(JPG, "jpg"), (PNG, "png"), (PDF, "pdf"),
                                               (b"plain text", None), (b"", None), (b"PK\x03\x04", None)])
def test_signature_detection(content, expected):
    assert detect_file_type(content) == expected


@pytest.mark.parametrize("filename, content, extension", [
    ("photo.jpg", JPG, ".jpg"), ("photo.jpeg", JPG, ".jpg"),
    ("image.png", PNG, ".png"), ("invoice.pdf", PDF, ".pdf"),
    ("INVOICE.PDF", PDF, ".pdf"),
])
def test_valid_file_saved_with_uuid(isolated_app, filename, content, extension):
    _, _, root = isolated_app
    with app.app_context():
        original, stored = save_receipt(upload(filename, content))
    assert original
    assert re.fullmatch(r"[0-9a-f]{32}" + re.escape(extension), stored)
    assert (receipt_dir(root) / stored).read_bytes() == content


def test_unique_filenames_for_same_upload(isolated_app):
    _, _, root = isolated_app
    with app.app_context():
        _, first = save_receipt(upload("same.pdf", PDF))
        _, second = save_receipt(upload("same.pdf", PDF))
    assert first != second
    assert len(list(receipt_dir(root).iterdir())) == 2


@pytest.mark.parametrize("filename, content, message", [
    ("script.exe", PDF, "Only JPG, PNG and PDF files are allowed."),
    ("evil.pdf.exe", PDF, "Only JPG, PNG and PDF files are allowed."),
    ("fake.pdf", b"not a pdf", "Unsupported or invalid receipt file."),
    ("fake.png", PDF, "File extension does not match file content."),
    ("fake.pdf", PNG, "File extension does not match file content."),
    ("empty.pdf", b"", "The receipt file is empty."),
    ("large.pdf", b"%PDF-" + b"A" * (MAX_RECEIPT_SIZE - 4), "Receipt file must not exceed 5 MB."),
])
def test_invalid_files_rejected_without_disk_write(isolated_app, filename, content, message):
    _, _, root = isolated_app
    with app.app_context(), pytest.raises(ValueError, match=re.escape(message)):
        save_receipt(upload(filename, content))
    assert not receipt_dir(root).exists() or not list(receipt_dir(root).iterdir())


@pytest.mark.parametrize("filename", ["", "////", ".."])
def test_empty_or_unsafe_filename_rejected(isolated_app, filename):
    _, _, root = isolated_app
    with app.app_context(), pytest.raises(ValueError):
        save_receipt(upload(filename, PDF))
    assert not receipt_dir(root).exists()


def test_missing_file_rejected(isolated_app):
    with app.app_context(), pytest.raises(ValueError, match="Please select a receipt file"):
        save_receipt(None)


def test_exactly_five_megabytes_accepted(isolated_app):
    _, _, root = isolated_app
    content = b"%PDF-" + b"A" * (MAX_RECEIPT_SIZE - 5)
    with app.app_context():
        _, stored = save_receipt(upload("max.pdf", content))
    assert (receipt_dir(root) / stored).stat().st_size == MAX_RECEIPT_SIZE


def test_path_traversal_filename_is_sanitized(isolated_app):
    _, _, root = isolated_app
    with app.app_context():
        original, stored = save_receipt(upload("../../secret.pdf", PDF))
    assert "/" not in original and "\\" not in original
    assert "/" not in stored and "\\" not in stored
    assert (receipt_dir(root) / stored).read_bytes() == PDF
    assert not (root / "secret.pdf").exists()


# --- Integration tests: real upload + database + access control ---
@pytest.mark.parametrize("filename, content", [("receipt.jpg", JPG), ("receipt.png", PNG), ("receipt.pdf", PDF)])
def test_real_submission_persists_file_and_relationship(isolated_app, filename, content):
    client, ids, root = isolated_app
    response = submit(client, ids, filename, content)
    assert response.status_code == 302
    assert counts() == (1, 1)
    with app.app_context():
        claim = db.session.execute(db.select(Claim)).scalar_one()
        assert claim.status == "pending"
        assert claim.member_id == ids["member"]
        assert claim.event_id == ids["event"]
        receipt = claim.receipts[0]
        assert receipt.original_filename == filename
        assert (receipt_dir(root) / receipt.stored_filename).read_bytes() == content


@pytest.mark.parametrize("filename, content", [
    ("malware.exe", PDF), ("fake.pdf", b"not a pdf"),
    ("wrong.png", PDF), ("empty.pdf", b""),
    ("oversized.pdf", b"%PDF-" + b"A" * (MAX_RECEIPT_SIZE - 4)),
])
def test_rejected_upload_does_not_create_database_records(isolated_app, filename, content):
    client, ids, root = isolated_app
    response = submit(client, ids, filename, content)
    assert response.status_code == 200
    assert counts() == (0, 0)
    assert not receipt_dir(root).exists() or not list(receipt_dir(root).iterdir())


def test_missing_receipt_does_not_create_claim(isolated_app):
    client, ids, _ = isolated_app
    response = client.post("/claims/submit", data={"title": "Missing Receipt", "amount": "10.00", "event_id": str(ids["event"])})
    assert response.status_code == 200
    assert counts() == (0, 0)


def test_database_commit_failure_cleans_uploaded_file(isolated_app):
    client, ids, root = isolated_app
    with patch.object(db.session, "commit", side_effect=RuntimeError("Simulated database failure")):
        with pytest.raises(RuntimeError, match="Simulated database failure"):
            submit(client, ids)
    assert counts() == (0, 0)
    assert not list(receipt_dir(root).iterdir())


@pytest.mark.parametrize("action", ["view", "download"])
def test_owner_can_access_receipt(isolated_app, action):
    client, ids, root = isolated_app
    receipt_id = create_receipt(ids, root)
    response = client.get(f"/claims/receipts/{receipt_id}/{action}")
    assert response.status_code == 200
    assert response.data == PDF
    assert "private" in response.headers.get("Cache-Control", "")
    assert "no-store" in response.headers.get("Cache-Control", "")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    if action == "download":
        assert "attachment" in response.headers.get("Content-Disposition", "")
        assert "original.pdf" in response.headers.get("Content-Disposition", "")
    else:
        assert "attachment" not in response.headers.get("Content-Disposition", "")


@pytest.mark.parametrize("action", ["view", "download"])
def test_treasurer_can_access_receipt(isolated_app, action):
    client, ids, root = isolated_app
    receipt_id = create_receipt(ids, root)
    login(client, ids["treasurer"])
    assert client.get(f"/claims/receipts/{receipt_id}/{action}").status_code == 200


@pytest.mark.parametrize("action", ["view", "download"])
def test_other_member_cannot_access_receipt(isolated_app, action):
    client, ids, root = isolated_app
    receipt_id = create_receipt(ids, root)
    login(client, ids["other"])
    assert client.get(f"/claims/receipts/{receipt_id}/{action}").status_code == 404


@pytest.mark.parametrize("action", ["view", "download"])
def test_guest_cannot_access_receipt(isolated_app, action):
    client, ids, root = isolated_app
    receipt_id = create_receipt(ids, root)
    login(client, None)
    response = client.get(f"/claims/receipts/{receipt_id}/{action}")
    assert response.status_code == 302


@pytest.mark.parametrize("action", ["view", "download"])
def test_missing_receipt_returns_404(isolated_app, action):
    client, _, _ = isolated_app
    assert client.get(f"/claims/receipts/999999/{action}").status_code == 404


def test_treasurer_cannot_submit_receipt(isolated_app):
    client, ids, _ = isolated_app
    login(client, ids["treasurer"])
    assert submit(client, ids).status_code == 403
    assert counts() == (0, 0)


def test_guest_cannot_submit_receipt(isolated_app):
    client, ids, _ = isolated_app
    login(client, None)
    assert submit(client, ids).status_code == 302
    assert counts() == (0, 0)
