import os

import tempfile

from pathlib import Path

import pytest

from sqlalchemy.exc import SQLAlchemyError

test_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
test_db_path = Path(test_directory.name) / "test_airost.db"

os.environ["DATABASE_URL"] = f"sqlite:///{test_db_path.as_posix()}"

from app import app

from database import db
from models import User, Event, Claim, Payment
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def client():

    app.config["TESTING"] = True

    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.drop_all()

        db.create_all()

        treasurer = User(
            club_id="TEST-T001",
            name="Test Treasurer",
            email="treasurer@test.local",
            role="treasurer",
            active=True,
        )

        treasurer.set_password("TestPassword123!")

        member = User(
            club_id="TEST-M001",
            name="Test Member",
            email="member@test.local",
            role="member",
            active=True,
        )

        member.set_password("TestPassword123!")

        event = Event(name="Automated Testing Event")

        db.session.add_all([treasurer, member, event])

        db.session.commit()

        claim = Claim(
            title="Test Pending Claim",
            description="Testing payment status validation",
            amount="100.00",
            status="pending",
            member_id=member.id,
            event_id=event.id,
        )

        db.session.add(claim)
        db.session.commit()

        treasurer_id = treasurer.id
        claim_id = claim.id


    test_client = app.test_client()

    with test_client.session_transaction() as session:
        session["_user_id"] = str(treasurer_id)
        session["_fresh"] = True

    yield test_client, claim_id

    with app.app_context():
        db.session.remove()


def test_pending_claim_cannot_be_paid(client):

    test_client, claim_id = client

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "TEST-REF-001"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "pending"

        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one_or_none()

        assert payment is None


def test_approved_claim_can_be_paid(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)


        claim.status = "approved"

        db.session.commit()

        db.session.expire_all()

        verified_claim = db.session.get(Claim, claim_id)

        print("Status After Commit:", verified_claim.status)

        assert verified_claim.status == "approved"


    with app.app_context():
        before_payment = db.session.get(Claim, claim_id)

        print("Status Before Payment:", before_payment.status)

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "TEST-PAY-001"},
        follow_redirects=False,
    )


    print("Response Status:", response.status_code)

    print("Redirect Location:", response.headers.get("Location"))

    with test_client.session_transaction() as session:
        print("Flash Messages:", session.get("_flashes", []))

    assert response.status_code == 302

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "paid"

        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one_or_none()

        assert payment is not None

        assert payment.amount == claim.amount

        assert payment.payment_method == "Bank Transfer"

        assert payment.reference_number == "TEST-PAY-001"

        assert payment.claim_id == claim_id


import atexit

def cleanup_test_database():

    with app.app_context():
        db.session.remove()

        db.engine.dispose()

    test_directory.cleanup()


atexit.register(cleanup_test_database)


def test_duplicate_payment_is_prevented(client):

    test_client, claim_id = client


    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        claim.status = "approved"

        db.session.commit()


    first_response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "FIRST-PAYMENT"},
        follow_redirects=False,
    )

    assert first_response.status_code == 302

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "paid"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 1

        first_payment_id = payments[0].id


    second_response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "SECOND-PAYMENT"},
        follow_redirects=False,
    )

    assert second_response.status_code == 302

    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert (
            "danger",
            "Only approved claims can be marked as paid.",
        ) in flash_messages


    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "paid"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 1

        assert payments[0].id == first_payment_id

        assert payments[0].reference_number == "FIRST-PAYMENT"

        assert payments[0].amount == claim.amount


def test_invalid_payment_method_is_rejected(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bitcoin", "reference_number": "INVALID-PAYMENT"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("danger", "Invalid payment method.") in flash_messages

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "approved"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 0


def test_reference_number_too_long_is_rejected(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    long_reference = "A" * 101

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": long_reference},
        follow_redirects=False,
    )

    assert response.status_code == 302

    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("danger", "Reference number is too long.") in flash_messages

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "approved"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 0


def test_member_cannot_make_payment(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

        member = db.session.execute(
            db.select(User).where(User.club_id == "TEST-M001")
        ).scalar_one()

        member_id = member.id

    with test_client.session_transaction() as session:
        session["_user_id"] = str(member_id)
        session["_fresh"] = True

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={
            "payment_method": "Bank Transfer",
            "reference_number": "UNAUTHORIZED-PAYMENT",
        },
        follow_redirects=False,
    )

    print("Authorization Status:", response.status_code)

    print("Authorization Redirect:", response.headers.get("Location"))

    with test_client.session_transaction() as session:
        print("Authorization Flashes:", session.get("_flashes", []))

    assert response.status_code == 403

    assert response.headers.get("Location") is None

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "approved"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 0


def test_unauthenticated_user_cannot_make_payment(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    with test_client.session_transaction() as session:
        session.clear()

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "GUEST-PAYMENT"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    assert "/login" in response.headers["Location"]

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "approved"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 0


def test_reference_number_100_characters_is_accepted(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    reference_number = "A" * 100

    assert len(reference_number) == 100

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": reference_number},
        follow_redirects=False,
    )

    assert response.status_code == 302

    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("success", "Claim marked as paid successfully.") in flash_messages

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "paid"

        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one()

        assert payment.reference_number == reference_number

        assert len(payment.reference_number) == 100

        assert payment.amount == claim.amount


def test_cash_payment_can_be_completed(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Cash", "reference_number": ""},
        follow_redirects=False,
    )

    assert response.status_code == 302

    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("success", "Claim marked as paid successfully.") in flash_messages

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "paid"

        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one()

        assert payment.payment_method == "Cash"

        assert payment.reference_number is None

        assert payment.amount == claim.amount

        treasurer = db.session.execute(
            db.select(User).where(User.club_id == "TEST-T001")
        ).scalar_one()

        assert payment.paid_by_id == treasurer.id

        assert payment.paid_at is not None


def test_payment_database_integrity(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={
            "payment_method": "Bank Transfer",
            "reference_number": "INTEGRITY-TEST-001",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("success", "Claim marked as paid successfully.") in flash_messages

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert claim.status == "paid"

        assert len(payments) == 1

        payment = payments[0]

        assert payment.claim_id == claim.id

        assert payment.amount == claim.amount

        assert payment.payment_method == "Bank Transfer"
        assert payment.reference_number == "INTEGRITY-TEST-001"

        treasurer = db.session.execute(
            db.select(User).where(User.club_id == "TEST-T001")
        ).scalar_one()

        assert payment.paid_by_id == treasurer.id

        assert payment.paid_at is not None

        assert claim.payment is not None

        assert claim.payment.id == payment.id


def test_payment_transaction_rollback(client, monkeypatch):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    original_commit = db.session.commit

    def simulate_commit_failure():
        raise SQLAlchemyError("Simulated database failure")

    monkeypatch.setattr(db.session, "commit", simulate_commit_failure)

    try:
        response = test_client.post(
            f"/claims/review/{claim_id}/pay",
            data={
                "payment_method": "Bank Transfer",
                "reference_number": "ROLLBACK-TEST-001",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302

        with test_client.session_transaction() as session:
            flash_messages = session.get("_flashes", [])

            assert ("danger", "Failed to save payment record.") in flash_messages

    finally:
        monkeypatch.setattr(db.session, "commit", original_commit)

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "approved"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 0


def test_payment_integrity_error_rollback(client, monkeypatch):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    def simulate_integrity_error():
        raise IntegrityError(
            statement="INSERT INTO payments (...) VALUES (...)",
            params={},
            orig=Exception("Simulated UNIQUE constraint failure"),
        )

    with monkeypatch.context() as patcher:
        patcher.setattr(db.session, "commit", simulate_integrity_error)

        response = test_client.post(
            f"/claims/review/{claim_id}/pay",
            data={
                "payment_method": "Bank Transfer",
                "reference_number": "INTEGRITY-ERROR-TEST",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302

        with test_client.session_transaction() as session:
            flash_messages = session.get("_flashes", [])

            assert (
                "danger",
                "Payment could not be saved because of a database constraint. "
                "Please refresh and verify the claim's payment status.",
            ) in flash_messages


    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "approved"

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 0


def test_real_sqlite_unique_constraint(client):

    test_client, claim_id = client

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={
            "payment_method": "Bank Transfer",
            "reference_number": "FIRST-PAYMENT-001",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        first_payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one()

        first_payment_id = first_payment.id

        duplicate_payment = Payment(
            claim_id=claim_id,
            amount=first_payment.amount,
            payment_method="Cash",
            reference_number="DUPLICATE-PAYMENT-002",
            paid_at=first_payment.paid_at,
            paid_by_id=first_payment.paid_by_id,
        )

        db.session.add(duplicate_payment)

        with pytest.raises(IntegrityError):
            db.session.commit()

        db.session.rollback()

        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        assert len(payments) == 1

        assert payments[0].id == first_payment_id

        assert payments[0].reference_number == "FIRST-PAYMENT-001"

        claim = db.session.get(Claim, claim_id)

        assert claim.status == "paid"
