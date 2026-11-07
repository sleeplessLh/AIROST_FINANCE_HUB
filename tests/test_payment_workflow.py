# ============================================================
# Automated Payment Workflow Test
# Step 5.3：Pending Claim Cannot Be Paid
# ============================================================

# os：设置测试专用的 Database URL
import os

# tempfile：创建临时目录，避免影响开发数据库
import tempfile

# Path：处理文件路径
from pathlib import Path

# pytest：Python 自动化测试框架
import pytest

from sqlalchemy.exc import SQLAlchemyError

# 在导入 app.py 之前，指定独立的 Testing Database
# TemporaryDirectory 会在测试完成后清理临时文件
test_directory = tempfile.TemporaryDirectory()

# 建立独立 SQLite Database 的绝对路径
test_db_path = Path(test_directory.name) / "test_airost.db"

# 使用绝对路径构造 SQLite Database URL
os.environ["DATABASE_URL"] = f"sqlite:///{test_db_path.as_posix()}"

# 导入 Flask Application
# 此时 app.py 会使用上面指定的 Testing Database
from app import app

# 导入 Database 和 ORM Models
from database import db
from models import User, Event, Claim, Payment
from sqlalchemy.exc import IntegrityError


# ============================================================
# Test Fixture
# ============================================================


@pytest.fixture
def client():

    # 启用 Flask Testing Mode
    app.config["TESTING"] = True

    # 测试环境禁用 CSRF，方便直接模拟 POST Request
    # 仅影响当前测试进程
    app.config["WTF_CSRF_ENABLED"] = False

    # 建立 Flask Application Context
    with app.app_context():
        # 清空 Testing Database 中的所有 Tables
        db.drop_all()

        # 根据 Models 重新创建 Tables
        db.create_all()

        # 创建测试 Treasurer
        treasurer = User(
            club_id="TEST-T001",
            name="Test Treasurer",
            email="treasurer@test.local",
            role="treasurer",
            active=True,
        )

        # 设置测试密码
        treasurer.set_password("TestPassword123!")

        # 创建测试 Member
        member = User(
            club_id="TEST-M001",
            name="Test Member",
            email="member@test.local",
            role="member",
            active=True,
        )

        member.set_password("TestPassword123!")

        # 创建测试 Event
        event = Event(name="Automated Testing Event")

        # 将测试数据加入 Session
        db.session.add_all([treasurer, member, event])

        # 提交并生成 Primary Keys
        db.session.commit()

        # 创建 Pending Claim
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

        # 保存测试所需的 Primary Keys
        treasurer_id = treasurer.id
        claim_id = claim.id

        # 创建 Flask Test Client

    # 这里已经退出 with app.app_context()
    # 原本的 Application Context 已经结束

    # 创建 Flask Test Client
    test_client = app.test_client()

    # 模拟已经登录的 Treasurer Session
    with test_client.session_transaction() as session:
        session["_user_id"] = str(treasurer_id)
        session["_fresh"] = True

    # yield：将 Test Client 和 Claim ID 交给测试函数
    # 此时不会保留 Fixture 最初的 Application Context
    yield test_client, claim_id

    # 测试结束后，清理 Database Session
    with app.app_context():
        db.session.remove()


# ============================================================
# Test 1：Pending Claim Cannot Be Paid
# ============================================================


def test_pending_claim_cannot_be_paid(client):

    # 取得 Fixture 提供的 Test Client 和 Claim ID
    test_client, claim_id = client

    # 模拟 Treasurer 提交 Payment Form
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "TEST-REF-001"},
        follow_redirects=False,
    )

    # 被拒绝后应该 Redirect 到 Claim Details
    assert response.status_code == 302

    # 检查 Database 中的实际结果
    with app.app_context():
        # 重新取得 Claim Object
        claim = db.session.get(Claim, claim_id)

        # Pending Claim 不应该被修改成 Paid
        assert claim.status == "pending"

        # 查询是否存在关联的 Payment Record
        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one_or_none()

        # 不应该产生 Payment Record
        assert payment is None


# ============================================================
# Test 2：Approved Claim Can Be Paid
# ============================================================
def test_approved_claim_can_be_paid(client):

    # 取得 Fixture 提供的 Test Client 和 Claim ID
    test_client, claim_id = client

    # 先将测试 Claim 修改为 Approved
    with app.app_context():
        # 根据 Primary Key 查询 Claim
        claim = db.session.get(Claim, claim_id)


        # 将 Claim Status 修改为 Approved
        claim.status = "approved"

        # 保存到 Testing Database
        db.session.commit()

        # 清除当前 Session 中缓存的 ORM Objects
        db.session.expire_all()

        # 重新从 Database 查询 Claim
        verified_claim = db.session.get(Claim, claim_id)

        # 打印 Commit 后读取到的状态
        print("Status After Commit:", verified_claim.status)

        # 确认修改确实成功
        assert verified_claim.status == "approved"


    # 使用新的 Application Context 检查付款前的状态
    with app.app_context():
        # 查询 Claim
        before_payment = db.session.get(Claim, claim_id)

        # 查看付款前的实际状态
        print("Status Before Payment:", before_payment.status)

    # 模拟 Treasurer 提交 Payment Form
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "TEST-PAY-001"},
        follow_redirects=False,
    )


    # 查看 Flask 返回的 HTTP Status Code
    print("Response Status:", response.status_code)

    # 查看 Flask Redirect 到哪个 URL
    print("Redirect Location:", response.headers.get("Location"))

    # 查看 Flask Session 中保存的 Flash Messages
    with test_client.session_transaction() as session:
        print("Flash Messages:", session.get("_flashes", []))

    # 成功付款后，Flask 应该返回 Redirect
    assert response.status_code == 302

    # 检查实际 Database 结果
    with app.app_context():
        # 重新查询 Claim
        claim = db.session.get(Claim, claim_id)

        # 验证 Claim Status 已经更新为 Paid
        assert claim.status == "paid"

        # 查询对应的 Payment Record
        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one_or_none()

        # 验证 Payment Record 确实存在
        assert payment is not None

        # 验证 Payment Amount 与 Claim Amount 一致
        assert payment.amount == claim.amount

        # 验证 Payment Method
        assert payment.payment_method == "Bank Transfer"

        # 验证 Reference Number
        assert payment.reference_number == "TEST-PAY-001"

        # 验证 Payment Record 属于正确的 Claim
        assert payment.claim_id == claim_id


# ============================================================
# Testing Database Cleanup
# ============================================================

# atexit：注册 Python Process 结束时执行的清理函数
import atexit

def cleanup_test_database():

    # Flask-SQLAlchemy 需要 Application Context
    with app.app_context():
        # 关闭当前 Session 并释放其 Connection
        db.session.remove()

        # 释放 SQLAlchemy Engine 管理的数据库连接
        db.engine.dispose()

    # 删除临时目录及其中的 Testing Database
    test_directory.cleanup()


# 注册清理函数
# Python 正常结束时会调用 cleanup_test_database()
atexit.register(cleanup_test_database)


# ============================================================
# Test 3：Duplicate Payment Prevention
# ============================================================


def test_duplicate_payment_is_prevented(client):

    # 取得 Fixture 提供的 Test Client 和 Claim ID
    test_client, claim_id = client

    # ========================================================
    # Step 1：准备 Approved Claim
    # ========================================================

    with app.app_context():
        # 根据 Primary Key 查询 Claim
        claim = db.session.get(Claim, claim_id)

        # 将 Claim 修改为 Approved
        claim.status = "approved"

        # 保存到 Testing Database
        db.session.commit()

    # ========================================================
    # Step 2：第一次 Payment Request
    # ========================================================

    # 模拟 Treasurer 第一次确认付款
    first_response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "FIRST-PAYMENT"},
        follow_redirects=False,
    )

    # 成功付款后应该 Redirect
    assert first_response.status_code == 302

    # 验证第一次付款确实成功
    with app.app_context():
        # 重新查询 Claim
        claim = db.session.get(Claim, claim_id)

        # Claim 应该已经变成 Paid
        assert claim.status == "paid"

        # 查询当前 Claim 的 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 第一次付款后应该只有一笔记录
        assert len(payments) == 1

        # 保存第一次付款记录的 ID
        first_payment_id = payments[0].id

    # ========================================================
    # Step 3：第二次 Payment Request
    # ========================================================

    # 模拟 Treasurer 对同一个 Claim 再次确认付款
    second_response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "SECOND-PAYMENT"},
        follow_redirects=False,
    )

    # 系统应该拒绝第二次付款并 Redirect
    assert second_response.status_code == 302

    # 检查第二次请求的 Flash Message
    with test_client.session_transaction() as session:
        # 取得所有尚未消费的 Flash Messages
        flash_messages = session.get("_flashes", [])

        # 第二次请求应该被状态检查拒绝
        assert (
            "danger",
            "Only approved claims can be marked as paid.",
        ) in flash_messages

    # ========================================================
    # Step 4：检查最终 Database 状态
    # ========================================================

    with app.app_context():
        # 重新查询 Claim
        claim = db.session.get(Claim, claim_id)

        # Claim 必须保持 Paid
        assert claim.status == "paid"

        # 查询所有关联的 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 核心验证：不能创建第二笔 Payment
        assert len(payments) == 1

        # 原本的 Payment Record 必须保持不变
        assert payments[0].id == first_payment_id

        # Reference Number 不应该被第二次请求覆盖
        assert payments[0].reference_number == "FIRST-PAYMENT"

        # Payment Amount 必须保持正确
        assert payments[0].amount == claim.amount


# ============================================================
# Test 4：Invalid Payment Method Validation
# ============================================================


def test_invalid_payment_method_is_rejected(client):

    # 取得 Fixture 提供的 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：模拟提交非法 Payment Method
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bitcoin", "reference_number": "INVALID-PAYMENT"},
        follow_redirects=False,
    )

    # Step 3：验证 Flask 返回 Redirect
    assert response.status_code == 302

    # Step 4：验证 Flask 拒绝非法付款方式
    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("danger", "Invalid payment method.") in flash_messages

    # Step 5：验证 Claim Status 没有改变
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "approved"

        # 查询对应的 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 不允许创建任何 Payment Record
        assert len(payments) == 0


# ============================================================
# Test 5：Reference Number Length Validation
# ============================================================


def test_reference_number_too_long_is_rejected(client):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：生成超过 100 个字符的 Reference Number
    long_reference = "A" * 101

    # Step 3：发送付款请求
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": long_reference},
        follow_redirects=False,
    )

    # Step 4：验证 Flask 返回 Redirect
    assert response.status_code == 302

    # Step 5：验证 Flask 拒绝过长的 Reference Number
    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("danger", "Reference number is too long.") in flash_messages

    # Step 6：验证 Database 没有被修改
    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        # Claim 必须保持 Approved
        assert claim.status == "approved"

        # 查询 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 不应该创建任何 Payment Record
        assert len(payments) == 0


# ============================================================
# Test 6：Member Cannot Make Payment
# ============================================================


def test_member_cannot_make_payment(client):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

        # 查询测试用的 Member
        member = db.session.execute(
            db.select(User).where(User.club_id == "TEST-M001")
        ).scalar_one()

        # 保存 Member ID
        member_id = member.id

    # Step 2：将登录身份从 Treasurer 切换为 Member
    with test_client.session_transaction() as session:
        session["_user_id"] = str(member_id)
        session["_fresh"] = True

    # Step 3：模拟 Member 尝试付款
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={
            "payment_method": "Bank Transfer",
            "reference_number": "UNAUTHORIZED-PAYMENT",
        },
        follow_redirects=False,
    )

    # 查看 Member 请求实际返回的 HTTP Status Code
    print("Authorization Status:", response.status_code)

    # 查看 Member 被重定向到哪里
    print("Authorization Redirect:", response.headers.get("Location"))

    # 查看权限验证产生的 Flash Messages
    with test_client.session_transaction() as session:
        print("Authorization Flashes:", session.get("_flashes", []))

    # Step 4：验证请求被拒绝
    # Member 没有 Treasurer 权限，必须返回 403 Forbidden
    assert response.status_code == 403

    # 403 Forbidden 不应该重定向
    assert response.headers.get("Location") is None

    # Step 5：验证 Database 没有发生付款
    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        # Claim 必须保持 Approved
        assert claim.status == "approved"

        # 查询 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # Member 不应该能够创建 Payment
        assert len(payments) == 0


# ============================================================
# Test 7：Unauthenticated User Cannot Make Payment
# ============================================================


def test_unauthenticated_user_cannot_make_payment(client):

    # 取得 Fixture 提供的 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：清除 Test Client 的登录 Session
    with test_client.session_transaction() as session:
        session.clear()

    # Step 3：模拟未登录用户直接发送付款请求
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": "GUEST-PAYMENT"},
        follow_redirects=False,
    )

    # Step 4：验证未登录请求被重定向
    assert response.status_code == 302

    # Flask-Login 应该将用户重定向到登录页面
    assert "/login" in response.headers["Location"]

    # Step 5：确认 Database 没有发生付款
    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        # Claim 必须保持 Approved
        assert claim.status == "approved"

        # 查询对应的 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 未登录用户不应该创建 Payment
        assert len(payments) == 0


# ============================================================
# Test 8：Reference Number Maximum Valid Length
# ============================================================


def test_reference_number_100_characters_is_accepted(client):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：生成刚好 100 个字符的 Reference Number
    reference_number = "A" * 100

    # 确认长度刚好是 100
    assert len(reference_number) == 100

    # Step 3：提交付款请求
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Bank Transfer", "reference_number": reference_number},
        follow_redirects=False,
    )

    # Step 4：验证请求成功并返回 Redirect
    assert response.status_code == 302

    # 验证成功付款的 Flash Message
    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("success", "Claim marked as paid successfully.") in flash_messages

    # Step 5：验证 Database 中的实际结果
    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        # Claim 应该已经付款
        assert claim.status == "paid"

        # 查询 Payment Record
        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one()

        # Reference Number 必须完整保存
        assert payment.reference_number == reference_number

        # 确认数据库保存了全部 100 个字符
        assert len(payment.reference_number) == 100

        # 验证付款金额没有改变
        assert payment.amount == claim.amount


# ============================================================
# Test 9：Cash Payment Validation
# ============================================================


def test_cash_payment_can_be_completed(client):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：提交 Cash Payment
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={"payment_method": "Cash", "reference_number": ""},
        follow_redirects=False,
    )

    # Step 3：验证 Flask 返回 Redirect
    assert response.status_code == 302

    # 验证付款成功的 Flash Message
    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("success", "Claim marked as paid successfully.") in flash_messages

    # Step 4：检查 Database 中的 Claim
    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        # Claim Status 应该更新为 Paid
        assert claim.status == "paid"

        # 查询对应的 Payment Record
        payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one()

        # 验证付款方式
        assert payment.payment_method == "Cash"

        # 空 Reference Number 应该保存为 None
        assert payment.reference_number is None

        # 验证 Payment Amount
        assert payment.amount == claim.amount

        # 验证付款人员
        treasurer = db.session.execute(
            db.select(User).where(User.club_id == "TEST-T001")
        ).scalar_one()

        assert payment.paid_by_id == treasurer.id

        # 验证付款时间已记录
        assert payment.paid_at is not None


# ============================================================
# Test 10：Payment Database Integrity
# ============================================================


def test_payment_database_integrity(client):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：模拟 Treasurer 完成付款
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={
            "payment_method": "Bank Transfer",
            "reference_number": "INTEGRITY-TEST-001",
        },
        follow_redirects=False,
    )

    # 验证请求返回 Redirect
    assert response.status_code == 302

    # 验证付款成功
    with test_client.session_transaction() as session:
        flash_messages = session.get("_flashes", [])

        assert ("success", "Claim marked as paid successfully.") in flash_messages

    # Step 3：重新读取 Database
    with app.app_context():
        # 查询 Claim
        claim = db.session.get(Claim, claim_id)

        # 查询该 Claim 对应的所有 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # Step 4：验证 Claim Status
        assert claim.status == "paid"

        # Step 5：验证只有一笔 Payment
        assert len(payments) == 1

        # 取得唯一的 Payment Object
        payment = payments[0]

        # Step 6：验证 Foreign Key
        assert payment.claim_id == claim.id

        # Step 7：验证金额一致
        assert payment.amount == claim.amount

        # Step 8：验证付款方式和 Reference Number
        assert payment.payment_method == "Bank Transfer"
        assert payment.reference_number == "INTEGRITY-TEST-001"

        # Step 9：验证付款人员
        treasurer = db.session.execute(
            db.select(User).where(User.club_id == "TEST-T001")
        ).scalar_one()

        assert payment.paid_by_id == treasurer.id

        # Step 10：验证付款时间存在
        assert payment.paid_at is not None

        # Step 11：验证 Claim 的 ORM Relationship
        assert claim.payment is not None

        # Relationship 应指向同一笔 Payment
        assert claim.payment.id == payment.id


# ============================================================
# Test 11：Payment Transaction Rollback
# ============================================================


def test_payment_transaction_rollback(client, monkeypatch):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：保存原本的 commit 方法
    original_commit = db.session.commit

    # 定义一个模拟数据库提交失败的函数
    def simulate_commit_failure():
        raise SQLAlchemyError("Simulated database failure")

    # 临时替换 db.session.commit
    monkeypatch.setattr(db.session, "commit", simulate_commit_failure)

    try:
        # Step 3：模拟 Treasurer 提交付款请求
        response = test_client.post(
            f"/claims/review/{claim_id}/pay",
            data={
                "payment_method": "Bank Transfer",
                "reference_number": "ROLLBACK-TEST-001",
            },
            follow_redirects=False,
        )

        # Step 4：验证 Flask 返回 Redirect
        assert response.status_code == 302

        # 验证数据库错误对应的 Flash Message
        with test_client.session_transaction() as session:
            flash_messages = session.get("_flashes", [])

            assert ("danger", "Failed to save payment record.") in flash_messages

    finally:
        # Step 5：恢复原本的 commit 方法
        monkeypatch.setattr(db.session, "commit", original_commit)

    # Step 6：使用新的 Application Context 检查数据库
    with app.app_context():
        # 重新读取 Claim
        claim = db.session.get(Claim, claim_id)

        # Claim 不应该被标记为 Paid
        assert claim.status == "approved"

        # 查询对应的 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 不应该创建 Payment Record
        assert len(payments) == 0


# ============================================================
# Test 12：Payment IntegrityError Rollback
# ============================================================


def test_payment_integrity_error_rollback(client, monkeypatch):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：定义模拟 IntegrityError 的函数
    def simulate_integrity_error():
        raise IntegrityError(
            statement="INSERT INTO payments (...) VALUES (...)",
            params={},
            orig=Exception("Simulated UNIQUE constraint failure"),
        )

    # Step 3：临时替换 Session 的 commit 方法
    with monkeypatch.context() as patcher:
        patcher.setattr(db.session, "commit", simulate_integrity_error)

        # Step 4：模拟 Treasurer 提交付款请求
        response = test_client.post(
            f"/claims/review/{claim_id}/pay",
            data={
                "payment_method": "Bank Transfer",
                "reference_number": "INTEGRITY-ERROR-TEST",
            },
            follow_redirects=False,
        )

        # Step 5：验证请求返回 Redirect
        assert response.status_code == 302

        # Step 6：验证正确的错误提示
        with test_client.session_transaction() as session:
            flash_messages = session.get("_flashes", [])

            assert (
                "danger",
                "Payment could not be saved because of a database constraint. "
                "Please refresh and verify the claim's payment status.",
            ) in flash_messages

    # 离开 monkeypatch.context 后，原本的 commit 已恢复

    # Step 7：重新检查数据库
    with app.app_context():
        # 重新查询 Claim
        claim = db.session.get(Claim, claim_id)

        # Claim 应保持 Approved
        assert claim.status == "approved"

        # 查询对应的 Payment Records
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 不应该创建任何 Payment Record
        assert len(payments) == 0


# ============================================================
# Test 13：Real SQLite UNIQUE Constraint Testing
# ============================================================


def test_real_sqlite_unique_constraint(client):

    # 取得 Test Client 和 Claim ID
    test_client, claim_id = client

    # Step 1：将 Claim 修改为 Approved
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.status = "approved"
        db.session.commit()

    # Step 2：通过 Flask Route 完成第一次付款
    response = test_client.post(
        f"/claims/review/{claim_id}/pay",
        data={
            "payment_method": "Bank Transfer",
            "reference_number": "FIRST-PAYMENT-001",
        },
        follow_redirects=False,
    )

    # 验证第一次付款成功
    assert response.status_code == 302

    with app.app_context():
        # Step 3：查询第一次付款记录
        first_payment = db.session.execute(
            db.select(Payment).where(Payment.claim_id == claim_id)
        ).scalar_one()

        # 保存原始付款记录 ID
        first_payment_id = first_payment.id

        # Step 4：尝试直接插入第二笔 Payment
        # 注意：绕过 Flask Route，直接测试数据库约束
        duplicate_payment = Payment(
            claim_id=claim_id,
            amount=first_payment.amount,
            payment_method="Cash",
            reference_number="DUPLICATE-PAYMENT-002",
            paid_at=first_payment.paid_at,
            paid_by_id=first_payment.paid_by_id,
        )

        db.session.add(duplicate_payment)

        # Step 5：SQLite 应拒绝重复的 claim_id
        with pytest.raises(IntegrityError):
            db.session.commit()

        # Step 6：提交失败后必须 Rollback
        db.session.rollback()

        # Step 7：重新查询数据库
        payments = (
            db.session.execute(db.select(Payment).where(Payment.claim_id == claim_id))
            .scalars()
            .all()
        )

        # 数据库只能存在一笔付款记录
        assert len(payments) == 1

        # 原本的 Payment Record 不能被覆盖
        assert payments[0].id == first_payment_id

        # 原本的 Reference Number 不能改变
        assert payments[0].reference_number == "FIRST-PAYMENT-001"

        # Step 8：验证 Claim 仍然是 Paid
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "paid"


# ============================================================
# Test Entry Point
# ============================================================

# 使用 pytest 运行测试，不需要手动调用函数
