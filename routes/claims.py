from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    current_app,
    send_from_directory,
    send_file,
    Response,
    jsonify
)

from flask_login import login_required, current_user

from decimal import Decimal, InvalidOperation

from database import db

from models import (
    Event,
    Claim,
    Receipt,
    ReceiptFile,
    Budget,
    Payment,
    PaymentProof,
    AuditLog,
    utc_now,
)
from utils.decorators import role_required

from utils.notifications import notify_claim_approved, notify_claim_paid, notify_claim_rejected, notify_claim_submitted

from utils.timeline import claim_timeline

from utils.listing import PER_PAGE, filter_args, filter_claims, page_number, search_text


from pathlib import Path

from utils.file_upload import (
    validate_upload,
    save_receipt,
    read_stored_receipt,
    receipt_mime_type,
    delete_stored_receipt,
    receipt_upload_folder,
)

from utils.budget import calculate_event_budget

from datetime import datetime, timedelta, timezone
import csv

import io

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from utils.audit import record_audit_log

import os 

from utils.file_upload import (
    ALLOWED_EXTENSIONS,
    MAX_RECEIPT_SIZE,
    detect_file_type,
)

from utils.receipt_ai import (
    extract_receipt_data,
    review_claim_receipts,
    ReceiptAnalysisError
)

from utils.timezone import format_malaysia_time

from utils.csv_safety import neutralize_csv_formula


claims_bp = Blueprint("claims", __name__, url_prefix="/claims")

# POST /claims/analyse-receipt: read an uploaded receipt with AI and return suggested claim fields as JSON.
@claims_bp.route("/analyse-receipt", methods=["POST"])
@login_required
@role_required("member")
def analyse_receipt():
    receipt = request.files.get("receipt")

    if receipt is None or not receipt.filename:
        return jsonify(error="Choose a receipt file first."), 400

    extension = Path(receipt.filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        return jsonify(error="Only JPG, PNG and PDF files are allowed."), 400

    file_bytes = receipt.stream.read(MAX_RECEIPT_SIZE + 1)

    if not file_bytes:
        return jsonify(error="The selected receipt is empty."), 400

    if len(file_bytes) > MAX_RECEIPT_SIZE:
        return jsonify(error="Receipt file must not exceed 5 MB."), 400

    detected_type = detect_file_type(file_bytes)

    if detected_type is None:
        return jsonify(error="Unsupported or invalid receipt file."), 400

    expected_type = "jpg" if extension in {".jpg", ".jpeg"} else extension[1:]

    if detected_type != expected_type:
        return jsonify(error="File extension does not match file content."), 400

    mime_types = {
        "jpg": "image/jpeg",
        "png": "image/png",
        "pdf": "application/pdf",
    }
    mime_type = mime_types[detected_type]

    try:
        extracted_data = extract_receipt_data(file_bytes, mime_type)

        return jsonify(extracted_data)

    except ReceiptAnalysisError as error:
        if not os.getenv("GEMINI_API_KEY", "").strip():
            return jsonify(error=str(error)), 503

        return jsonify(error=str(error)), 502


# GET/POST /claims/submit: create a claim, saved as a draft or submitted for review.
@claims_bp.route("/submit", methods=["GET", "POST"])
@login_required
@role_required("member")
def submit_claim():
    events = db.session.execute(
        db.select(Event).where(~Event.closure.has()).order_by(Event.name)
    ).scalars().all()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        merchant = request.form.get("merchant", "").strip()
        description = request.form.get("description", "").strip()
        amount_text = request.form.get("amount", "").strip()
        event_id_text = request.form.get("event_id", "").strip()
        action = request.form.get("action", "submit")

        if action not in {"draft", "submit"}:
            flash("Invalid claim action.", "danger")
            return render_template("submit_claim.html", events=events)

        if not title or not merchant or not amount_text or not event_id_text:
            flash("Please fill in all required fields.", "danger")
            return render_template("submit_claim.html", events=events)

        if len(title) > 150:
            flash("Claim title must not exceed 150 characters.", "danger")
            return render_template("submit_claim.html", events=events)

        if len(merchant) > 150:
            flash("Merchant must not exceed 150 characters.", "danger")
            return render_template("submit_claim.html", events=events)

        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            flash("Invalid claim amount.", "danger")
            return render_template("submit_claim.html", events=events)

        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            flash(
                "Amount must be positive and have at most 2 decimal places.",
                "danger",
            )
            return render_template("submit_claim.html", events=events)

        if amount > Decimal("99999999.99"):
            flash("Claim amount exceeds the allowed limit.", "danger")
            return render_template("submit_claim.html", events=events)

        try:
            event_id = int(event_id_text)
        except ValueError:
            flash("Invalid event selection.", "danger")
            return render_template("submit_claim.html", events=events)

        event = db.session.get(Event, event_id)
        if event is None:
            flash("Selected event does not exist.", "danger")
            return render_template("submit_claim.html", events=events)

        if event.is_closed:
            flash("This event is closed for new claims.", "danger")
            return render_template("submit_claim.html", events=events)

        claim_status = "draft" if action == "draft" else "pending"

        duplicate_id = db.session.scalar(
            db.select(Claim.id)
            .where(Claim.member_id == current_user.id)
            .where(Claim.event_id == event.id)
            .where(Claim.title == title)
            .where(Claim.amount == amount)
            .where(Claim.status == claim_status)
            .where(Claim.created_at >= utc_now() - timedelta(seconds=30))
            .limit(1)
        )
        if duplicate_id is not None:
            flash("This claim was already submitted.", "info")
            return redirect(url_for("claims.claim_details", claim_id=duplicate_id))

        file = request.files.get("receipt")
        has_file = file is not None and bool(file.filename)

        if action == "submit" and not has_file:
            flash("Please upload a receipt before submitting the claim.", "danger")
            return render_template("submit_claim.html", events=events)

        original_filename = None
        stored_filename = None

        if has_file:
            try:
                original_filename, stored_filename = save_receipt(file)
            except ValueError as error:
                flash(str(error), "danger")
                return render_template("submit_claim.html", events=events)

        claim = Claim(
            title=title,
            merchant=merchant,
            description=description,
            amount=amount,
            status=claim_status,
            member_id=current_user.id,
            event_id=event.id,
        )

        if original_filename is not None and stored_filename is not None:
            receipt = Receipt(
                original_filename=original_filename,
                stored_filename=stored_filename,
            )

            file_bytes = read_stored_receipt(stored_filename)
            if file_bytes is not None:
                receipt.file = ReceiptFile(data=file_bytes)

            claim.receipts.append(receipt)

        try:
            db.session.add(claim)

            if claim_status == "pending":
                db.session.flush()
                record_audit_log(
                    user_id=current_user.id,
                    action="submit_claim",
                    target_type="Claim",
                    target_id=claim.id,
                    details="Claim submitted for review.",
                )

            db.session.commit()
        except Exception:
            db.session.rollback()

            if stored_filename is not None:
                delete_stored_receipt(stored_filename)

            raise

        if action == "draft":
            flash("Claim saved as draft.", "success")
        else:
            notify_claim_submitted(claim)
            flash("Claim submitted for review.", "success")

        return redirect(url_for("claims.claim_details", claim_id=claim.id))

    return render_template("submit_claim.html", events=events)


# GET /claims/my: list the current member's claims.
@claims_bp.route("/my", methods=["GET"])
@login_required
@role_required("member")
def my_claims():

    member_id = current_user.id

    statement = (
        db.select(Claim)
        .where(Claim.member_id == member_id)
        .order_by(Claim.created_at.desc(), Claim.id.desc())
    )

    q = search_text()
    status = request.args.get("status", "")
    statement = filter_claims(statement, q, status, {"draft", "pending", "approved", "paid", "rejected"})

    pagination = db.paginate(statement, page=page_number(), per_page=PER_PAGE, error_out=False)

    return render_template(
        "my_claims.html",
        claims=pagination.items,
        pagination=pagination,
        q=q,
        status=status,
        args=filter_args(q=q, status=status),
    )

# GET /claims/<id>: show one of the current member's claims.
@claims_bp.route("/<int:claim_id>", methods=["GET"])
@login_required
@role_required("member")
def claim_details(claim_id):
    claim = db.session.get(Claim, claim_id)

    if claim is None:
        abort(404)

    if claim.member_id != current_user.id:
        abort(404)

    can_edit = claim.status in {'draft', 'rejected'}

    rejection_log = db.session.execute(
        db.select(AuditLog)
        .where(AuditLog.target_type == "Claim")
        .where(AuditLog.target_id == claim.id)
        .where(AuditLog.action == "reject_claim")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).scalars().first()
    
    return render_template(
    "claim_details.html",
    claim=claim,
    can_edit=can_edit,
    rejection_reason = rejection_log.details if rejection_log else None,
    timeline=claim_timeline(claim),
    ) 


# GET /claims/receipts/<id>/view: show a receipt in the browser (claim owner or treasurer).
@claims_bp.route("/receipts/<int:receipt_id>/view", methods=["GET"])
@login_required
def view_receipt(receipt_id):

    receipt = db.session.get(Receipt, receipt_id)

    if receipt is None:
        abort(404)

    claim = receipt.claim

    if not (
        (current_user.role == "member" and claim.member_id == current_user.id)
        or current_user.role == "treasurer"
    ):
        abort(404)

    return build_receipt_response(receipt, as_attachment=False)


# GET /claims/receipts/<id>/download: download a receipt (claim owner or treasurer).
@claims_bp.route("/receipts/<int:receipt_id>/download", methods=["GET"])
@login_required
def download_receipt(receipt_id):

    receipt = db.session.get(Receipt, receipt_id)

    if receipt is None:
        abort(404)

    claim = receipt.claim

    if not (
        (current_user.role == "member" and claim.member_id == current_user.id)
        or current_user.role == "treasurer"
    ):
        abort(404)

    return build_receipt_response(receipt, as_attachment=True)


# GET /claims/review: list all submitted claims for treasurers (drafts excluded).
@claims_bp.route("/review", methods=["GET"])
@login_required
@role_required("treasurer")
def review_claims():
    statement = (
        db.select(Claim)
        .where(Claim.status != "draft")
        .order_by(Claim.created_at.desc(), Claim.id.desc())
    )

    q = search_text()
    status = request.args.get("status", "")
    statement = filter_claims(statement, q, status, {"pending", "approved", "paid", "rejected"})
    pagination = db.paginate(statement, page=page_number(), per_page=PER_PAGE, error_out=False)

    return render_template(
        "review_claims.html",
        claims=pagination.items,
        pagination=pagination,
        q=q,
        status=status,
        args=filter_args(q=q, status=status),
    )


# POST /claims/review/<id>/ai-check: AI checklist comparing the receipts with the claim (advice only).
@claims_bp.route("/review/<int:claim_id>/ai-check", methods=["POST"])
@login_required
@role_required("treasurer")
def ai_check_claim(claim_id):
    claim = db.get_or_404(Claim, claim_id)

    if claim.status == "draft":
        return jsonify(error="Draft claims cannot be checked."), 400

    receipt_files = []
    for receipt in claim.receipts[-3:]:
        data = read_stored_receipt(receipt.stored_filename)
        if data is None and receipt.file is not None:
            data = receipt.file.data
        if data:
            receipt_files.append((data, receipt_mime_type(receipt.stored_filename)))

    if not receipt_files:
        return jsonify(error="This claim has no receipt to check."), 400

    claim_details = {
        "title": claim.title,
        "merchant": claim.merchant or "",
        "claimed_amount_rm": f"{claim.amount:.2f}",
        "description": claim.description or "",
        "event": claim.event.name,
        "event_date": claim.event.event_date.isoformat() if claim.event.event_date else "not set",
        "claim_created": format_malaysia_time(claim.created_at, "%Y-%m-%d"),
    }

    try:
        result = review_claim_receipts(claim_details, receipt_files)
    except ReceiptAnalysisError as error:
        status_code = 503 if not os.getenv("GEMINI_API_KEY", "").strip() else 502
        return jsonify(error=str(error)), status_code

    warnings = [check["name"] for check in result["checks"] if check["status"] == "warning"]
    if warnings:
        outcome = f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''} ({', '.join(warnings)})"
    else:
        outcome = "no warnings"
    record_audit_log(
        user_id=current_user.id,
        action="ai_check_claim",
        target_type="Claim",
        target_id=claim.id,
        details=f"AI check: {outcome}. {result['summary']}".strip(),
    )
    db.session.commit()

    return jsonify(result)


# GET /claims/review/<id>: treasurer view of a claim with its event budget.
@claims_bp.route("/review/<int:claim_id>", methods=["GET"])
@login_required
@role_required("treasurer")
def review_claim_details(claim_id):

    claim = db.session.get(Claim, claim_id)

    if claim is None:
        abort(404)

    event = claim.event

    budget_info = calculate_event_budget(event)

    is_over_budget = (
        claim.status == "pending" and claim.amount > budget_info["remaining"]
    )

    return render_template(
        "review_claim_details.html",
        claim=claim,
        budget_info=budget_info,
        is_over_budget=is_over_budget,
        timeline=claim_timeline(claim, include_internal=True),
        ai_check_enabled=bool(os.getenv("GEMINI_API_KEY", "").strip()),
    )


# POST /claims/review/<id>/approve: approve a pending claim if the event budget allows it.
@claims_bp.route("/review/<int:claim_id>/approve", methods=["POST"])
@login_required
@role_required("treasurer")
def approve_claim(claim_id):

    claim = db.session.get(Claim, claim_id)

    if claim is None:
        abort(404)

    if claim.status != "pending":
        flash("Only pending claims can be approved.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


    event = claim.event

    budget_info = calculate_event_budget(event)

    remaining_budget = budget_info["remaining"]

    if claim.amount > remaining_budget:
        flash(
            f"Insufficient event budget. "
            f"Remaining: RM {remaining_budget:.2f}, "
            f"Claim Amount: RM {claim.amount:.2f}.",
            "danger",
        )

        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


    claim.status = "approved"

    record_audit_log(
        user_id=current_user.id,
        action="approve_claim",
        target_type="Claim",
        target_id=claim.id,
        details="Claim approved by treasurer."
    )

    db.session.commit()

    notify_claim_approved(claim)

    flash("Claim approved successfully.", "success")

    return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


# POST /claims/review/<id>/reject: reject a pending claim with a reason.
@claims_bp.route("/review/<int:claim_id>/reject", methods=["POST"])
@login_required
@role_required("treasurer")
def reject_claim(claim_id):

    claim = db.session.get(Claim, claim_id)

    if claim is None:
        abort(404)

    if claim.status != "pending":
        flash("This claim has already been reviewed.", "warning")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    rejection_reason = request.form.get("rejection_reason", "").strip()

    if not rejection_reason:
        flash("Please provide a reason for rejecting this claim.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    claim.status = "rejected"

    record_audit_log(
        user_id=current_user.id,
        action="reject_claim",
        target_type="Claim",
        target_id=claim.id,
        details=rejection_reason,
    )

    db.session.commit()

    notify_claim_rejected(claim, rejection_reason)

    flash("Claim rejected successfully!", "success")

    return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


# GET /claims/budgets: list every event with its budget figures.
@claims_bp.route("/budgets", methods=["GET"])
@login_required
@role_required("treasurer")
def manage_budgets():

    statement = db.select(Event).order_by(Event.name)

    events = db.session.execute(statement).scalars().all()

    budget_rows = []

    for event in events:
        budget_info = calculate_event_budget(event)

        row = {"event": event, "budget": budget_info}

        budget_rows.append(row)

    return render_template("manage_budgets.html", budget_rows=budget_rows)


# POST /claims/budgets/<event_id>/update: create or change an event's budget.
@claims_bp.route("/budgets/<int:event_id>/update", methods=["POST"])
@login_required
@role_required("treasurer")
def update_budget(event_id):


    event = db.session.get(Event, event_id)

    if event is None:
        abort(404)


    amount_str = request.form.get("allocated_amount", "").strip()

    try:
        new_allocated_amount = Decimal(amount_str)

    except InvalidOperation:
        flash("Invalid budget amount.", "danger")
        return redirect(url_for("claims.manage_budgets"))

    if not new_allocated_amount.is_finite() or new_allocated_amount < 0:
        flash("Budget amount must be a non-negative number.", "danger")
        return redirect(url_for("claims.manage_budgets"))

    if new_allocated_amount.as_tuple().exponent < -2:
        flash("Budget amount cannot exceed two decimal places.", "danger")
        return redirect(url_for("claims.manage_budgets"))

    if new_allocated_amount >= Decimal("100000000"):
        flash("Budget amount is too large.", "danger")
        return redirect(url_for("claims.manage_budgets"))


    budget_info = calculate_event_budget(event)

    committed_amount = budget_info["committed"]

    if new_allocated_amount < committed_amount:

        flash(
            f"Budget adjustment rejected. "
            f"Committed amount: RM {committed_amount:.2f}. "
            f"New allocated budget cannot be lower than "
            f"the committed amount.",
            "danger"
        )

        return redirect(url_for("claims.manage_budgets"))


    old_allocated_amount = (
        event.budget.allocated_amount
        if event.budget is not None
        else Decimal("0.00")
    )

    if event.budget is None:

        budget = Budget(
            event_id=event.id,
            allocated_amount=new_allocated_amount
        )

        db.session.add(budget)

    else:
        event.budget.allocated_amount = new_allocated_amount


    record_audit_log(
        user_id=current_user.id,
        action="update_budget",
        target_type="Budget",
        target_id=event.id,
        details=(
            f"Event ID: {event.id}; "
            f"Old Amount: RM {old_allocated_amount:.2f}; "
            f"New Amount: RM {new_allocated_amount:.2f}"
        )
    )

    db.session.commit()

    flash("Event budget updated successfully.", "success")

    return redirect(url_for("claims.manage_budgets"))


# POST /claims/review/<id>/pay: record the payment for an approved claim.
@claims_bp.route("/review/<int:claim_id>/pay", methods=["POST"])
@login_required
@role_required("treasurer")
def mark_claim_paid(claim_id):

    claim = db.session.get(Claim, claim_id)


    if claim is None:
        abort(404)

    if claim.status != "approved":
        flash("Only approved claims can be marked as paid.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    payment_method = request.form.get("payment_method", "").strip()

    reference_number = request.form.get("reference_number", "").strip()

    if payment_method not in ("Bank Transfer", "Cash"):
        flash("Invalid payment method.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    if len(reference_number) > 100:
        flash("Reference number is too long.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    if claim.payment is not None:
        flash("A payment record already exists for this claim.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    proof = None
    proof_file = request.files.get("payment_proof")
    if proof_file is not None and proof_file.filename:
        try:
            original_filename, data, stored_filename = validate_upload(proof_file, "payment proof")
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("claims.review_claim_details", claim_id=claim.id))
        proof = PaymentProof(
            original_filename=original_filename,
            stored_filename=stored_filename,
            data=data,
        )

    payment = Payment(
        claim_id=claim.id,
        amount=claim.amount,
        payment_method=payment_method,
        reference_number=reference_number or None,
        paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        paid_by_id=current_user.id,
    )

    if proof is not None:
        payment.proof = proof

    claim.status = "paid"

    record_audit_log(
        user_id=current_user.id,
        action="mark_claim_paid",
        target_type="Claim",
        target_id=claim.id,
        details=(
            f"Amount: RM {claim.amount:.2f}; "
            f"Method: {payment_method}; "
            f"Reference: {reference_number or 'N/A'}"
        ),
    )

    db.session.add(payment)

    try:
        db.session.commit()

    except IntegrityError:
        db.session.rollback()

        flash(
            "Payment could not be saved because of a database constraint. "
            "Please refresh and verify the claim's payment status.",
            "danger",
        )

        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    except SQLAlchemyError:
        db.session.rollback()

        flash("Failed to save payment record.", "danger")

        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    notify_claim_paid(claim)

    flash("Claim marked as paid successfully.", "success")

    return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


# POST /claims/review/<id>/payment-proof: add or replace the payment proof of a paid claim.
@claims_bp.route("/review/<int:claim_id>/payment-proof", methods=["POST"])
@login_required
@role_required("treasurer")
def upload_payment_proof(claim_id):
    claim = db.get_or_404(Claim, claim_id)

    if claim.status != "paid" or claim.payment is None:
        flash("Payment proof can only be added to a paid claim.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    try:
        original_filename, data, stored_filename = validate_upload(
            request.files.get("payment_proof"), "payment proof"
        )
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    replaced = claim.payment.proof is not None

    if replaced:
        proof = claim.payment.proof
        proof.original_filename = original_filename
        proof.stored_filename = stored_filename
        proof.data = data
        proof.uploaded_at = utc_now()
    else:
        claim.payment.proof = PaymentProof(
            original_filename=original_filename,
            stored_filename=stored_filename,
            data=data,
        )

    record_audit_log(
        user_id=current_user.id,
        action="upload_payment_proof",
        target_type="Claim",
        target_id=claim.id,
        details=f"{'Replaced' if replaced else 'Added'} payment proof: {original_filename}",
    )

    db.session.commit()

    flash("Payment proof uploaded.", "success")
    return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


# GET /claims/<id>/payment-proof: view (or ?download=1) the payment proof (claim owner or treasurer).
@claims_bp.route("/<int:claim_id>/payment-proof", methods=["GET"])
@login_required
def view_payment_proof(claim_id):
    claim = db.session.get(Claim, claim_id)

    allowed = claim is not None and (
        (current_user.role == "member" and claim.member_id == current_user.id)
        or current_user.role == "treasurer"
    )
    if not allowed or claim.payment is None or claim.payment.proof is None:
        abort(404)

    proof = claim.payment.proof
    response = send_file(
        io.BytesIO(proof.data),
        mimetype=receipt_mime_type(proof.stored_filename),
        as_attachment=request.args.get("download") == "1",
        download_name=proof.original_filename,
    )

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# Submitted claims of an event plus count and amount per status.
def event_report_data(event):
    claims = db.session.execute(
        db.select(Claim)
        .where(Claim.event_id == event.id, Claim.status != "draft")
        .order_by(Claim.created_at, Claim.id)
    ).scalars().all()

    totals = {
        status: {"count": 0, "amount": Decimal("0.00")}
        for status in ("pending", "approved", "paid", "rejected")
    }
    for claim in claims:
        totals[claim.status]["count"] += 1
        totals[claim.status]["amount"] += claim.amount

    return claims, totals


# GET /claims/budgets/<event_id>/report: event expense report page.
@claims_bp.route("/budgets/<int:event_id>/report", methods=["GET"])
@login_required
@role_required("treasurer")
def event_report(event_id):
    event = db.get_or_404(Event, event_id)
    claims, totals = event_report_data(event)

    return render_template(
        "event_report.html",
        event=event,
        budget_info=calculate_event_budget(event),
        claims=claims,
        totals=totals,
    )


# GET /claims/budgets/<event_id>/report.csv: event expense report as CSV.
@claims_bp.route("/budgets/<int:event_id>/report.csv", methods=["GET"])
@login_required
@role_required("treasurer")
def event_report_csv(event_id):
    event = db.get_or_404(Event, event_id)
    claims, totals = event_report_data(event)
    budget_info = calculate_event_budget(event)

    output = io.StringIO(newline="")
    writer = csv.writer(output)

    writer.writerow(["Event", neutralize_csv_formula(event.name)])
    writer.writerow(["Status", "Closed" if event.is_closed else "Open"])
    writer.writerow(["Allocated (RM)", f"{budget_info['allocated']:.2f}"])
    writer.writerow(["Committed (RM)", f"{budget_info['committed']:.2f}"])
    writer.writerow(["Remaining (RM)", f"{budget_info['remaining']:.2f}"])
    writer.writerow(["Pending (RM)", f"{totals['pending']['amount']:.2f}"])
    writer.writerow([])

    writer.writerow(["Claim ID", "Member", "Title", "Merchant", "Amount (RM)", "Status", "Submitted At", "Paid At"])
    for claim in claims:
        writer.writerow([
            claim.id,
            neutralize_csv_formula(claim.member.name),
            neutralize_csv_formula(claim.title),
            neutralize_csv_formula(claim.merchant or ""),
            f"{claim.amount:.2f}",
            claim.status,
            format_malaysia_time(claim.created_at, "%Y-%m-%d %H:%M"),
            format_malaysia_time(claim.payment.paid_at, "%Y-%m-%d %H:%M") if claim.payment else "",
        ])

    csv_content = "\ufeff" + output.getvalue()
    output.close()

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=event_{event.id}_report.csv"},
    )


# GET /claims/export: download all claims as a CSV file.
@claims_bp.route("/export", methods=["GET"])
@login_required
@role_required("treasurer")
def export_claims():

    statement = db.select(Claim).order_by(Claim.id)

    claims = db.session.execute(statement).scalars().all()

    output = io.StringIO(newline="")

    writer = csv.writer(output)

    writer.writerow(
        ["Claim ID", "Member", "Event", "Amount (RM)", "Status", "Submitted At", "Merchant"]
    )

    for claim in claims:
        writer.writerow(
            [
                claim.id,
                neutralize_csv_formula(claim.member.name),
                neutralize_csv_formula(claim.event.name),
                f"{claim.amount:.2f}",
                claim.status,
                format_malaysia_time(claim.created_at, "%Y-%m-%d %H:%M"),
                neutralize_csv_formula(claim.merchant or ""),
            ]
        )


    csv_content = "\ufeff" + output.getvalue()

    output.close()

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=claims_report.csv"},
    )

# GET/POST /claims/<id>/edit: edit a draft or rejected claim, then save or resubmit it.
@claims_bp.route("/<int:claim_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("member")
def edit_claim(claim_id):
    claim = db.session.get(Claim, claim_id)

    if claim is None:
        abort(404)

    if claim.member_id != current_user.id:
        abort(404)

    if claim.status not in {"draft", "rejected"}:
        abort(403)

    events = db.session.execute(
        db.select(Event)
        .where(db.or_(~Event.closure.has(), Event.id == claim.event_id))
        .order_by(Event.name)
    ).scalars().all()

    if request.method == "GET":
        return render_template(
            "edit_claim.html",
            claim=claim,
            events=events,
        )

    title = request.form.get("title", "").strip()
    merchant = request.form.get("merchant", "").strip()
    description = request.form.get("description", "").strip()
    amount_text = request.form.get("amount", "").strip()
    event_id_text = request.form.get("event_id", "").strip()
    action = request.form.get("action", "submit")

    if action not in {"draft", "submit"}:
        flash("Invalid claim action.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    if not title or not merchant or not amount_text or not event_id_text:
        flash("Please fill in all required fields.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    if len(title) > 150:
        flash("Claim title must not exceed 150 characters.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    if len(merchant) > 150:
        flash("Merchant must not exceed 150 characters.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    try:
        amount = Decimal(amount_text)
    except InvalidOperation:
        flash("Invalid claim amount.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        flash(
            "Amount must be positive and have at most 2 decimal places.",
            "danger",
        )
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    if amount > Decimal("99999999.99"):
        flash("Claim amount exceeds the allowed limit.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    try:
        event_id = int(event_id_text)
    except ValueError:
        flash("Invalid event selection.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    event = db.session.get(Event, event_id)
    if event is None:
        flash("Selected event does not exist.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    if event.is_closed and (action == "submit" or event.id != claim.event_id):
        flash("This event is closed for new claims. You can only save the claim as a draft.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    file = request.files.get("receipt")
    has_file = file is not None and bool(file.filename)

    if action == "submit" and not has_file and not claim.receipts:
        flash("Please upload a receipt before submitting the claim.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    stored_filename = None
    new_receipt = None

    if has_file:
        try:
            original_filename, stored_filename = save_receipt(file)
        except ValueError as error:
            flash(str(error), "danger")
            return render_template(
                "edit_claim.html", claim=claim, events=events
            )

        new_receipt = Receipt(
            original_filename=original_filename,
            stored_filename=stored_filename,
        )

        file_bytes = read_stored_receipt(stored_filename)
        if file_bytes is not None:
            new_receipt.file = ReceiptFile(data=file_bytes)

    claim.title = title
    claim.merchant = merchant
    claim.description = description
    claim.amount = amount
    claim.event_id = event.id

    if new_receipt is not None:
        claim.receipts.append(new_receipt)

    previous_status = claim.status

    if action == "draft":
        claim.status = "draft"
        message = "Draft updated."
    else:
        claim.status = "pending"
        message = "Claim resubmitted for review."

        record_audit_log(
            user_id=current_user.id,
            action="resubmit_claim" if previous_status == "rejected" else "submit_claim",
            target_type="Claim",
            target_id=claim.id,
            details="Claim resubmitted for review." if previous_status == "rejected" else "Claim submitted for review.",
        )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

        if stored_filename is not None:
            delete_stored_receipt(stored_filename)

        raise

    if claim.status == "pending":
        notify_claim_submitted(claim, resubmitted=previous_status == "rejected")

    flash(message, "success")
    return redirect(
        url_for("claims.claim_details", claim_id=claim.id)
    )

# Build the file response for a receipt: from disk if present, otherwise from the database.
def build_receipt_response(receipt, as_attachment):

    upload_folder = receipt_upload_folder()
    
    if (upload_folder / receipt.stored_filename).is_file():
        response = send_from_directory(
            directory=upload_folder,
            path=receipt.stored_filename,
            as_attachment=as_attachment,
            download_name=receipt.original_filename,
        )

    elif receipt.file is not None:
        response = send_file(
            io.BytesIO(receipt.file.data),
            mimetype=receipt_mime_type(receipt.stored_filename),
            as_attachment=as_attachment,
            download_name=receipt.original_filename,
        )

    else:
        abort(404)

    response.headers["Cache-Control"] = "private, no-store"

    response.headers["X-Content-Type-Options"] = "nosniff"

    return response


# POST /claims/<id>/delete: delete the member's own draft claim.
@claims_bp.route("/<int:claim_id>/delete", methods=["POST"])
@login_required
@role_required("member")
def delete_draft_claim(claim_id):

    claim = db.session.get(Claim, claim_id)

    if claim is None or claim.member_id != current_user.id:
        abort(404)

    if claim.status != "draft":
        flash("Only draft claims can be deleted.", "danger")
        return redirect(url_for("claims.claim_details", claim_id=claim.id))

    stored_filenames = [receipt.stored_filename for receipt in claim.receipts]
    title = claim.title

    db.session.delete(claim)

    record_audit_log(
        user_id=current_user.id,
        action="delete_draft_claim",
        target_type="Claim",
        target_id=claim_id,
        details=f"Title: {title}; Receipts: {len(stored_filenames)}",
    )

    db.session.commit()

    for stored_filename in stored_filenames:
        delete_stored_receipt(stored_filename)

    flash("Draft claim deleted.", "success")
    return redirect(url_for("claims.my_claims"))


# POST /claims/receipts/<id>/delete: remove a receipt from the member's own draft or rejected claim.
@claims_bp.route("/receipts/<int:receipt_id>/delete", methods=["POST"])
@login_required
@role_required("member")
def delete_receipt(receipt_id):

    receipt = db.session.get(Receipt, receipt_id)

    if receipt is None or receipt.claim.member_id != current_user.id:
        abort(404)

    claim = receipt.claim

    if claim.status not in {"draft", "rejected"}:
        flash("Receipts can only be removed from draft or rejected claims.", "danger")
        return redirect(url_for("claims.claim_details", claim_id=claim.id))

    stored_filename = receipt.stored_filename
    original_filename = receipt.original_filename

    db.session.delete(receipt)

    record_audit_log(
        user_id=current_user.id,
        action="delete_receipt",
        target_type="Claim",
        target_id=claim.id,
        details=f"Receipt: {original_filename}",
    )

    db.session.commit()

    delete_stored_receipt(stored_filename)

    flash(f"Receipt {original_filename} removed.", "success")
    return redirect(url_for("claims.claim_details", claim_id=claim.id))
