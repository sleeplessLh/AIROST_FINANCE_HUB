# ============================================================
# Treasurer Dashboard Statistics Utility
# 功能：统计整个系统的 Budget 和 Claim Information
# ============================================================

# Decimal：精确处理财务金额
from decimal import Decimal

# func：使用 SQL Aggregate Functions，例如 SUM() 和 COUNT()
from sqlalchemy import func

# db：Flask-SQLAlchemy Database Instance
from database import db

# Budget 和 Claim：SQLAlchemy ORM Models
from models import Budget, Claim


# ============================================================
# calculate_treasurer_statistics()
# 参数：无
# 返回：Dictionary，包含四项 Dashboard Statistics
# ============================================================


def calculate_treasurer_statistics():

    # --------------------------------------------------------
    # 1. Total Allocated Budget
    # --------------------------------------------------------

    # 使用 SQL SUM() 计算所有 Budget 的 allocated_amount 总和
    allocated_statement = db.select(func.sum(Budget.allocated_amount))

    # scalar()：执行 SQL，并取得单个结果
    allocated_result = db.session.scalar(allocated_statement)

    # 如果 Database 中没有 Budget，SUM() 返回 None
    total_allocated = (
        allocated_result if allocated_result is not None else Decimal("0.00")
    )

    # --------------------------------------------------------
    # 2. Total Committed Budget
    # --------------------------------------------------------

    # 只计算 Approved 和 Paid Claims 的金额
    committed_statement = db.select(func.sum(Claim.amount)).where(
        Claim.status.in_(["approved", "paid"])
    )

    # 执行 SQL SUM()
    committed_result = db.session.scalar(committed_statement)

    # 如果没有符合条件的 Claims，Committed Amount 为 0
    total_committed = (
        committed_result if committed_result is not None else Decimal("0.00")
    )

    # --------------------------------------------------------
    # 3. Total Remaining Budget
    # --------------------------------------------------------

    # Remaining = Allocated - Committed
    total_remaining = total_allocated - total_committed

    # --------------------------------------------------------
    # 4. Pending Claims Count
    # --------------------------------------------------------

    # COUNT()：统计 Pending Claims 的数量
    pending_statement = db.select(func.count(Claim.id)).where(Claim.status == "pending")

    # scalar()：返回符合条件的 Claim 数量
    pending_claims = db.session.scalar(pending_statement)

    # --------------------------------------------------------
    # 5. Return Statistics
    # --------------------------------------------------------

    # 返回 Dictionary，供 Flask Route 和 Jinja2 Template 使用
    return {
        "total_allocated": total_allocated,
        "total_committed": total_committed,
        "total_remaining": total_remaining,
        "pending_claims": pending_claims,
    }
