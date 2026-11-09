from datetime import datetime, timezone
from database import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


# 返回当前 UTC 时间，供数据库记录创建时间
def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# User Model：对应数据库中的用户表
class User(UserMixin, db.Model):
    # UserMixin：提供登录认证相关接口。
    # db.Model：提供 ORM 数据库映射功能
    id = db.Column(db.Integer, primary_key=True)

    # 用户编号，例如 AIR-M001
    club_id = db.Column(db.String(30), unique=True, nullable=False, index=True)

    # 用户基本信息
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # 用户角色：member / treasurer / admin
    role = db.Column(db.String(20), nullable=False, default="member")

    # 账户是否启用
    active = db.Column(db.Boolean, default=True)

    # 用户银行资料
    bank_name = db.Column(db.String(100), default="")
    bank_account_name = db.Column(db.String(100), default="")
    bank_account_number = db.Column(db.String(60), default="")

    # 用户创建时间
    created_at = db.Column(db.DateTime, default=utc_now)

    # 一个 User 可以提交多个 Claims
    claims = db.relationship("Claim", back_populates="member")  # 就是在创建foreign key

    # 将用户输入的密码转换成 Hash，并保存到当前 User Object
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    # 验证输入密码是否与数据库保存的 Hash 匹配
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # 告诉 Flask-Login 当前账户是否启用
    @property
    def is_active(self):
        return self.active


# ============================================================
# Event Model
# 储存社团活动的信息
# ============================================================


class Event(db.Model):
    # 指定 Database Table Name
    __tablename__ = "events"

    # Event 的 Primary Key
    id = db.Column(db.Integer, primary_key=True)

    # Event Name
    name = db.Column(db.String(150), nullable=False)

    # Event Description
    description = db.Column(db.Text, nullable=True)

    # Event Date
    event_date = db.Column(db.Date, nullable=True)

    # Record Creation Time
    created_at = db.Column(db.DateTime, default=utc_now)

    claims = db.relationship("Claim", back_populates="event")

    # budget：通过 event.budget 访问该 Event 对应的 Budget Object
    # "Budget"：指定关联的 Model Class
    # back_populates="event"：对应 Budget Class 中的 event Relationship Attribute
    # uselist=False：将 Relationship 配置为单个 Object，而不是 Object List
    budget = db.relationship("Budget", back_populates="event", uselist=False)


# class User(db.Model):
#     # claims：通过 user.claims 访问该 User 提交的所有 Claim Objects
#     # "Claim"：指定关联的 Model Class
#     # back_populates="member"：对应 Claim Class 中的 member Relationship Attribute
#     # 告诉 SQLAlchemy：User.claims 和 Claim.member 是同一关系的两个方向
#     claims = db.relationship("Claim", back_populates="member")


# class Claim(db.Model):
#     # member_id：真正储存在 Database 中的 Foreign Key
#     # db.ForeignKey("user.id")：将 Claim.member_id 关联到 User.id
#     member_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

#     # member：通过 claim.member 访问提交该 Claim 的 User Object
#     # "User"：指定关联的 Model Class
#     # back_populates="claims"：对应 User Class 中的 claims Relationship Attribute
#     # 告诉 SQLAlchemy：Claim.member 和 User.claims 是同一关系的两个方向
#     # 当修改其中一个 Relationship 时，SQLAlchemy 会同步另一方向的 ORM 关联状态
#     member = db.relationship("User", back_populates="claims")

# ============================================================
# Claim Model
# 储存 Member 提交的报销申请
# ============================================================


class Claim(db.Model):
    # 指定 Database Table Name
    __tablename__ = "claims"

    # Claim Primary Key
    id = db.Column(db.Integer, primary_key=True)

    # 报销申请标题
    title = db.Column(db.String(150), nullable=False)

    # 报销申请描述
    description = db.Column(db.Text, nullable=True)

    # 报销金额
    amount = db.Column(db.Numeric(10, 2), nullable=False)

    # Claim Status：默认 pending
    status = db.Column(db.String(20), nullable=False, default="draft")

    # 提交 Claim 的 User ID
    member_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Claim 所属的 Event ID
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)

    # Claim 创建时间
    created_at = db.Column(db.DateTime, default=utc_now)

    # Claim 对应的 User Object
    member = db.relationship("User", back_populates="claims")

    # Claim 对应的 Event Object
    event = db.relationship("Event", back_populates="claims")

    # receipts：通过 claim.receipts 访问该 Claim 的所有 Receipt Objects
    # "Receipt"：指定关联的 ORM Model
    # back_populates="claim"：对应 Receipt Class 中的 claim Relationship Attribute
    # cascade="all, delete-orphan"：删除 Claim 时，同时删除关联的 Receipt 数据库记录
    # 注意：不会自动删除磁盘上的实际文件
    receipts = db.relationship(
        "Receipt", back_populates="claim", cascade="all, delete-orphan"
    )

    # payment：取得当前 Claim 对应的 Payment ORM Object
    # uselist=False：返回单个 Payment Object，而不是 List
    # 如果尚未付款，则返回 None
    # back_populates：与 Payment.claim 建立双向 Relationship
    payment = db.relationship(
        "Payment", back_populates="claim", uselist=False, cascade="all, delete-orphan"
    )


# ============================================================
# Budget Model
# 储存每场 Event 的预算金额
# ============================================================


class Budget(db.Model):
    # 指定 Database Table Name
    __tablename__ = "budgets"

    # Budget Primary Key
    id = db.Column(db.Integer, primary_key=True)

    # Event Foreign Key：关联 events Table 的 id Column
    # unique=True：同一个 Event ID 最多只能出现在一条 Budget Record 中
    # nullable=False：每个 Budget 都必须关联一个 Event
    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id"), unique=True, nullable=False
    )

    # 活动获得的总预算
    # Numeric(10, 2)：最多 10 位数字，其中 2 位为小数
    allocated_amount = db.Column(db.Numeric(10, 2), nullable=False)

    # Budget 创建时间
    created_at = db.Column(db.DateTime, default=utc_now)

    # event：通过 budget.event 访问对应的 Event Object
    # "Event"：指定关联的 Model Class
    # back_populates="budget"：对应 Event Class 中的 budget Relationship Attribute
    event = db.relationship("Event", back_populates="budget")


# ============================================================
# Receipt Model：储存 Claim 的收据文件信息
# ============================================================
class Receipt(db.Model):
    # 指定 Database Table Name
    __tablename__ = "receipts"

    # Receipt Primary Key
    id = db.Column(db.Integer, primary_key=True)

    # Foreign Key：关联 Claim Table
    # nullable=False：每份 Receipt 必须属于一条 Claim
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=False)

    # original_filename：用户上传时的原始文件名称
    original_filename = db.Column(db.String(255), nullable=False)

    # stored_filename：Server 储存文件时使用的唯一名称
    stored_filename = db.Column(db.String(255), unique=True, nullable=False)

    # uploaded_at：记录文件上传时间
    uploaded_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    # claim：通过 receipt.claim 访问关联的 Claim Object
    # back_populates="receipts"：对应 Claim.receipts
    claim = db.relationship("Claim", back_populates="receipts")


# ============================================================
# Payment Model
# 功能：保存 Claim 的付款记录
# ============================================================
class Payment(db.Model):
    # 指定 SQLite Database Table Name
    __tablename__ = "payments"

    # Payment ID：Primary Key
    id = db.Column(db.Integer, primary_key=True)

    # Claim ID：Foreign Key，关联 claims.id
    # unique=True：一个 Claim 最多只能对应一条 Payment Record
    # nullable=False：每条 Payment 必须关联一个 Claim
    claim_id = db.Column(
        db.Integer, db.ForeignKey("claims.id"), unique=True, nullable=False
    )

    # Payment Amount：实际付款金额，保留两位小数
    amount = db.Column(db.Numeric(10, 2), nullable=False)

    # Payment Method：例如 Bank Transfer、Cash
    payment_method = db.Column(db.String(30), nullable=False)

    # Reference Number：银行交易参考编号
    reference_number = db.Column(db.String(100), nullable=True)

    # Payment Date：记录实际付款时间
    paid_at = db.Column(db.DateTime, nullable=False)

    # Paid By：记录确认付款的 Treasurer User ID
    paid_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Relationship：取得当前 Payment 对应的 Claim
    claim = db.relationship("Claim", back_populates="payment")

    # Relationship：取得确认付款的 Treasurer User
    paid_by = db.relationship("User", foreign_keys=[paid_by_id])


# ============================================================
# AuditLog Model
# 记录系统中的重要操作，例如批准 Claim、修改 Budget
# ============================================================
class AuditLog(db.Model):
    # 指定 Database Table Name
    __tablename__ = "audit_logs"

    # Audit Log Primary Key
    id = db.Column(db.Integer, primary_key=True)

    # 执行操作的 User ID
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # 操作类型，例如 approve_claim、update_budget
    action = db.Column(db.String(100), nullable=False)

    # 被操作的资源类型，例如 Claim、Budget、User
    target_type = db.Column(db.String(50), nullable=False)

    # 被操作记录的 ID
    target_id = db.Column(db.Integer, nullable=False)

    # 操作的附加说明
    details = db.Column(db.Text, nullable=True)

    # 操作发生的 UTC 时间
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    # 通过 audit_log.user 访问执行操作的 User Object
    user = db.relationship("User")