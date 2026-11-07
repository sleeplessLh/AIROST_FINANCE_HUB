# ============================================================
# Budget Calculation Utility
# 功能：计算指定 Event 的预算使用情况
# ============================================================

# Decimal：精确处理财务金额
from decimal import Decimal

# func：使用 SQLAlchemy 的 SQL Aggregate Functions
from sqlalchemy import func

# db：Flask-SQLAlchemy Database Instance
from database import db

# Claim：用于查询 Event 对应的 Claims
from models import Claim


# ============================================================
# calculate_event_budget()
# 参数：
# event：Event ORM Object
#
# 返回：
# Dictionary，包含 allocated、committed、remaining、
# utilization_percentage
# ============================================================


def calculate_event_budget(event):

    # 如果 Event 尚未创建 Budget，Allocated Amount 视为 0
    # 否则取得对应 Budget 的 allocated_amount
    allocated = (
        event.budget.allocated_amount if event.budget is not None else Decimal("0.00")
    )

    # 创建 SQL Statement：计算符合条件的 Claim Amount 总和
    statement = (
        db.select(func.sum(Claim.amount))
        # 只统计属于当前 Event 的 Claims
        .where(Claim.event_id == event.id)
        # 只统计 Approved 和 Paid Claims
        .where(Claim.status.in_(["approved", "paid"]))
    )

    # scalar()：取得 SQL SUM() 返回的单个数值
    # 如果没有符合条件的 Claim，SUM() 返回 None
    committed_result = db.session.scalar(statement)

    # 如果 SUM() 返回 None，将 Committed Amount 设为 0
    committed = committed_result if committed_result is not None else Decimal("0.00")

    # Remaining Budget = Allocated Budget - Committed Budget
    remaining = allocated - committed

    # 如果 Allocated Budget 大于 0，计算预算使用百分比
    if allocated > 0:
        # Decimal 运算，避免浮点数精度问题
        utilization_percentage = (committed / allocated) * Decimal("100")

    else:
        # 没有分配预算时，避免除以 0
        utilization_percentage = Decimal("0.00")

    # 返回 Dictionary，供 Flask Route 或 Template 使用
    return {
        "allocated": allocated,
        "committed": committed,
        "remaining": remaining,
        "utilization_percentage": utilization_percentage,
    }
