# ============================================================
# Audit Log Utility
# 统一记录系统中的重要操作
# ============================================================

from database import db
from models import AuditLog


# ============================================================
# Record Audit Log Function
# ============================================================
def record_audit_log(user_id, action, target_type, target_id, details=None):
    # 创建 AuditLog ORM Object
    audit_log = AuditLog(
        user_id=user_id,  # 执行操作的 User ID
        action=action,  # 操作类型
        target_type=target_type,  # 被操作的资源类型
        target_id=target_id,  # 被操作记录的 ID
        details=details,  # 可选的操作说明
    )

    # 将 AuditLog Object 加入当前 Database Session
    db.session.add(audit_log)

    # 返回 AuditLog Object，供调用者使用
    return audit_log
