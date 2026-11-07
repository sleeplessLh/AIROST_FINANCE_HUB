# Blueprint：将相关 Routes 组织成独立模块
# render_template：渲染 HTML
# request：读取浏览器提交的数据
# redirect：返回重定向响应
# url_for：根据 Endpoint 名称生成 URL
# flash：保存要在下一个页面显示的提示信息
from flask import Blueprint, render_template, request, redirect, url_for, flash

# current_user：获取当前登录用户
# login_user：建立用户登录状态
# logout_user：清除用户登录状态
# login_required：限制只有已登录用户才能访问指定 Route
from flask_login import current_user, login_user, logout_user, login_required

# func：用于 SQLAlchemy 的 SQL 函数
from sqlalchemy import func

# 导入数据库中的 User Model
from models import User

from database import db


auth_bp = Blueprint("auth", __name__)


# GET /login：显示登录页面
# POST /login：处理用户提交的登录表单
@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    # 如果用户已经登录，就不需要重复登录
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    # 如果浏览器提交了登录表单
    if request.method == "POST":
        # 从 HTML Form 中取得 AIROST ID，并去除空格、统一大小写
        club_id = request.form["club_id"].strip().lower()

        # 从 HTML Form 中取得用户输入的密码
        password = request.form["password"]

        # 根据 AIROST ID 查询数据库中的用户
        user = db.session.execute(
            db.select(User).where(func.lower(User.club_id) == club_id)
        ).scalar_one_or_none()

        # 用户存在、账户启用、密码正确，才允许登录
        if user and user.active and user.check_password(password):
            # 建立登录状态；remember 由 Checkbox 决定
            login_user(user, remember=bool(request.form.get("remember")))

            # 登录成功后返回首页（当前阶段作为 Dashboard 占位页面）
            return redirect(url_for("dashboard"))

        # 登录失败时显示错误信息
        flash("Invalid AIROST ID or password.", "danger")

    # GET 请求，或者 POST 登录失败时，显示登录页面
    return render_template("login.html")


# 只允许 POST Request 执行 Logout
@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    # 清除当前用户的登录状态
    logout_user()
    # 返回 Login Page
    return redirect(url_for("auth.login"))
