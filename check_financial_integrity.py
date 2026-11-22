from decimal import Decimal

from collections import Counter

from app import app

from database import db
from models import Claim, Payment


LEGACY_PAID_CLAIM_IDS = {1, 3}


# Check that paid claims and payment records match (development tool).
def check_financial_integrity():

    with app.app_context():
        claims = db.session.execute(db.select(Claim).order_by(Claim.id)).scalars().all()

        payments = (
            db.session.execute(db.select(Payment).order_by(Payment.id)).scalars().all()
        )

        claim_map = {claim.id: claim for claim in claims}

        payment_map = {payment.claim_id: payment for payment in payments}

        payment_counts = Counter(payment.claim_id for payment in payments)

        errors = []

        legacy_warnings = []


        for claim in claims:
            if claim.status != "paid":
                continue

            if claim.id in payment_map:
                continue

            if claim.id in LEGACY_PAID_CLAIM_IDS:
                legacy_warnings.append(
                    f"Claim #{claim.id}: Legacy paid claim without payment record."
                )

            else:
                errors.append(f"Claim #{claim.id}: Paid but Payment Record is missing.")


        for claim in claims:
            if claim.status != "paid" and claim.id in payment_map:
                errors.append(
                    f"Claim #{claim.id}: Payment exists but status is {claim.status}."
                )


        for payment in payments:
            claim = claim_map.get(payment.claim_id)

            if claim is None:
                continue

            if Decimal(payment.amount) != Decimal(claim.amount):
                errors.append(
                    f"Claim #{claim.id}: Payment amount does not match Claim amount."
                )


        for payment in payments:
            if payment.claim_id not in claim_map:
                errors.append(
                    f"Payment #{payment.id}: Referenced Claim does not exist."
                )


        for claim_id, count in payment_counts.items():
            if count > 1:
                errors.append(f"Claim #{claim_id}: {count} Payment Records detected.")


        print("\n========================================")
        print("      FINANCIAL INTEGRITY REPORT")
        print("========================================")

        print(f"Total Claims: {len(claims)}")
        print(f"Total Payments: {len(payments)}")
        print(f"Integrity Errors: {len(errors)}")
        print(f"Legacy Warnings: {len(legacy_warnings)}")

        print("----------------------------------------")

        if errors:
            print("\nINTEGRITY ERRORS:")

            for index, error in enumerate(errors, start=1):
                print(f"{index}. {error}")

        else:
            print("\nPASS: No unclassified integrity errors found.")

        if legacy_warnings:
            print("\nLEGACY WARNINGS:")

            for index, warning in enumerate(legacy_warnings, start=1):
                print(f"{index}. {warning}")

        print("\n========================================\n")

        return {"errors": errors, "legacy_warnings": legacy_warnings}


if __name__ == "__main__":
    check_financial_integrity()
