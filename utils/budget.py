from decimal import Decimal

from sqlalchemy import func

from database import db

from models import Claim


# Allocated, committed, remaining and utilization figures for one event.
def calculate_event_budget(event):

    allocated = (
        event.budget.allocated_amount if event.budget is not None else Decimal("0.00")
    )

    statement = (
        db.select(func.sum(Claim.amount))
        .where(Claim.event_id == event.id)
        .where(Claim.status.in_(["approved", "paid"]))
    )

    committed_result = db.session.scalar(statement)

    committed = committed_result if committed_result is not None else Decimal("0.00")

    remaining = allocated - committed

    if allocated > 0:
        utilization_percentage = (committed / allocated) * Decimal("100")

    else:
        utilization_percentage = Decimal("0.00")

    return {
        "allocated": allocated,
        "committed": committed,
        "remaining": remaining,
        "utilization_percentage": utilization_percentage,
    }
