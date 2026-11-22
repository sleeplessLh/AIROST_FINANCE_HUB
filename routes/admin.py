from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from database import db
from models import User, Event, Claim, Payment, AuditLog, PasswordReset, utc_now
from utils.decorators import role_required
from werkzeug.security import generate_password_hash
from sqlalchemy.exc import IntegrityError
from datetime import date
from utils.audit import record_audit_log

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# GET /admin/: admin dashboard with user and event counts.
@admin_bp.route("/")
@login_required
@role_required("admin")
def dashboard():
    total_users = db.session.query(User).count()

    total_events = db.session.query(Event).count()

    active_users = db.session.query(User).filter(User.active.is_(True)).count()

    inactive_users = db.session.query(User).filter(User.active.is_(False)).count()

    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        total_events=total_events,
        active_users=active_users,
        inactive_users=inactive_users,
    )


# GET /admin/users: list all users.
@admin_bp.route("/users")
@login_required
@role_required("admin")
def list_users():
    statement = db.select(User).order_by(User.id.asc())
    users = db.session.scalars(statement).all()
    return render_template("admin/users.html", users=users)


# GET/POST /admin/users/create: create a user account.
@admin_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_user():
    if request.method == "GET":
        return render_template("admin/create_user.html", form={})

    club_id = request.form.get("club_id", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "")

    # Show the form again with the entered values (except the password) and a 400 status.
    def show_form_with_error(message):
        flash(message, "danger")
        return render_template("admin/create_user.html", form=request.form), 400

    if not club_id or not name or not email or not password:
        return show_form_with_error("All fields are required.")

    if role not in {"member", "treasurer", "admin"}:
        return show_form_with_error("Invalid user role.")

    if len(password) < 8:
        return show_form_with_error("Password must contain at least 8 characters.")
    
    user = User(
        club_id=club_id,
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
        active=True,
    )

    db.session.add(user)

    try:
        db.session.flush()

        record_audit_log(
            user_id=current_user.id,
            action="create_user",
            target_type="User",
            target_id=user.id,
            details=f"Club ID: {club_id}; Role: {role}",
        )
        db.session.commit()

    except IntegrityError:
        db.session.rollback()
        return show_form_with_error("Club ID or email already exists.")

    flash("User created successfully.", "success")
    return redirect(url_for("admin.list_users"))


# GET/POST /admin/users/<id>/edit: edit a user's name, email, role and active status.
@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_user(user_id):
    user = db.get_or_404(User, user_id)

    if request.method == "GET":
        return render_template("admin/edit_user.html", user=user)

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "")
    active = request.form.get("active") == "on"

    if not name or not email:
        flash("Name and email are required.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    if role not in {"member", "treasurer", "admin"}:
        flash("Invalid user role.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    if user.id == current_user.id and (not active or role != "admin"):
        flash("You cannot deactivate or demote your own account.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))
    
    changes = []
    if user.name != name:
        changes.append("name")
    if user.email != email:
        changes.append("email")
    if user.role != role:
        changes.append(f"role: {user.role} -> {role}")
    if user.active != active:
        changes.append(f"active: {user.active} -> {active}")

    user.name = name
    user.email = email
    user.role = role
    user.active = active

    if changes:
        record_audit_log(
            user_id=current_user.id,
            action="update_user",
            target_type="User",
            target_id=user.id,
            details="; ".join(changes),
        )

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Email already exists.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    flash("User updated successfully.", "success")
    return redirect(url_for("admin.list_users"))


# POST /admin/users/<id>/reset-password: set a temporary password the user must change at next sign-in.
@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@role_required("admin")
def reset_user_password(user_id):

    user = db.get_or_404(User, user_id)

    if user.id == current_user.id:
        flash("Use My Profile to change your own password.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    temporary_password = request.form.get("temporary_password", "")

    if len(temporary_password) < 8:
        flash("Temporary password must contain at least 8 characters.", "danger")
        return redirect(url_for("admin.edit_user", user_id=user.id))

    user.set_password(temporary_password)

    if user.pending_password_reset is None:
        user.pending_password_reset = PasswordReset()
    else:
        user.pending_password_reset.created_at = utc_now()

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


# GET /admin/events: list all events.
@admin_bp.route("/events")
@login_required
@role_required("admin")
def list_events():
    statement = db.select(Event).order_by(Event.id.asc())
    events = db.session.scalars(statement).all()
    return render_template("admin/events.html", events=events)


# GET/POST /admin/events/create: create an event.
@admin_bp.route("/events/create", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_event():
    if request.method == "GET":
        return render_template("admin/create_event.html")

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    event_date_text = request.form.get("event_date", "").strip()

    if not name:
        flash("Event name is required.", "danger")
        return redirect(url_for("admin.create_event"))

    if len(name) > 150:
        flash("Event name must not exceed 150 characters.", "danger")
        return redirect(url_for("admin.create_event"))

    event_date = None

    if event_date_text:
        try:
            event_date = date.fromisoformat(event_date_text)
        except ValueError:
            flash("Invalid event date.", "danger")
            return redirect(url_for("admin.create_event"))

    event = Event(name=name, description=description or None, event_date=event_date)

    db.session.add(event)

    db.session.flush()

    record_audit_log(
        user_id=current_user.id,
        action="create_event",
        target_type="Event",
        target_id=event.id,
        details=f"Name: {name}",
    )

    db.session.commit()

    flash("Event created successfully.", "success")
    return redirect(url_for("admin.list_events"))


# GET/POST /admin/events/<id>/edit: edit an event.
@admin_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_event(event_id):
    event = db.get_or_404(Event, event_id)

    if request.method == "GET":
        return render_template("admin/edit_event.html", event=event)

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    event_date_text = request.form.get("event_date", "").strip()

    if not name:
        flash("Event name is required.", "danger")
        return redirect(url_for("admin.edit_event", event_id=event.id))

    if len(name) > 150:
        flash("Event name must not exceed 150 characters.", "danger")
        return redirect(url_for("admin.edit_event", event_id=event.id))

    event_date = None

    if event_date_text:
        try:
            event_date = date.fromisoformat(event_date_text)
        except ValueError:
            flash("Invalid event date.", "danger")
            return redirect(url_for("admin.edit_event", event_id=event.id))

    changes = []
    if event.name != name:
        changes.append("name")
    if event.description != (description or None):
        changes.append("description")
    if event.event_date != event_date:
        changes.append("event_date")

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

    db.session.commit()

    flash("Event updated successfully.", "success")
    return redirect(url_for("admin.list_events"))


# POST /admin/users/<id>/delete: delete an account that has no claims, payments or activity history.
@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_user(user_id):

    user = db.get_or_404(User, user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin.list_users"))

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

    club_id = user.club_id
    details = f"Club ID: {user.club_id}; Name: {user.name}; Role: {user.role}"

    db.session.delete(user)

    record_audit_log(
        user_id=current_user.id,
        action="delete_user",
        target_type="User",
        target_id=user_id,
        details=details,
    )

    db.session.commit()

    flash(f"User {club_id} deleted successfully.", "success")
    return redirect(url_for("admin.list_users"))


    
