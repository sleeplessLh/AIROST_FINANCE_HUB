import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from database import db
from models import THEME_CHOICES, UserPreference, UserProfile
from utils.audit import record_audit_log


profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


PHONE_PATTERN = re.compile(r"[0-9+\-\s()]{7,20}")

ACCOUNT_NUMBER_PATTERN = re.compile(r"[0-9\-\s]{5,30}")


# Collect the user's phone and bank details for the profile form.
def get_profile_values(user):
    return {
        "phone": user.profile.phone if user.profile is not None else "",
        "bank_name": user.bank_name or "",
        "bank_account_name": user.bank_account_name or "",
        "bank_account_number": user.bank_account_number or "",
    }


# GET/POST /profile/: show and update contact and bank details.
@profile_bp.route("/", methods=["GET", "POST"])
@login_required
def view_profile():

    if request.method == "GET":
        return render_template("profile.html", form=get_profile_values(current_user))

    new_values = {
        "phone": request.form.get("phone", "").strip(),
        "bank_name": request.form.get("bank_name", "").strip(),
        "bank_account_name": request.form.get("bank_account_name", "").strip(),
        "bank_account_number": request.form.get("bank_account_number", "").strip(),
    }

    errors = []

    if new_values["phone"] and not PHONE_PATTERN.fullmatch(new_values["phone"]):
        errors.append("Phone number may only contain digits, spaces, +, - and ( ), 7 to 20 characters.")

    if len(new_values["bank_name"]) > 100:
        errors.append("Bank name must not exceed 100 characters.")

    if len(new_values["bank_account_name"]) > 100:
        errors.append("Account holder name must not exceed 100 characters.")

    if new_values["bank_account_number"] and not ACCOUNT_NUMBER_PATTERN.fullmatch(new_values["bank_account_number"]):
        errors.append("Account number may only contain digits, spaces and -, 5 to 30 characters.")

    bank_fields = [
        new_values["bank_name"],
        new_values["bank_account_name"],
        new_values["bank_account_number"],
    ]
    if any(bank_fields) and not all(bank_fields):
        errors.append("Please complete all three bank fields, or leave all of them empty.")

    if errors:
        for error in errors:
            flash(error, "danger")
        return render_template("profile.html", form=new_values), 400

    old_values = get_profile_values(current_user)
    changed_fields = [
        field for field in new_values
        if new_values[field] != old_values[field]
    ]

    if not changed_fields:
        flash("No changes to save.", "info")
        return redirect(url_for("profile.view_profile"))


    if current_user.profile is None:
        current_user.profile = UserProfile()

    current_user.profile.phone = new_values["phone"]
    current_user.bank_name = new_values["bank_name"]
    current_user.bank_account_name = new_values["bank_account_name"]
    current_user.bank_account_number = new_values["bank_account_number"]

    record_audit_log(
        user_id=current_user.id,
        action="update_profile",
        target_type="User",
        target_id=current_user.id,
        details="Updated fields: " + ", ".join(changed_fields),
    )

    db.session.commit()

    flash("Profile updated successfully.", "success")
    return redirect(url_for("profile.view_profile"))


# POST /profile/password: change the current user's password.
@profile_bp.route("/password", methods=["POST"])
@login_required
def change_password():

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not current_user.check_password(current_password):
        flash("Current password is incorrect.", "danger")

    elif len(new_password) < 8:
        flash("New password must contain at least 8 characters.", "danger")

    elif new_password != confirm_password:
        flash("The new passwords do not match.", "danger")

    elif current_user.check_password(new_password):
        flash("New password must be different from the current password.", "danger")

    else:
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


# GET/POST /profile/new-password: choose a new password after an admin reset.
@profile_bp.route("/new-password", methods=["GET", "POST"])
@login_required
def set_new_password():

    if not current_user.must_change_password:
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        return render_template("set_new_password.html")

    temporary_password = request.form.get("temporary_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not current_user.check_password(temporary_password):
        flash("Temporary password is incorrect.", "danger")

    elif len(new_password) < 8:
        flash("New password must contain at least 8 characters.", "danger")

    elif new_password != confirm_password:
        flash("The new passwords do not match.", "danger")

    elif current_user.check_password(new_password):
        flash("New password must be different from the temporary password.", "danger")

    else:
        current_user.set_password(new_password)

        current_user.pending_password_reset = None

        record_audit_log(
            user_id=current_user.id,
            action="change_password",
            target_type="User",
            target_id=current_user.id,
            details="Password changed after admin reset.",
        )

        db.session.commit()
        flash("Your new password has been saved.", "success")
        return redirect(url_for("dashboard"))

    return redirect(url_for("profile.set_new_password"))


# POST /profile/appearance: save the appearance theme (JSON reply for JavaScript requests).
@profile_bp.route("/appearance", methods=["POST"])
@login_required
def update_appearance():

    theme = request.form.get("theme", "").strip()

    wants_json = request.accept_mimetypes.best == "application/json"

    if theme not in THEME_CHOICES:
        if wants_json:
            return jsonify({"ok": False, "error": "Invalid appearance option."}), 400
        flash("Please choose a valid appearance option.", "danger")
        return redirect(url_for("profile.view_profile"))

    if current_user.preference is None:
        current_user.preference = UserPreference()

    current_user.preference.theme = theme
    db.session.commit()

    if wants_json:
        return jsonify({"ok": True, "theme": theme})

    flash("Appearance updated.", "success")
    return redirect(url_for("profile.view_profile"))
