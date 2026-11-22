from flask import Flask, abort, jsonify, render_template, redirect, url_for, request

from flask_login import LoginManager, login_required, current_user

from database import db

from models import DEFAULT_THEME, User, Event, Claim, Budget, Receipt, AuditLog

from routes.auth import auth_bp

from routes.claims import claims_bp

from routes.admin import admin_bp

from routes.profile import profile_bp

import os

from utils.decorators import role_required

from flask_wtf.csrf import CSRFProtect

from utils.dashboard import calculate_treasurer_statistics, calculate_member_statistics

from utils.budget import calculate_event_budget

from utils.timezone import MALAYSIA_TZ, format_malaysia_time

from datetime import datetime

from decimal import Decimal

from sqlalchemy.orm import selectinload

from sqlalchemy import func, inspect, text

import click

import hmac

import secrets

from utils.file_upload import receipt_upload_folder


app = Flask(__name__)

csrf = CSRFProtect()

csrf.init_app(app)
app.add_template_filter(format_malaysia_time, "myt")

app.register_blueprint(auth_bp)

app.register_blueprint(claims_bp)

app.register_blueprint(admin_bp)

app.register_blueprint(profile_bp)

# Read SECRET_KEY from the environment; refuse to start on Render without one.
def get_secret_key():

    secret_key = os.environ.get("SECRET_KEY", "").strip()

    if secret_key:
        return secret_key

    if os.environ.get("RENDER"):
        raise RuntimeError("SECRET_KEY must be set in the production environment.")

    return secrets.token_hex(32)


app.config["SECRET_KEY"] = get_secret_key()

app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024


database_url = os.environ.get("DATABASE_URL", "sqlite:///airost.db").strip()

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

db.init_app(app)

login_manager = LoginManager(app)

login_manager.login_view = "auth.login"


# Make default_theme available in every template (used before sign-in).
@app.context_processor
def inject_default_theme():
    return {"default_theme": DEFAULT_THEME}


# Make today_myt (today's date in Malaysia time) available in every template.
@app.context_processor
def inject_today():
    return {"today_myt": datetime.now(MALAYSIA_TZ).date().isoformat()}


PASSWORD_CHANGE_ALLOWED_ENDPOINTS = {"profile.set_new_password", "auth.logout", "static", "favicon"}
PROFILE_SETUP_ALLOWED_ENDPOINTS = {"profile.complete_profile", "auth.logout", "static", "favicon"}


# Send users to the new-password or complete-profile page until those steps are done.
@app.before_request
def require_account_setup():
    if not current_user.is_authenticated:
        return None

    if current_user.must_change_password:
        if request.endpoint not in PASSWORD_CHANGE_ALLOWED_ENDPOINTS:
            return redirect(url_for("profile.set_new_password"))
        return None

    if current_user.must_complete_profile and request.endpoint not in PROFILE_SETUP_ALLOWED_ENDPOINTS:
        return redirect(url_for("profile.complete_profile"))

    return None

# Add the unique email index to an existing user table (create_all does not alter tables).
def ensure_email_unique_index():

    duplicates = (
        db.session.execute(
            db.select(User.email).group_by(User.email).having(func.count(User.id) > 1)
        )
        .scalars()
        .all()
    )

    if duplicates:
        app.logger.warning(
            "Unique email index not created. Duplicate emails: %s",
            ", ".join(duplicates),
        )
        return

    db.session.execute(
        text('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email ON "user" (email)')
    )
    db.session.commit()


LEGACY_MERCHANT = "KY Enterprise"


# Add claims.merchant if missing and, only then, set existing claims to KY Enterprise.
def ensure_claim_merchant_column():
    columns = {column["name"] for column in inspect(db.engine).get_columns("claims")}
    if "merchant" in columns:
        return

    db.session.execute(text("ALTER TABLE claims ADD COLUMN merchant VARCHAR(150)"))
    result = db.session.execute(
        text("UPDATE claims SET merchant = :merchant"), {"merchant": LEGACY_MERCHANT}
    )
    db.session.commit()
    app.logger.info("Added claims.merchant; set %s existing claims to %s", result.rowcount, LEGACY_MERCHANT)


# Add claims.purchase_date if missing; existing claims stay empty.
def ensure_claim_purchase_date_column():
    columns = {column["name"] for column in inspect(db.engine).get_columns("claims")}
    if "purchase_date" in columns:
        return

    db.session.execute(text("ALTER TABLE claims ADD COLUMN purchase_date DATE"))
    db.session.commit()
    app.logger.info("Added claims.purchase_date (existing claims left empty)")


with app.app_context():
    db.create_all()

    ensure_email_unique_index()

    ensure_claim_merchant_column()

    ensure_claim_purchase_date_column()

# CLI: create an admin account (flask --app app create-admin).
@app.cli.command("create-admin")
@click.option("--club-id", prompt="Admin AIROST ID")
@click.option("--name", prompt="Admin name")
@click.option("--email", prompt="Admin email")
@click.password_option(prompt="Admin password", confirmation_prompt=True)
def create_admin(club_id, name, email, password):
    from sqlalchemy.exc import IntegrityError

    club_id = club_id.strip()
    name = name.strip()
    email = email.strip().lower()

    if not club_id or not name or not email:
        raise click.ClickException("AIROST ID, name, and email are required.")

    if len(password) < 8:
        raise click.ClickException("Password must contain at least 8 characters.")

    admin = User(
        club_id=club_id,
        name=name,
        email=email,
        role="admin",
        active=True,
    )
    admin.set_password(password)

    db.session.add(admin)

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise click.ClickException(
            "That AIROST ID or email is already registered."
        ) from error

    click.echo(f"Administrator account created for {club_id}.")

# GET/POST /setup-admin: create the first admin with ADMIN_SETUP_KEY; closed once an admin exists.
@app.route("/setup-admin", methods=["GET", "POST"])
def setup_admin():
    from sqlalchemy.exc import IntegrityError

    setup_key = os.environ.get("ADMIN_SETUP_KEY", "").strip()

    if not setup_key:
        abort(404)

    existing_admin_id = db.session.scalar(
        db.select(User.id)
        .where(User.role == "admin")
        .limit(1)
    )
    if existing_admin_id is not None:
        abort(404)

    error_message = None

    if request.method == "POST":
        submitted_key = request.form.get("setup_key", "")

        if not hmac.compare_digest(submitted_key, setup_key):
            return render_template(
                "setup_admin.html",
                error_message="Invalid setup key.",
            ), 403

        club_id = request.form.get("club_id", "").strip()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not club_id or not name or not email or not password:
            error_message = "Please complete all fields."
        elif len(password) < 8:
            error_message = "Password must contain at least 8 characters."
        elif password != confirm_password:
            error_message = "The passwords do not match."

        if error_message:
            return render_template(
                "setup_admin.html",
                error_message=error_message,
            ), 400

        admin_user = User(
            club_id=club_id,
            name=name,
            email=email,
            role="admin",
            active=True,
        )
        admin_user.set_password(password)
        db.session.add(admin_user)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return render_template(
                "setup_admin.html",
                error_message="That AIROST ID or email is already registered.",
            ), 409

        return redirect(url_for("auth.login"))

    return render_template("setup_admin.html", error_message=error_message)


# Render the 403 error page.
@app.errorhandler(403)
def forbidden_error(error):
    return render_template(
        "error.html",
        error_code = 403,
        error_title="Access Forbidden",
        error_message = "You do not have permission to access this page."
    ), 403

# Render the 404 error page.
@app.errorhandler(404)
def page_not_found(error):
    return render_template(
        "error.html",
        error_code=404,
        error_title="Page Not Found",
        error_message="The page you are looking for does not exist.",
    ), 404

# Log the error, roll back the session and render the 500 error page.
@app.errorhandler(500)
def internal_server_error(error):

    original_error = getattr(error, "original_exception", None)

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

    try:
        db.session.rollback()
    except Exception:
        app.logger.exception("Database rollback failed")

    try:
        return render_template(
            "error.html",
            error_code=500,
            error_title="Internal Server Error",
            error_message="An unexpected error occurred. Please try again later.",
        ), 500

    except Exception:
        app.logger.exception("Failed to render 500 error page")

        return "500 Internal Server Error", 500

# Flask-Login: load the user stored in the session.
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# POST /tasks/pending-reminders: email treasurers about claims pending 3+ days (needs X-Task-Token).
@app.route("/tasks/pending-reminders", methods=["POST"])
@csrf.exempt
def pending_reminders():
    from utils.notifications import notify_pending_reminders

    task_token = os.environ.get("TASK_TOKEN", "").strip()
    if not task_token:
        abort(404)

    if not hmac.compare_digest(request.headers.get("X-Task-Token", ""), task_token):
        abort(403)

    reminded = notify_pending_reminders()
    return jsonify({"ok": True, "reminded_claims": reminded})


# GET /favicon.ico: redirect to the SVG site icon.
@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.svg"), code=301)


# GET /: redirect to the dashboard.
@app.route("/")
@login_required
def home():
    return redirect(url_for("dashboard"))


# GET /dashboard: admins go to /admin/; treasurers and members see their own dashboard.
@app.route("/dashboard")
@login_required
def dashboard():

    role = current_user.role

    if role == "member":
        stats = calculate_member_statistics(current_user.id)
        return render_template("member_dashboard.html", stats = stats)
    
    elif role == "treasurer":
        stats = calculate_treasurer_statistics()


        events = db.session.execute(
            db.select(Event).order_by(Event.id)
        ).scalars().all()

        event_budget_rows = []

        for event in events:

            budget_info = calculate_event_budget(event)

            utilization = budget_info["utilization_percentage"]

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

            pending_amount = db.session.scalar(
                db.select(func.sum(Claim.amount))
                .where(Claim.event_id == event.id, Claim.status == "pending")
            ) or Decimal("0.00")

            if budget_info["allocated"] > 0:
                committed_width = min(max(utilization, Decimal("0")), Decimal("100"))
                pending_width = min(pending_amount / budget_info["allocated"] * 100, 100 - committed_width)
            else:
                committed_width = Decimal("0")
                pending_width = Decimal("0")

            event_budget_rows.append({
                "event": event,
                "budget": budget_info,
                "risk_level": risk_level,
                "pending": pending_amount,
                "committed_width": committed_width,
                "pending_width": pending_width,
                "would_exceed": budget_info["remaining"] >= 0 and pending_amount > budget_info["remaining"],
            })


        pending_claims = db.session.execute(
            db.select(Claim)
            .where(Claim.status == "pending")
            .order_by(Claim.created_at.desc())
        ).scalars().all()

        pending_amount = sum(
            (claim.amount for claim in pending_claims),
            Decimal("0.00")
        )

        pending_claim_rows = []

        event_budget_cache = {}

        at_risk_count = 0

        for claim in pending_claims:

            event_id = claim.event_id

            if event_id not in event_budget_cache:

                event_budget_cache[event_id] = calculate_event_budget(
                    claim.event
                )

            budget_info = event_budget_cache[event_id]

            is_over_budget = claim.amount > budget_info["remaining"]

            if is_over_budget:
                at_risk_count += 1

            pending_claim_rows.append({
                "claim": claim,
                "budget": budget_info,
                "is_over_budget": is_over_budget
            })

        return render_template(
            "treasurer_dashboard.html",
            stats=stats,
            event_budget_rows=event_budget_rows,
            pending_claim_rows=pending_claim_rows,
            pending_amount=pending_amount,
            pending_count=len(pending_claims),
            at_risk_count=at_risk_count
        )

    elif role == "admin":
        return redirect(url_for("admin.dashboard"))

    else:
        abort(403)


# GET /admin/audit-logs: paginated audit log with action and user filters.
@app.route("/admin/audit-logs", methods=["GET"])
@login_required
@role_required("admin")
def admin_audit_logs():

    action_filter = request.args.get("action", "").strip()

    user_filter = request.args.get("user_id", "").strip()

    page = request.args.get("page", 1, type=int)

    page = max(page, 1)

    per_page = 10

    statement = db.select(AuditLog).options(
        selectinload(AuditLog.user)
    )

    if action_filter:
        statement = statement.where(AuditLog.action == action_filter)

    if user_filter:
        if user_filter.isdecimal() and int(user_filter) > 0:
            statement = statement.where(AuditLog.user_id == int(user_filter)) 
        else:
            statement = statement.where(AuditLog.user_id == -1)

    statement = statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())

    pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

    logs = pagination.items

    actions = (
        db.session.execute(
            db.select(AuditLog.action).distinct().order_by(AuditLog.action)
        )
        .scalars()
        .all()
    )

    return render_template(
        "admin_audit_logs.html",
        logs=logs,
        pagination=pagination,
        actions=actions,
        action_filter=action_filter,
        user_filter=user_filter,
    )


# CLI: list receipt files on disk with no database record; --delete removes them.
@app.cli.command("cleanup-receipts")
@click.option(
    "--delete",
    is_flag=True,
    help="Actually delete orphan files. Without this flag, only list them.",
)
def cleanup_receipts(delete):

    upload_folder = receipt_upload_folder()

    if not upload_folder.is_dir():
        click.echo("No receipt folder found. Nothing to clean.")
        return

    referenced = set(db.session.scalars(db.select(Receipt.stored_filename)).all())

    orphans = sorted(
        path
        for path in upload_folder.iterdir()
        if path.is_file() and path.name not in referenced
    )

    if not orphans:
        click.echo("No orphan receipt files found.")
        return

    for path in orphans:
        click.echo(f"  {path.name}  ({path.stat().st_size} bytes)")

    if not delete:
        click.echo(
            f"{len(orphans)} orphan file(s) found. "
            "Run again with --delete to remove them."
        )
        return

    for path in orphans:
        path.unlink(missing_ok=True)

    click.echo(f"Deleted {len(orphans)} orphan file(s).")
