from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from database import db
from models import User, Event
from utils.decorators import role_required
from werkzeug.security import generate_password_hash
from sqlalchemy.exc import IntegrityError
from datetime import date

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
        return render_template("admin/create_user.html")

    # POST：接收用户提交的表单数据
    club_id = request.form.get("club_id", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "")

    # 验证必填字段
    if not club_id or not name or not email or not password:
        flash("All fields are required.", "danger")
        return redirect(url_for("admin.create_user"))

    # 验证角色是否合法
    if role not in {"member", "treasurer", "admin"}:
        flash("Invalid user role.", "danger")
        return redirect(url_for("admin.create_user"))

    if len(password) < 8:
        flash("Password must contain at least 8 characters.", "danger")
        return redirect(url_for("admin.create_user"))

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
        # 将新用户保存到数据库
        db.session.commit()
    except IntegrityError:
        # Club ID 或 Email 重复时，撤销本次事务
        db.session.rollback()
        flash("Club ID or email already exists.", "danger")
        return redirect(url_for("admin.create_user"))

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

    # 修改 User ORM 对象的属性
    user.name = name
    user.email = email
    user.role = role
    user.active = active

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

    # 将新活动写入数据库
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

    # 更新 ORM 对象的属性
    event.name = name
    event.description = description or None
    event.event_date = event_date

    # 提交数据库事务
    db.session.commit()

    flash("Event updated successfully.", "success")
    return redirect(url_for("admin.list_events"))

