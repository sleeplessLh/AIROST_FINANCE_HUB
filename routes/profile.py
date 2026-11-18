# ============================================================
# Profile Blueprint
# 个人资料页面：查看 / 修改电话与银行资料、修改密码
# 所有已登录用户（member / treasurer / admin）都可以使用
# ============================================================

# re：正则表达式（Regular Expression），用来检查电话、户口号码的格式
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from database import db
from models import UserProfile
from utils.audit import record_audit_log


# 创建 Profile Blueprint，所有 Routes 以 /profile 开头
profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


# 电话：只能有数字、+、-、空格、括号，长度 7–20
# 例如 012-3456789、+60 12-345 6789
PHONE_PATTERN = re.compile(r"[0-9+\-\s()]{7,20}")

# 户口号码：只能有数字、-、空格，长度 5–30
ACCOUNT_NUMBER_PATTERN = re.compile(r"[0-9\-\s]{5,30}")


# ============================================================
# 读取用户目前的资料，整理成 Dictionary，供 Template 显示
# 尚未建立 Profile 的用户，电话显示为空字符串
# ============================================================
def get_profile_values(user):
    return {
        "phone": user.profile.phone if user.profile is not None else "",
        "bank_name": user.bank_name or "",
        "bank_account_name": user.bank_account_name or "",
        "bank_account_number": user.bank_account_number or "",
    }


# ============================================================
# 查看与修改个人资料
# GET  /profile/：显示个人资料
# POST /profile/：储存电话与银行资料
# ============================================================
@profile_bp.route("/", methods=["GET", "POST"])
@login_required
def view_profile():

    # GET：显示目前的资料
    if request.method == "GET":
        return render_template("profile.html", form=get_profile_values(current_user))

    # POST：读取表单，去除前后空格
    new_values = {
        "phone": request.form.get("phone", "").strip(),
        "bank_name": request.form.get("bank_name", "").strip(),
        "bank_account_name": request.form.get("bank_account_name", "").strip(),
        "bank_account_number": request.form.get("bank_account_number", "").strip(),
    }

    # -------------------- 验证 --------------------
    errors = []

    # 电话可以不填；有填就必须符合格式
    if new_values["phone"] and not PHONE_PATTERN.fullmatch(new_values["phone"]):
        errors.append("Phone number may only contain digits, spaces, +, - and ( ), 7 to 20 characters.")

    # 长度上限对应 models.py 的 String(100)
    if len(new_values["bank_name"]) > 100:
        errors.append("Bank name must not exceed 100 characters.")

    if len(new_values["bank_account_name"]) > 100:
        errors.append("Account holder name must not exceed 100 characters.")

    if new_values["bank_account_number"] and not ACCOUNT_NUMBER_PATTERN.fullmatch(new_values["bank_account_number"]):
        errors.append("Account number may only contain digits, spaces and -, 5 to 30 characters.")

    # 银行资料必须三项一起填，或三项都不填
    bank_fields = [
        new_values["bank_name"],
        new_values["bank_account_name"],
        new_values["bank_account_number"],
    ]
    if any(bank_fields) and not all(bank_fields):
        errors.append("Please complete all three bank fields, or leave all of them empty.")

    # 有错误：保留用户刚输入的内容，重新显示表单
    if errors:
        for error in errors:
            flash(error, "danger")
        return render_template("profile.html", form=new_values), 400

    # -------------------- 找出改了哪些栏位 --------------------
    old_values = get_profile_values(current_user)
    changed_fields = [
        field for field in new_values
        if new_values[field] != old_values[field]
    ]

    if not changed_fields:
        flash("No changes to save.", "info")
        return redirect(url_for("profile.view_profile"))

    # -------------------- 储存 --------------------

    # 第一次储存时才建立 Profile
    if current_user.profile is None:
        current_user.profile = UserProfile()

    current_user.profile.phone = new_values["phone"]
    current_user.bank_name = new_values["bank_name"]
    current_user.bank_account_name = new_values["bank_account_name"]
    current_user.bank_account_number = new_values["bank_account_number"]

    # 记录修改了哪些栏位（只记录栏位名称，不记录户口号码等内容）
    record_audit_log(
        user_id=current_user.id,
        action="update_profile",
        target_type="User",
        target_id=current_user.id,
        details="Updated fields: " + ", ".join(changed_fields),
    )

    # Profile、User 和 Audit Log 一起写入 Database
    db.session.commit()

    flash("Profile updated successfully.", "success")
    return redirect(url_for("profile.view_profile"))


# ============================================================
# 修改密码
# POST /profile/password
# ============================================================
@profile_bp.route("/password", methods=["POST"])
@login_required
def change_password():

    # 密码不使用 strip()：空格也可能是密码的一部分
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    # 依序检查，遇到第一个错误就停止
    if not current_user.check_password(current_password):
        flash("Current password is incorrect.", "danger")

    elif len(new_password) < 8:
        flash("New password must contain at least 8 characters.", "danger")

    elif new_password != confirm_password:
        flash("The new passwords do not match.", "danger")

    elif current_user.check_password(new_password):
        flash("New password must be different from the current password.", "danger")

    else:
        # 所有检查通过：产生新的 Hash 并储存
        current_user.set_password(new_password)

        record_audit_log(
            user_id=current_user.id,
            action="change_password",
            target_type="User",
            target_id=current_user.id,
            details="Password changed by user.",
        )

        db.session.commit()
        flash("Password changed successfully.", "success")

    return redirect(url_for("profile.view_profile"))