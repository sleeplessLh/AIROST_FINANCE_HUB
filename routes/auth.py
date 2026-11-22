from flask import Blueprint, render_template, request, redirect, url_for, flash

from flask_login import current_user, login_user, logout_user, login_required

from sqlalchemy import func

from models import User

from database import db


auth_bp = Blueprint("auth", __name__)


# GET/POST /login: show the sign-in form and sign the user in.
@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        club_id = request.form["club_id"].strip().lower()

        password = request.form["password"]

        user = db.session.execute(
            db.select(User).where(func.lower(User.club_id) == club_id)
        ).scalar_one_or_none()

        if user and user.active and user.check_password(password):
            login_user(user, remember=bool(request.form.get("remember")))

            return redirect(url_for("dashboard"))

        flash("Invalid AIROST ID or password.", "danger")

    return render_template("login.html")


# POST /logout: sign the user out.
@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
