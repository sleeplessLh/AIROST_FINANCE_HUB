from database import db
from models import AuditLog


# Add an audit log entry to the session; the caller commits it.
def record_audit_log(user_id, action, target_type, target_id, details=None):
    audit_log = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )

    db.session.add(audit_log)

    return audit_log
