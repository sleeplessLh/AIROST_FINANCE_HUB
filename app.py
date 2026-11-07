from flask import Flask, abort, render_template, redirect, url_for, request

from flask_login import LoginManager, login_required, current_user

from database import db

from models import User, Event, Claim, Budget, Receipt, AuditLog

from routes.auth import auth_bp

from routes.claims import claims_bp

from routes.admin import admin_bp

import os

from utils.decorators import role_required

from flask_wtf.csrf import CSRFProtect

from utils.dashboard import calculate_treasurer_statistics

from utils.budget import calculate_event_budget

from decimal import Decimal

from sqlalchemy.orm import selectinload


# 1. 创建 Flask Application Object
app = Flask(__name__)

# 创建 CSRF Protection Instance
csrf = CSRFProtect()

# 将 CSRF Protection 注册到 Flask Application
# 注册后，Jinja2 Template 才能使用 csrf_token()
csrf.init_app(app)

# 将 Authentication Routes 注册到 Flask Application
app.register_blueprint(auth_bp)

# 注册 Claims Blueprint
app.register_blueprint(claims_bp)

app.register_blueprint(admin_bp)

# 用于签名 Flask Session Cookie
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", 
    "dev-airost-change-me"
    )

# 限制整个 HTTP Request Body 的大小
# 6 MB：允许 5 MB Receipt + Multipart Form 的额外数据
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

# 2. 配置 SQLite Database

# ============================================================
# Database Configuration
# ============================================================
# app.config：Flask Application 的配置 Dictionary
# "SQLALCHEMY_DATABASE_URI"：Flask-SQLAlchemy 用来指定 Database 地址的配置项
# os.environ：当前 Python Process 能读取的环境变量
# .get()：根据名称查找环境变量
# 第一个参数：要查找的变量名称
# 第二个参数：找不到变量时使用的默认值
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///airost.db"
)

# 3. 关闭 SQLAlchemy 修改追踪功能
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 4. 将 SQLAlchemy 与 Flask Application 绑定
db.init_app(app)

# 创建 LoginManager，并绑定 Flask Application
login_manager = LoginManager(app)

# 未登录用户访问受保护页面时，跳转到 login Route
login_manager.login_view = "auth.login"

# 6. 创建 Application Context
with app.app_context():
    # 根据已经注册的 Models 创建不存在的数据库表
    db.create_all()

# 403：用户没有权限访问指定资源
@app.errorhandler(403)
def forbidden_error(error):
    return render_template(
        "error.html",
        error_code = 403,
        error_title="Access Forbidden",
        error_message = "You do not have permission to access this page."
    ), 403

# 404：请求的 URL 不存在
@app.errorhandler(404)
def page_not_found(error):
    return render_template(
        "error.html",
        error_code=404,
        error_title="Page Not Found",
        error_message="The page you are looking for does not exist.",
    ), 404

# 500：处理服务器内部错误
@app.errorhandler(500)
def internal_server_error(error):

    # 取得原始 Exception
    original_error = getattr(error, "original_exception", None)

    # 记录服务器内部错误
    if original_error is not None:
        app.logger.error(
            "Internal Server Error",
            exc_info=(
                type(original_error),
                original_error,
                original_error.__traceback__,
            ),
        )
    else:
        app.logger.error("HTTP 500 error: %s", error)

    # 尝试回滚数据库事务
    try:
        db.session.rollback()
    except Exception:
        # Rollback 自己失败时也要记录
        app.logger.exception("Database rollback failed")

    # 尝试返回统一的 Error Template
    try:
        return render_template(
            "error.html",
            error_code=500,
            error_title="Internal Server Error",
            error_message="An unexpected error occurred. Please try again later.",
        ), 500

    except Exception:
        # Error Template 自己发生异常时，返回简单的 Fallback Response
        app.logger.exception("Failed to render 500 error page")

        return "500 Internal Server Error", 500

@login_manager.user_loader
# 它将下面的函数注册为 User Loader Callback。
# 当 Flask-Login 需要恢复当前登录用户时，就会调用这个函数。
def load_user(user_id):
    return db.session.get(User, int(user_id))


# 7. Home Route
@app.route("/")
@login_required
def home():
    return redirect(url_for("dashboard"))


# ============================================================
# Admin Test Route
# ============================================================
# 注册 /admin URL
@app.route("/admin")
# 检查用户是否已登录
@login_required
# 检查当前用户是否拥有 admin Role
@role_required("admin")
def admin():
    # 只有 admin 可以执行这个 Function
    return "Welcome to Admin Dashboard"


# ============================================================
# Treasurer Test Route
# ============================================================
# 注册 /treasurer URL
@app.route("/treasurer")
# 检查用户是否已登录
@login_required
# 检查当前用户是否拥有 treasurer Role
@role_required("treasurer")
def treasurer():
    # 只有 treasurer 可以执行这个 Function
    return "Welcome to Treasurer Dashboard"


# ============================================================
# Dashboard Route
# 根据当前登录用户的 Role 返回对应 Dashboard
# ============================================================


# 注册 /dashboard Route
@app.route("/dashboard")
# 只有已登录用户才能访问 Dashboard
@login_required
def dashboard():

    # 获取当前登录用户的 Role
    role = current_user.role

    # Member：进入 Member Dashboard
    if role == "member":
        return render_template("member_dashboard.html")

    # Treasurer：进入 Treasurer Dashboard
    elif role == "treasurer":
        # 计算整个系统的 Budget 和 Claim Statistics
        stats = calculate_treasurer_statistics()

        # ============================================================
        # Event Budget Overview Data
        # ============================================================

        # 查询 Database 中的全部 Event Records
        # db.select(Event)：建立 SELECT Query
        # scalars().all()：取得 Event ORM Objects 的 List
        events = db.session.execute(
            db.select(Event).order_by(Event.id)
        ).scalars().all()

        # 创建 List，保存每个 Event 的预算数据
        event_budget_rows = []

        # 遍历全部 Event Objects
        for event in events:

            # 计算当前 Event 的预算统计
            # 返回 Dictionary：allocated、committed、remaining 等
            budget_info = calculate_event_budget(event)

            # 取得预算使用百分比
            utilization = budget_info["utilization_percentage"]

            # 根据预算使用率判断风险等级
            if budget_info["remaining"] < 0:
                risk_level = "over_budget"

            elif budget_info["allocated"] == 0:
                risk_level = "unallocated"

            elif utilization >= 90:
                risk_level = "critical"

            elif utilization >= 75:
                risk_level = "warning"

            else:
                risk_level = "healthy"

            # 将当前 Event 的数据加入 List
            event_budget_rows.append({
                "event": event,
                "budget": budget_info,
                "risk_level": risk_level
            })


        # ============================================================
        # Pending Claims & Financial Risk Summary
        # ============================================================

        # 查询所有 Status 为 pending 的 Claim
        # order_by(Claim.created_at.desc())：最新提交的 Claim 排在前面
        # scalars().all()：取得 Claim ORM Objects 的 List
        pending_claims = db.session.execute(
            db.select(Claim)
            .where(Claim.status == "pending")
            .order_by(Claim.created_at.desc())
        ).scalars().all()

        # 计算所有 Pending Claims 的申请总金额
        # Decimal("0.00")：使用 Decimal 作为初始值，避免使用 Float
        pending_amount = sum(
            (claim.amount for claim in pending_claims),
            Decimal("0.00")
        )

        # 创建 List，保存每笔 Pending Claim 的预算风险信息
        pending_claim_rows = []

        # 用 Dictionary 缓存每个 Event 的预算结果
        # 避免同一个 Event 的多笔 Claim 重复计算 Budget
        event_budget_cache = {}

        # 统计超过当前剩余预算的 Pending Claims 数量
        at_risk_count = 0

        # 遍历全部 Pending Claims
        for claim in pending_claims:

            # 获取当前 Claim 所属的 Event ID
            event_id = claim.event_id

            # 如果尚未计算过这个 Event 的 Budget
            if event_id not in event_budget_cache:

                # 计算该 Event 的 Allocated、Committed、Remaining
                event_budget_cache[event_id] = calculate_event_budget(
                    claim.event
                )

            # 从缓存中获取 Event Budget Information
            budget_info = event_budget_cache[event_id]

            # 判断当前 Claim 是否超过 Event Remaining Budget
            # 返回值：bool，True 表示当前 Claim 单独审批时预算不足
            is_over_budget = claim.amount > budget_info["remaining"]

            # 如果存在预算风险，则增加计数
            if is_over_budget:
                at_risk_count += 1

            # 保存当前 Pending Claim 的预算风险信息
            pending_claim_rows.append({
                "claim": claim,                 # 当前 Claim ORM Object
                "budget": budget_info,         # 所属 Event 的预算信息
                "is_over_budget": is_over_budget  # 是否超过剩余预算
            })

            # 将统计结果传递给 Treasurer Dashboard Template
        return render_template(
            "treasurer_dashboard.html",
            stats=stats,
            event_budget_rows=event_budget_rows,
            pending_claim_rows=pending_claim_rows,
            pending_amount=pending_amount,
            pending_count=len(pending_claims),
            at_risk_count=at_risk_count
        )

    # Admin：进入 Admin Dashboard
    elif role == "admin":
        return render_template("admin_dashboard.html")

    # 未知 Role：拒绝访问
    else:
        abort(403)


# ============================================================
# Admin Audit Log Viewer
# 支持 Action Filtering、User Filtering 和 Pagination
# ============================================================
@app.route("/admin/audit-logs", methods=["GET"])
@login_required
@role_required("admin")
def admin_audit_logs():

    # 从 URL Query Parameters 读取 Action Filter
    # strip()：移除前后空格
    action_filter = request.args.get("action", "").strip()

    # 从 URL 读取 User ID Filter
    user_filter = request.args.get("user_id", "").strip()

    # 读取 Page Number，默认第一页
    # type=int：尝试转换为 Integer，转换失败则使用默认值
    page = request.args.get("page", 1, type=int)

    # 防止 Page Number 小于 1
    page = max(page, 1)

    # 每页显示 10 条 Audit Logs
    per_page = 10

    # 创建基础 SELECT Statement
    statement = db.select(AuditLog).options(
        selectinload(AuditLog.user)
    )

    # 如果 Admin 指定 Action，则加入过滤条件
    if action_filter:
        statement = statement.where(AuditLog.action == action_filter)

    # 如果 User ID 是有效的正整数，则进行过滤
    if user_filter:
        if user_filter.isdecimal() and int(user_filter) > 0:
            statement = statement.where(AuditLog.user_id == int(user_filter))
        else:
            # 无效 User ID 不应返回其他用户的日志
            statement = statement.where(AuditLog.user_id == -1)

    # 按创建时间和 ID 降序排列
    statement = statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())

    # 使用 Flask-SQLAlchemy Pagination
    # 是这句话让查询语句真正开始执行
    pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

    # 当前页面的 AuditLog ORM Objects
    logs = pagination.items

    # 查询数据库中现有的 Action Types
    # distinct()：去除重复值
    actions = (
        db.session.execute(
            db.select(AuditLog.action).distinct().order_by(AuditLog.action)
        )
        .scalars()
        .all()
    )

    # 将数据传递给 Jinja2 Template
    return render_template(
        "admin_audit_logs.html",
        logs=logs,
        pagination=pagination,
        actions=actions,
        action_filter=action_filter,
        user_filter=user_filter,
    )

