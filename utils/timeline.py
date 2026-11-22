from database import db
from models import AuditLog

TIMELINE_ACTIONS = {
    "submit_claim": ("Submitted for review", "info"),
    "resubmit_claim": ("Resubmitted for review", "info"),
    "approve_claim": ("Approved", "success"),
    "reject_claim": ("Rejected", "danger"),
    "mark_claim_paid": ("Paid", "success"),
}


# History of a claim (created, submitted, approved/rejected, paid) from the audit log.
def claim_timeline(claim):
    items = [{
        "label": "Created",
        "actor": claim.member.name,
        "at": claim.created_at,
        "note": None,
        "kind": "neutral",
    }]

    logs = db.session.execute(
        db.select(AuditLog)
        .where(AuditLog.target_type == "Claim")
        .where(AuditLog.target_id == claim.id)
        .where(AuditLog.action.in_(TIMELINE_ACTIONS))
        .order_by(AuditLog.created_at, AuditLog.id)
    ).scalars().all()

    for log in logs:
        label, kind = TIMELINE_ACTIONS[log.action]
        note = log.details if log.action in ("reject_claim", "mark_claim_paid") else None
        items.append({
            "label": label,
            "actor": log.user.name if log.user else f"User #{log.user_id}",
            "at": log.created_at,
            "note": note,
            "kind": kind,
        })

    return items
