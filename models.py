from datetime import datetime, timezone
from database import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


# Current UTC time as a naive datetime, used for timestamps.
def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# User account: login details, role and bank details.
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)

    club_id = db.Column(db.String(30), unique=True, nullable=False, index=True)

    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), nullable=False, default="member")

    active = db.Column(db.Boolean, default=True)

    bank_name = db.Column(db.String(100), default="")
    bank_account_name = db.Column(db.String(100), default="")
    bank_account_number = db.Column(db.String(60), default="")

    created_at = db.Column(db.DateTime, default=utc_now)

    claims = db.relationship("Claim", back_populates="member")

    profile = db.relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    preference = db.relationship(
        "UserPreference",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    pending_password_reset = db.relationship(
        "PasswordReset",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    pending_profile_setup = db.relationship(
        "ProfileSetup",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Store a hash of the given password.
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    # Check a password against the stored hash.
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # Tell Flask-Login whether the account may sign in.
    @property
    def is_active(self):
        return self.active

    # Appearance setting: system (default), light or dark.
    @property
    def theme(self):
        if self.preference is None:
            return DEFAULT_THEME
        return self.preference.theme

    # True while an admin password reset waits for the user to set a new password.
    @property
    def must_change_password(self):
        return self.pending_password_reset is not None

    # True until a new admin-created account has completed its profile.
    @property
    def must_complete_profile(self):
        return self.pending_profile_setup is not None


# Extra personal details (phone number), kept in their own table.
class UserProfile(db.Model):
    __tablename__ = "user_profiles"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False
    )

    phone = db.Column(db.String(30), nullable=False, default="")

    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user = db.relationship("User", back_populates="profile")


THEME_CHOICES = ("system", "light", "dark")

DEFAULT_THEME = "light"


# Per-user settings (appearance theme), kept in their own table.
class UserPreference(db.Model):
    __tablename__ = "user_preferences"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False
    )

    theme = db.Column(db.String(10), nullable=False, default=DEFAULT_THEME)

    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user = db.relationship("User", back_populates="preference")


# Pending admin password reset; deleted once the user sets a new password.
class PasswordReset(db.Model):
    __tablename__ = "password_resets"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False
    )

    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    user = db.relationship("User", back_populates="pending_password_reset")


# Pending first sign-in profile completion for an admin-created account; deleted once done.
class ProfileSetup(db.Model):
    __tablename__ = "profile_setups"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False
    )

    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    user = db.relationship("User", back_populates="pending_profile_setup")


# Club event that claims and a budget belong to.
class Event(db.Model):
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(150), nullable=False)

    description = db.Column(db.Text, nullable=True)

    event_date = db.Column(db.Date, nullable=True)

    created_at = db.Column(db.DateTime, default=utc_now)

    claims = db.relationship("Claim", back_populates="event")

    budget = db.relationship("Budget", back_populates="event", uselist=False)

    closure = db.relationship(
        "EventClosure", back_populates="event", uselist=False, cascade="all, delete-orphan"
    )

    # True once an admin has closed the event for new claims.
    @property
    def is_closed(self):
        return self.closure is not None


# Marks an event as closed for new claims; deleted when the event is reopened.
class EventClosure(db.Model):
    __tablename__ = "event_closures"

    id = db.Column(db.Integer, primary_key=True)

    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id"), unique=True, nullable=False
    )

    closed_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    event = db.relationship("Event", back_populates="closure")


# Reimbursement claim: draft, pending, approved or rejected, then paid.
class Claim(db.Model):
    __tablename__ = "claims"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(150), nullable=False)

    description = db.Column(db.Text, nullable=True)

    amount = db.Column(db.Numeric(10, 2), nullable=False)

    status = db.Column(db.String(20), nullable=False, default="draft")

    member_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=utc_now)

    member = db.relationship("User", back_populates="claims")

    event = db.relationship("Event", back_populates="claims")

    receipts = db.relationship(
        "Receipt", back_populates="claim", cascade="all, delete-orphan"
    )

    payment = db.relationship(
        "Payment", back_populates="claim", uselist=False, cascade="all, delete-orphan"
    )


# Allocated budget for one event.
class Budget(db.Model):
    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)

    event_id = db.Column(
        db.Integer, db.ForeignKey("events.id"), unique=True, nullable=False
    )

    allocated_amount = db.Column(db.Numeric(10, 2), nullable=False)

    created_at = db.Column(db.DateTime, default=utc_now)

    event = db.relationship("Event", back_populates="budget")


# Receipt file details for a claim.
class Receipt(db.Model):
    __tablename__ = "receipts"

    id = db.Column(db.Integer, primary_key=True)

    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id"), nullable=False)

    original_filename = db.Column(db.String(255), nullable=False)

    stored_filename = db.Column(db.String(255), unique=True, nullable=False)

    uploaded_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    claim = db.relationship("Claim", back_populates="receipts")

    file = db.relationship(
        "ReceiptFile",
        back_populates="receipt",
        uselist=False,
        cascade="all, delete-orphan",
    )

# Receipt file contents stored in the database, so files survive Render redeploys.
class ReceiptFile(db.Model):
    __tablename__ = "receipt_files"

    id = db.Column(db.Integer, primary_key=True)

    receipt_id = db.Column(
        db.Integer, db.ForeignKey("receipts.id"), unique=True, nullable=False
    )

    data = db.Column(db.LargeBinary, nullable=False)

    receipt = db.relationship("Receipt", back_populates="file")


# Payment record created when a treasurer marks a claim as paid.
class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)

    claim_id = db.Column(
        db.Integer, db.ForeignKey("claims.id"), unique=True, nullable=False
    )

    amount = db.Column(db.Numeric(10, 2), nullable=False)

    payment_method = db.Column(db.String(30), nullable=False)

    reference_number = db.Column(db.String(100), nullable=True)

    paid_at = db.Column(db.DateTime, nullable=False)

    paid_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    claim = db.relationship("Claim", back_populates="payment")

    paid_by = db.relationship("User", foreign_keys=[paid_by_id])

    proof = db.relationship(
        "PaymentProof", back_populates="payment", uselist=False, cascade="all, delete-orphan"
    )


# Payment proof file (e.g. transfer screenshot) stored in the database.
class PaymentProof(db.Model):
    __tablename__ = "payment_proofs"

    id = db.Column(db.Integer, primary_key=True)

    payment_id = db.Column(
        db.Integer, db.ForeignKey("payments.id"), unique=True, nullable=False
    )

    original_filename = db.Column(db.String(255), nullable=False)

    stored_filename = db.Column(db.String(255), nullable=False)

    data = db.Column(db.LargeBinary, nullable=False)

    uploaded_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    payment = db.relationship("Payment", back_populates="proof")


# Record of an important action: who did what to which record, and when.
class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    action = db.Column(db.String(100), nullable=False)

    target_type = db.Column(db.String(50), nullable=False)

    target_id = db.Column(db.Integer, nullable=False)

    details = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    user = db.relationship("User")
