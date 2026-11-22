import json
import os
import threading
import urllib.error
import urllib.request

from flask import current_app, url_for

from database import db
from models import User

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


# Sender details from BREVO_API_KEY and MAIL_FROM; None when email is not configured.
def email_settings():
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    sender = os.getenv("MAIL_FROM", "").strip()
    if not api_key or not sender:
        return None
    return {
        "api_key": api_key,
        "sender": {"email": sender, "name": os.getenv("MAIL_FROM_NAME", "AIROST Finance Hub").strip()},
    }


# POST one message to the Brevo API; failures are logged, never raised.
def deliver(payload, api_key, logger):
    request = urllib.request.Request(
        BREVO_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"api-key": api_key, "content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        logger.warning("Email to %s failed: %s", [r["email"] for r in payload["to"]], error)


# Send a plain-text email in a background thread; skipped when not configured or in tests (MAIL_SUPPRESS).
def send_email(recipients, subject, text):
    recipients = [email for email in dict.fromkeys(recipients) if email]
    if not recipients:
        return False

    if current_app.config.get("MAIL_SUPPRESS", current_app.testing):
        return False

    settings = email_settings()
    if settings is None:
        current_app.logger.info("Email not configured; skipped: %s", subject)
        return False

    payload = {
        "sender": settings["sender"],
        "to": [{"email": email} for email in recipients],
        "subject": subject,
        "textContent": text,
    }

    logger = current_app.logger
    threading.Thread(target=deliver, args=(payload, settings["api_key"], logger), daemon=True).start()
    return True


# Email addresses of all active treasurers.
def treasurer_emails():
    statement = db.select(User.email).where(User.role == "treasurer", User.active.is_(True))
    return db.session.execute(statement).scalars().all()


# Claim summary lines shared by every notification.
def claim_summary(claim):
    return (
        f"Claim: {claim.title} (#{claim.id})\n"
        f"Member: {claim.member.name}\n"
        f"Event: {claim.event.name}\n"
        f"Amount: RM {claim.amount:.2f}\n"
    )


# Tell treasurers that a claim was submitted or resubmitted for review.
def notify_claim_submitted(claim, resubmitted=False):
    verb = "resubmitted" if resubmitted else "submitted"
    link = url_for("claims.review_claim_details", claim_id=claim.id, _external=True)
    text = (
        f"A claim has been {verb} for review.\n\n"
        f"{claim_summary(claim)}\n"
        f"Review it here: {link}\n"
    )
    return send_email(treasurer_emails(), f"Claim {verb}: {claim.title}", text)


# Tell the member that their claim was approved.
def notify_claim_approved(claim):
    link = url_for("claims.claim_details", claim_id=claim.id, _external=True)
    text = (
        f"Hi {claim.member.name},\n\n"
        f"Your claim has been approved and is waiting for payment.\n\n"
        f"{claim_summary(claim)}\n"
        f"View it here: {link}\n"
    )
    return send_email([claim.member.email], f"Claim approved: {claim.title}", text)


# Tell the member that their claim was rejected, including the reason.
def notify_claim_rejected(claim, reason):
    link = url_for("claims.claim_details", claim_id=claim.id, _external=True)
    text = (
        f"Hi {claim.member.name},\n\n"
        f"Your claim has been rejected.\n\n"
        f"{claim_summary(claim)}"
        f"Reason: {reason}\n\n"
        f"You can edit the claim and resubmit it here: {link}\n"
    )
    return send_email([claim.member.email], f"Claim rejected: {claim.title}", text)
