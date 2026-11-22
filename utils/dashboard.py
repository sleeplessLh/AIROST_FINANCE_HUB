from decimal import Decimal

from sqlalchemy import func

from database import db

from models import Budget, Claim


# Totals for the treasurer dashboard: allocated, committed, remaining and pending claims.
def calculate_treasurer_statistics():


    allocated_statement = db.select(func.sum(Budget.allocated_amount))

    allocated_result = db.session.scalar(allocated_statement)

    total_allocated = (
        allocated_result if allocated_result is not None else Decimal("0.00")
    )


    committed_statement = db.select(func.sum(Claim.amount)).where(
        Claim.status.in_(["approved", "paid"])
    )

    committed_result = db.session.scalar(committed_statement)

    total_committed = (
        committed_result if committed_result is not None else Decimal("0.00")
    )


    total_remaining = total_allocated - total_committed


    pending_statement = db.select(func.count(Claim.id)).where(Claim.status == "pending")

    pending_claims = db.session.scalar(pending_statement)


    return {
        "total_allocated": total_allocated,
        "total_committed": total_committed,
        "total_remaining": total_remaining,
        "pending_claims": pending_claims,
    }


# Claim counts by status and the total amount paid for one member.
def calculate_member_statistics(member_id):


    count_statement = (
        db.select(Claim.status, func.count(Claim.id))
        .where(Claim.member_id == member_id)
        .group_by(Claim.status)
    )

    status_counts = dict(db.session.execute(count_statement).all())

    paid_statement = (
        db.select(func.sum(Claim.amount))
        .where(Claim.member_id == member_id)
        .where(Claim.status == "paid")
    )

    paid_result = db.session.scalar(paid_statement)

    paid_amount = paid_result if paid_result is not None else Decimal("0.00")


    return {
        "total": sum(status_counts.values()),
        "draft": status_counts.get("draft", 0),
        "pending": status_counts.get("pending", 0),
        "approved": status_counts.get("approved", 0),
        "rejected": status_counts.get("rejected", 0),
        "paid": status_counts.get("paid", 0),
        "paid_amount": paid_amount,
    }
