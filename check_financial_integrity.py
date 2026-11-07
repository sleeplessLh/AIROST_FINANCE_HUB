# ============================================================
# Financial Integrity Checker
# 检查 Claim 和 Payment Records 的数据一致性
# ============================================================

# Decimal：用于精确比较财务金额
from decimal import Decimal

# Counter：统计每个 Claim 对应的 Payment 数量
from collections import Counter

# 导入 Flask Application
from app import app

# 导入 Database 和 ORM Models
from database import db
from models import Claim, Payment


# ============================================================
# Legacy Configuration
# ============================================================

# 已确认属于旧版本的 Paid Claims
# 这些 Claim 在 Payment 功能开发前已经被标记为 paid
# 仅适用于当前开发数据库，不应直接用于生产环境
LEGACY_PAID_CLAIM_IDS = {1, 3}


# ============================================================
# Financial Integrity Validation
# ============================================================


def check_financial_integrity():

    # 建立 Flask Application Context
    with app.app_context():
        # 查询所有 Claim Records
        claims = db.session.execute(db.select(Claim).order_by(Claim.id)).scalars().all()

        # 查询所有 Payment Records
        payments = (
            db.session.execute(db.select(Payment).order_by(Payment.id)).scalars().all()
        )

        # 建立 Claim ID -> Claim Object 的 Dictionary
        claim_map = {claim.id: claim for claim in claims}

        # 建立 Claim ID -> Payment Object 的 Dictionary
        payment_map = {payment.claim_id: payment for payment in payments}

        # 统计每个 Claim 对应的 Payment 数量
        payment_counts = Counter(payment.claim_id for payment in payments)

        # 储存真正的数据完整性异常
        errors = []

        # 储存已确认的历史数据警告
        legacy_warnings = []

        # ====================================================
        # Check 1：Paid Claim Missing Payment
        # ====================================================

        for claim in claims:
            # 只检查已经标记为 paid 的 Claim
            if claim.status != "paid":
                continue

            # 已存在 Payment Record，不属于缺失情况
            if claim.id in payment_map:
                continue

            # 已确认的旧版本 Claim：记录为 Legacy Warning
            if claim.id in LEGACY_PAID_CLAIM_IDS:
                legacy_warnings.append(
                    f"Claim #{claim.id}: Legacy paid claim without payment record."
                )

            # 其他 Paid Claim 缺少 Payment：记录为 Error
            else:
                errors.append(f"Claim #{claim.id}: Paid but Payment Record is missing.")

        # ====================================================
        # Check 2：Non-Paid Claim Has Payment
        # ====================================================

        for claim in claims:
            # 只有 paid 状态才允许存在 Payment Record
            if claim.status != "paid" and claim.id in payment_map:
                errors.append(
                    f"Claim #{claim.id}: Payment exists but status is {claim.status}."
                )

        # ====================================================
        # Check 3：Payment Amount Mismatch
        # ====================================================

        for payment in payments:
            # 根据 Payment.claim_id 查找对应的 Claim
            claim = claim_map.get(payment.claim_id)

            # 如果 Claim 不存在，由 Check 4 负责报告
            if claim is None:
                continue

            # 使用 Decimal 精确比较金额
            if Decimal(payment.amount) != Decimal(claim.amount):
                errors.append(
                    f"Claim #{claim.id}: Payment amount does not match Claim amount."
                )

        # ====================================================
        # Check 4：Orphan Payment Record
        # ====================================================

        for payment in payments:
            # Payment 关联的 Claim 不存在
            if payment.claim_id not in claim_map:
                errors.append(
                    f"Payment #{payment.id}: Referenced Claim does not exist."
                )

        # ====================================================
        # Check 5：Duplicate Payment Records
        # ====================================================

        for claim_id, count in payment_counts.items():
            # 同一个 Claim 不允许有多笔 Payment
            if count > 1:
                errors.append(f"Claim #{claim_id}: {count} Payment Records detected.")

        # ====================================================
        # Display Financial Integrity Report
        # ====================================================

        print("\n========================================")
        print("      FINANCIAL INTEGRITY REPORT")
        print("========================================")

        print(f"Total Claims: {len(claims)}")
        print(f"Total Payments: {len(payments)}")
        print(f"Integrity Errors: {len(errors)}")
        print(f"Legacy Warnings: {len(legacy_warnings)}")

        print("----------------------------------------")

        # 显示真正的数据异常
        if errors:
            print("\nINTEGRITY ERRORS:")

            for index, error in enumerate(errors, start=1):
                print(f"{index}. {error}")

        else:
            print("\nPASS: No unclassified integrity errors found.")

        # 显示旧版本遗留数据警告
        if legacy_warnings:
            print("\nLEGACY WARNINGS:")

            for index, warning in enumerate(legacy_warnings, start=1):
                print(f"{index}. {warning}")

        print("\n========================================\n")

        # 返回检查结果，方便未来自动化测试
        return {"errors": errors, "legacy_warnings": legacy_warnings}


# ============================================================
# Script Entry Point
# ============================================================

# 只有直接执行本文件时才运行检查
if __name__ == "__main__":
    check_financial_integrity()
