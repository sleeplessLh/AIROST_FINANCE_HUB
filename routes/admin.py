from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from database import db
from models import User, Event, Claim, Payment, AuditLog, PasswordReset, utc_now
from utils.decorators import role_required
from werkzeug.security import generate_password_hash
from sqlalchemy.exc import IntegrityError
from datetime import date
from utils.audit import record_audit_log

# 创建 Admin Blueprint，所有路由统一以 /admin 开头
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# Admin Dashboard：显示用户和活动的统计信息
@admin_bp.route("/")
@login_required
@role_required("admin")
def dashboard():
    # 查询数据库中的用户总数
    total_users = db.session.query(User).count()

    # 查询数据库中的活动总数
    total_events = db.session.query(Event).count()

    # 查询 Active 用户总数
    active_users = db.session.query(User).filter(User.active.is_(True)).count()

    # 查询 Inactive 用户总数
    inactive_users = db.session.query(User).filter(User.active.is_(False)).count()

    # 将统计结果传递给 Jinja2 Template
    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        total_events=total_events,
        active_users=active_users,
        inactive_users=inactive_users,
    )


# User Management：显示数据库中的所有用户
@admin_bp.route("/users")
@login_required
@role_required("admin")
def list_users():
    # 创建 SELECT 查询，按照用户 ID 升序排列
    statement = db.select(User).order_by(User.id.asc())
    # 执行查询，并将所有 User 对象转换为列表
    users = db.session.scalars(statement).all()
    # 将 users 传递给 Jinja2 模板
    return render_template("admin/users.html", users=users)



# 创建新用户
@admin_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_user():
    # GET：显示创建用户的表单
    if request.method == "GET":
        return render_template("admin/create_user.html", form={})

    # POST：接收用户提交的表单数据
    club_id = request.form.get("club_id", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "")

    # 出错时：保留已输入的资料，重新显示表单（密码除外）
    # 400：告诉浏览器和测试"这次提交的资料有问题"
    def show_form_with_error(message):
        flash(message, "danger")
        return render_template("admin/create_user.html", form=request.form), 400

    # 验证必填字段
    if not club_id or not name or not email or not password:
        return show_form_with_error("All fields are required.")

    # 验证角色是否合法
    if role not in {"member", "treasurer", "admin"}:
        return show_form_with_error("Invalid user role.")

    if len(password) < 8:
        return show_form_with_error("Password must contain at least 8 characters.")
    
    # 创建 User ORM 对象
    user = User(
        club_id=club_id,
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
        active=True,
    )

    # 将用户加入数据库 Session
    db.session.add(user)

    try:
        # flush()：先把 User 送进数据库取得 user.id，但还不 commit
        # Club ID 重复时，IntegrityError 会在这里就抛出
        db.session.flush()

        # 记录 Admin 建立用户的操作（不记录密码）
        record_audit_log(
            user_id=current_user.id,
            action="create_user",
            target_type="User",
            target_id=user.id,
            details=f"Club ID: {club_id}; Role: {role}",
        )
        # User 和 Audit Log 一起写入数据库
        db.session.commit()

    except IntegrityError:
        # Club ID 或 Email 重复时，撤销本次事务
        db.session.rollback()
        return show_form_with_error("Club ID or email already exists.")

    flash("User created successfully.", "success")
    return redirect(url_for("admin.list_users"))



# 编辑现有用户
@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_user(user_id):
    # 根据主键查询用户，不存在则返回 404
    user = db.get_or_404(User, user_id)

    # GET：显示编辑表单，并传入当前用户资料
    if request.method == "GET":
        return render_template("admin/edit_user.html", user=user)

    # POST：获取表单提交的新资料
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "")
    active = request.form.get("active") == "on"

    # 验证必填字段
    if not name or not email:
        flash("Name and email are required.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    # 验证角色
    if role not in {"member", "treasurer", "admin"}:
        flash("Invalid user role.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    # 防止当前 Admin 禁用自己的账户或移除自己的 Admin 角色
    if user.id == current_user.id and (not active or role != "admin"):
        flash("You cannot deactivate or demote your own account.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))
    
    # 修改之前，先比对哪些栏位有变动
    # 角色和启用状态是权限相关的变更，记录"从什么改成什么"
    changes = []
    if user.name != name:
        changes.append("name")
    if user.email != email:
        changes.append("email")
    if user.role != role:
        changes.append(f"role: {user.role} -> {role}")
    if user.active != active:
        changes.append(f"active: {user.active} -> {active}")

    # 修改 User ORM 对象的属性
    user.name = name
    user.email = email
    user.role = role
    user.active = active

    # 有变动才记录
    if changes:
        record_audit_log(
            user_id=current_user.id,
            action="update_user",
            target_type="User",
            target_id=user.id,
            details="; ".join(changes),
        )

    try:
        # 将修改保存到数据库
        db.session.commit()
    except IntegrityError:
        # Email 与其他用户重复时撤销事务
        db.session.rollback()
        flash("Email already exists.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    flash("User updated successfully.", "success")
    return redirect(url_for("admin.list_users"))


# ============================================================
# 重设用户密码（用户忘记密码时使用）
# POST /admin/users/<user_id>/reset-password
# - Admin 设定一组临时密码，当面交给用户
# - 用户用临时密码登入后，必须先设定新密码（见 app.py 的 require_password_change）
# - 临时密码不会出现在 Flash 讯息或 Audit Log 里
# ============================================================
@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@role_required("admin")
def reset_user_password(user_id):

    user = db.get_or_404(User, user_id)

    # Admin 自己的密码请到 My Profile 修改（需要输入目前密码，比较安全）
    if user.id == current_user.id:
        flash("Use My Profile to change your own password.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    # 密码不使用 strip()：空格也可能是密码的一部分
    temporary_password = request.form.get("temporary_password", "")

    if len(temporary_password) < 8:
        flash("Temporary password must contain at least 8 characters.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    user.set_password(temporary_password)

    # 建立（或更新）待处理的重设记录：用户下次登入必须设定新密码
    if user.pending_password_reset is None:
        user.pending_password_reset = PasswordReset()
    else:
        user.pending_password_reset.created_at = utc_now()

    # user_id 记录执行的 Admin；不记录临时密码本身
    record_audit_log(
        user_id=current_user.id,
        action="reset_password",
        target_type="User",
        target_id=user.id,
        details=f"Temporary password set for {user.club_id}; must change at next sign-in.",
    )

    db.session.commit()

    flash(
        f"Temporary password set for {user.club_id}. Give it to them in person; "
        "they must choose a new password after signing in.",
        "success",
    )
    return redirect(url_for("admin.edit_user", user_id=user.id))


# Event Management：显示所有活动
@admin_bp.route("/events")
@login_required
@role_required("admin")
def list_events():
    # 构建 SELECT 查询，并按照活动 ID 升序排列
    statement = db.select(Event).order_by(Event.id.asc())
    # 执行查询，取得所有 Event ORM 对象
    events = db.session.scalars(statement).all()
    # 将活动列表传递给 HTML 模板
    return render_template("admin/events.html", events=events)



# Create Event：显示创建表单并处理提交
@admin_bp.route("/events/create", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_event():
    # GET：显示创建活动的 HTML 表单
    if request.method == "GET":
        return render_template("admin/create_event.html")

    # POST：读取 HTML 表单数据
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    event_date_text = request.form.get("event_date", "").strip()

    # 验证活动名称
    if not name:
        flash("Event name is required.", "danger")
        return redirect(url_for("admin.create_event"))

    # 验证名称长度，符合 Event.name 的 String(150)
    if len(name) > 150:
        flash("Event name must not exceed 150 characters.", "danger")
        return redirect(url_for("admin.create_event"))

    # 将日期字符串转换成 Python date 对象
    event_date = None

    if event_date_text:
        try:
            event_date = date.fromisoformat(event_date_text)
        except ValueError:
            flash("Invalid event date.", "danger")
            return redirect(url_for("admin.create_event"))

    # 创建 Event ORM 对象
    event = Event(name=name, description=description or None, event_date=event_date)

    # 将 Event 加入当前数据库 Session
    db.session.add(event)

    # flush()：取得 event.id，供 Audit Log 使用
    db.session.flush()

    record_audit_log(
        user_id=current_user.id,
        action="create_event",
        target_type="Event",
        target_id=event.id,
        details=f"Name: {name}",
    )

    # Event 和 Audit Log 一起写入数据库
    db.session.commit()

    flash("Event created successfully.", "success")
    return redirect(url_for("admin.list_events"))


# Edit Event：编辑现有活动
@admin_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_event(event_id):
    # 根据 Primary Key 查询 Event，不存在则返回 404
    event = db.get_or_404(Event, event_id)

    # GET：显示编辑页面，并传入当前 Event 对象
    if request.method == "GET":
        return render_template("admin/edit_event.html", event=event)

    # POST：获取 HTML 表单提交的数据
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    event_date_text = request.form.get("event_date", "").strip()

    # 验证 Event Name
    if not name:
        flash("Event name is required.", "danger")
        return redirect(url_for("admin.edit_event", event_id=event.id))

    # 验证 Event Name 长度
    if len(name) > 150:
        flash("Event name must not exceed 150 characters.", "danger")
        return redirect(url_for("admin.edit_event", event_id=event.id))

    # 将日期字符串转换成 Python date 对象
    event_date = None

    if event_date_text:
        try:
            event_date = date.fromisoformat(event_date_text)
        except ValueError:
            flash("Invalid event date.", "danger")
            return redirect(url_for("admin.edit_event", event_id=event.id))

    # 修改之前，先比对哪些栏位有变动
    changes = []
    if event.name != name:
        changes.append("name")
    if event.description != (description or None):
        changes.append("description")
    if event.event_date != event_date:
        changes.append("event_date")

    # 更新 ORM 对象的属性
    event.name = name
    event.description = description or None
    event.event_date = event_date

    if changes:
        record_audit_log(
            user_id=current_user.id,
            action="update_event",
            target_type="Event",
            target_id=event.id,
            details="Updated fields: " + ", ".join(changes),
        )

    # Event 和 Audit Log 一起写入数据库
    db.session.commit()

    flash("Event updated successfully.", "success")
    return redirect(url_for("admin.list_events"))


# ============================================================
# 删除用户
# POST /admin/users/<user_id>/delete
# 只允许删除"没有任何报销、付款或审计记录"的账号（例如建错的账号）
# 有记录的账号必须保留，请改用停用（Inactive）
# ============================================================
@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_user(user_id):

    # 根据主键查询用户，不存在则返回 404
    user = db.get_or_404(User, user_id)

    # 1. 不能删除自己的账号
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin.list_users"))

    # 2. 检查是否有任何关联记录
    #    .limit(1)：只要找到一笔就够了，不需要全部读出来
    #    scalar()：找不到时返回 None
    has_claims = (
        db.session.scalar(
            db.select(Claim.id).where(Claim.member_id == user.id).limit(1)
        )
        is not None
    )

    has_payments = (
        db.session.scalar(
            db.select(Payment.id).where(Payment.paid_by_id == user.id).limit(1)
        )
        is not None
    )

    has_audit_logs = (
        db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.user_id == user.id).limit(1)
        )
        is not None
    )

    if has_claims or has_payments or has_audit_logs:
        flash(
            f"{user.club_id} has claims, payments or activity history and cannot be deleted. "
            "Deactivate the account instead.",
            "warning",
        )
        return redirect(url_for("admin.list_users"))

    # 3. 删除之前先记下资料，删除后就读不到了
    club_id = user.club_id
    details = f"Club ID: {user.club_id}; Name: {user.name}; Role: {user.role}"

    # 删除 User；User.profile 设定了 cascade，UserProfile 会一起删除
    db.session.delete(user)

    # 记录 Admin 删除用户的操作
    record_audit_log(
        user_id=current_user.id,
        action="delete_user",
        target_type="User",
        target_id=user_id,
        details=details,
    )

    # 删除和 Audit Log 一起写入数据库
    db.session.commit()

    flash(f"User {club_id} deleted successfully.", "success")
    return redirect(url_for("admin.list_users"))



    