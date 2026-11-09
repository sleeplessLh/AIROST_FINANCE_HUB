# ============================================================
# Claims Blueprint
# 负责处理 Claim Submission 相关的 HTTP Requests
# ============================================================

# Blueprint：将相关 Routes 组织到独立 Module
# render_template：渲染 HTML Template
# request：读取浏览器发送的 HTTP Request
# redirect：返回 HTTP Redirect Response
# url_for：根据 Endpoint Name 生成 URL
# flash：保存一次性提示消息
# send_from_directory：从指定文件夹安全地发送文件
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
    Response,
    jsonify
)

# login_required：限制只有已登录用户才能访问
# current_user：取得当前登录用户的 ORM Object
from flask_login import login_required, current_user

# Decimal：处理财务金额
# InvalidOperation：捕获无效的十进制金额
from decimal import Decimal, InvalidOperation

# 导入 Database Object
from database import db

# 导入 Event 和 Claim Models
from models import Event, Claim, Receipt, Budget, Payment, AuditLog

# 导入之前创建的 Role-Based Access Control Decorator
from utils.decorators import role_required

# ============================================================
# Claims Blueprint
# 负责处理 Claim Submission 相关的 HTTP Requests
# ============================================================

# login_required：限制只有已登录用户才能访问
# current_user：取得当前登录用户的 ORM Object
from pathlib import Path

from utils.file_upload import save_receipt

# 导入 Budget Calculation Function
from utils.budget import calculate_event_budget

from datetime import datetime, timezone

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
    ReceiptAnalysisError
)



# 创建 Claims Blueprint
# "claims"：Blueprint Name，用于 url_for("claims.submit_claim")
# __name__：当前 Python Module Name
# url_prefix="/claims"：此 Blueprint 的所有 Routes 都以 /claims 开头
claims_bp = Blueprint("claims", __name__, url_prefix="/claims")

@claims_bp.route("/analyse-receipt", methods=["POST"])
@login_required
@role_required("member")
def analyse_receipt():
    # 从 multipart/form-data 请求中取得名为 receipt 的上传文件
    receipt = request.files.get("receipt")

    # 确认用户确实选择了文件
    if receipt is None or not receipt.filename:
        return jsonify(error="Choose a receipt file first."), 400

    # 根据文件名取得扩展名，例如 ".jpg" 或 ".pdf"
    extension = Path(receipt.filename).suffix.lower()

    # 只接受项目当前允许的收据扩展名
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify(error="Only JPG, PNG and PDF files are allowed."), 400

    # 最多读取最大限制加 1 Byte，以便识别超限文件
    file_bytes = receipt.stream.read(MAX_RECEIPT_SIZE + 1)

    # 拒绝空文件
    if not file_bytes:
        return jsonify(error="The selected receipt is empty."), 400

    # 检查文件大小
    if len(file_bytes) > MAX_RECEIPT_SIZE:
        return jsonify(error="Receipt file must not exceed 5 MB."), 400

    # 读取文件实际内容，判断它是真正的 JPG、PNG 还是 PDF
    detected_type = detect_file_type(file_bytes)

    if detected_type is None:
        return jsonify(error="Unsupported or invalid receipt file."), 400

    # 确认文件名扩展名与实际内容相符
    expected_type = "jpg" if extension in {".jpg", ".jpeg"} else extension[1:]

    if detected_type != expected_type:
        return jsonify(error="File extension does not match file content."), 400

    # 根据检测出的真实文件类型，确定发给 Gemini 的 MIME Type
    mime_types = {
        "jpg": "image/jpeg",
        "png": "image/png",
        "pdf": "application/pdf",
    }
    mime_type = mime_types[detected_type]

    try:
        # 调用 utils/receipt_ai.py 中的识别函数
        extracted_data = extract_receipt_data(file_bytes, mime_type)

        # 把 Python 字典转换为 JSON Response，供网页 JavaScript 使用
        return jsonify(extracted_data)

    except ReceiptAnalysisError as error:
        # 没有配置 API Key 时，返回 503 Service Unavailable
        if not os.getenv("GEMINI_API_KEY", "").strip():
            return jsonify(error=str(error)), 503

        # Gemini 无法识别或返回异常时，返回 502 Bad Gateway
        return jsonify(error=str(error)), 502


# ============================================================
# Submit Claim
# GET：显示 Claim Submission Form
# POST：处理 Member 提交的 Form Data
# ============================================================
@claims_bp.route("/submit", methods=["GET", "POST"])
@login_required
@role_required("member")
def submit_claim():
    # 查询所有活动，供 Member 在表单中选择
    events = db.session.execute(db.select(Event).order_by(Event.name)).scalars().all()

    if request.method == "POST":
        # 读取表单字段
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        amount_text = request.form.get("amount", "").strip()
        event_id_text = request.form.get("event_id", "").strip()
        action = request.form.get("action", "submit")

        # 只允许保存草稿或提交审核
        if action not in {"draft", "submit"}:
            flash("Invalid claim action.", "danger")
            return render_template("submit_claim.html", events=events)

        # 两种操作都需要基本报销信息
        if not title or not amount_text or not event_id_text:
            flash("Please fill in all required fields.", "danger")
            return render_template("submit_claim.html", events=events)

        # 检查标题长度
        if len(title) > 150:
            flash("Claim title must not exceed 150 characters.", "danger")
            return render_template("submit_claim.html", events=events)

        # 将金额字符串转换为 Decimal
        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            flash("Invalid claim amount.", "danger")
            return render_template("submit_claim.html", events=events)

        # 金额必须是有限的正数，且最多两位小数
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            flash(
                "Amount must be positive and have at most 2 decimal places.",
                "danger",
            )
            return render_template("submit_claim.html", events=events)

        # 检查金额是否超过数据库字段上限
        if amount > Decimal("99999999.99"):
            flash("Claim amount exceeds the allowed limit.", "danger")
            return render_template("submit_claim.html", events=events)

        # 将 Event ID 转换为整数
        try:
            event_id = int(event_id_text)
        except ValueError:
            flash("Invalid event selection.", "danger")
            return render_template("submit_claim.html", events=events)

        # 确认用户选择的活动存在
        event = db.session.get(Event, event_id)
        if event is None:
            flash("Selected event does not exist.", "danger")
            return render_template("submit_claim.html", events=events)

        # 获取用户上传的收据
        file = request.files.get("receipt")
        has_file = file is not None and bool(file.filename)

        # 正式提交审核时必须上传收据
        if action == "submit" and not has_file:
            flash("Please upload a receipt before submitting the claim.", "danger")
            return render_template("submit_claim.html", events=events)

        # 初始化收据资料；草稿可以暂时没有收据
        original_filename = None
        stored_filename = None

        # 如果用户选择了文件，就验证并保存它
        if has_file:
            try:
                original_filename, stored_filename = save_receipt(file)
            except ValueError as error:
                flash(str(error), "danger")
                return render_template("submit_claim.html", events=events)

        # 根据按钮操作决定报销单状态
        claim_status = "draft" if action == "draft" else "pending"

        # 创建 Claim ORM Object
        claim = Claim(
            title=title,
            description=description,
            amount=amount,
            status=claim_status,
            member_id=current_user.id,
            event_id=event.id,
        )

        # 只有上传了收据，才创建对应的 Receipt ORM Object
        if original_filename is not None and stored_filename is not None:
            receipt = Receipt(
                original_filename=original_filename,
                stored_filename=stored_filename,
            )

            # 通过 Relationship 将 Receipt 关联到 Claim
            claim.receipts.append(receipt)

        # 将 Claim 和 Receipt 一起写入数据库
        try:
            db.session.add(claim)
            db.session.commit()
        except Exception:
            # 数据库保存失败时，撤销本次事务
            db.session.rollback()

            # 删除刚才保存到磁盘的文件，避免留下孤立收据
            if stored_filename is not None:
                file_path = (
                    Path(current_app.instance_path)
                    / "uploads"
                    / "receipts"
                    / stored_filename
                )
                file_path.unlink(missing_ok=True)

            # 继续抛出原始异常，让应用错误处理器接手
            raise

        # 根据用户选择显示不同成功消息
        if action == "draft":
            flash("Claim saved as draft.", "success")
        else:
            flash("Claim submitted for review.", "success")

        # 保存成功后进入报销详情页
        return redirect(url_for("claims.claim_details", claim_id=claim.id))

    # GET 请求：显示新建报销表单
    return render_template("submit_claim.html", events=events)


# ============================================================
# My Claims Page
# GET /claims/my
# 只显示当前登录 Member 提交的 Claims
# ============================================================
@claims_bp.route("/my", methods=["GET"])
@login_required
@role_required("member")
def my_claims():

    # current_user.id：取得当前登录 Member 的 Primary Key
    member_id = current_user.id

    # db.select(Claim)：创建 SELECT Query，查询 Claim Model
    # where()：只选择 member_id 等于当前登录用户 ID 的 Claims
    # order_by()：根据 created_at 降序排列，最新的 Claim 排在前面
    statement = (
        db.select(Claim)
        .where(Claim.member_id == member_id)
        .order_by(Claim.created_at.desc(), Claim.id.desc())
    )

    # db.session.execute()：执行 SQL Query
    # scalars()：从查询结果中提取 Claim ORM Objects
    # all()：将所有 Claim Objects 放入 Python List
    claims = db.session.execute(statement).scalars().all()

    # render_template()：渲染 HTML Template
    # claims=claims：将 Python List 传递给 Jinja2 Template
    return render_template("my_claims.html", claims=claims)

@claims_bp.route("/<int:claim_id>", methods=["GET"])
# 假设用户访问：
# /claims/3
# Flask 会自动将：
# claim_id = 3
# 传递给：
# def claim_details(claim_id):
# 所以这里的 claim_id 是一个 Function Parameter（形参），它的值来自 URL。
@login_required
@role_required("member")
def claim_details(claim_id):
    claim = db.session.get(Claim, claim_id)

    if claim is None:
        abort(404)

    if claim.member_id != current_user.id:
        abort(404)

    can_edit = claim.status in {'draft', 'rejected'}

    # 查找这条报销单最近一次被拒绝的记录
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
    rejection_reason = rejection_log.details if rejection_log else None
    ) 

# ============================================================
# Receipt View Route
# GET /claims/receipts/<int:receipt_id>/view
# 功能：允许 Receipt Owner 或 Treasurer 查看 Receipt
# ============================================================


@claims_bp.route("/receipts/<int:receipt_id>/view", methods=["GET"])
@login_required
def view_receipt(receipt_id):

    # 根据 Receipt ID 查询对应的 Receipt ORM Object
    # 如果找不到，则返回 None
    receipt = db.session.get(Receipt, receipt_id)

    # 如果 Receipt 不存在，返回 HTTP 404
    if receipt is None:
        abort(404)

    # 通过 ORM Relationship 取得 Receipt 所属的 Claim
    claim = receipt.claim

    # Member：只能查看自己提交的 Claim 所关联的 Receipt
    # Treasurer：可以查看所有 Member 提交的 Receipt
    # 其他 Role：不允许访问
    if not (
        (current_user.role == "member" and claim.member_id == current_user.id)
        or current_user.role == "treasurer"
    ):
        abort(404)

    # 取得服务器保存 Receipt 的文件夹路径
    upload_folder = Path(current_app.instance_path) / "uploads" / "receipts"

    # 从指定文件夹发送 Receipt
    # directory：指定文件所在的文件夹路径
    # path：指定文件名，与 directory 组合形成完整文件路径
    # as_attachment=False：浏览器通常直接显示 JPG、PNG、PDF
    response = send_from_directory(
        directory=upload_folder, path=receipt.stored_filename, as_attachment=False
    )

    # 禁止浏览器和中间缓存长期保存敏感 Receipt
    response.headers["Cache-Control"] = "private, no-store"

    # 阻止浏览器自行猜测文件的 MIME Type
    response.headers["X-Content-Type-Options"] = "nosniff"

    # 返回包含 Receipt 文件内容的 HTTP Response
    return response


# ============================================================
# Receipt Download Route
# GET /claims/receipts/<int:receipt_id>/download
# 功能：允许 Receipt Owner 或 Treasurer 下载 Receipt
# ============================================================


@claims_bp.route("/receipts/<int:receipt_id>/download", methods=["GET"])
@login_required
def download_receipt(receipt_id):

    # 根据 Receipt ID 查询对应的 Receipt ORM Object
    receipt = db.session.get(Receipt, receipt_id)

    # 如果 Receipt 不存在，返回 HTTP 404
    if receipt is None:
        abort(404)

    # 通过 ORM Relationship 取得 Receipt 所属的 Claim
    claim = receipt.claim

    # 只允许 Receipt Owner 或 Treasurer 下载
    if not (
        (current_user.role == "member" and claim.member_id == current_user.id)
        or current_user.role == "treasurer"
    ):
        abort(404)

    # 取得 Receipt 文件夹路径
    upload_folder = Path(current_app.instance_path) / "uploads" / "receipts"

    # 从指定文件夹发送 Receipt
    # directory：文件所在的文件夹
    # path：服务器保存的唯一文件名
    # as_attachment=True：要求浏览器下载文件
    # download_name：下载时使用用户上传的原始文件名
    response = send_from_directory(
        directory=upload_folder,
        path=receipt.stored_filename,
        as_attachment=True,
        download_name=receipt.original_filename,
    )

    # 禁止缓存敏感 Receipt
    response.headers["Cache-Control"] = "private, no-store"

    # 阻止浏览器猜测 MIME Type
    response.headers["X-Content-Type-Options"] = "nosniff"

    # 返回包含 Receipt 文件内容的 HTTP Response
    return response


@claims_bp.route("/review", methods=["GET"])
@login_required
@role_required("treasurer")
def review_claims():
    statement = (db.select(Claim)
    .order_by(Claim.created_at.desc(), Claim.id.desc())
    )

    claims = db.session.execute(statement).scalars().all()

    return render_template("review_claims.html", claims=claims)


# ============================================================
# Treasurer Claim Details Route
# GET /claims/review/<int:claim_id>
# 功能：允许 Treasurer 查看指定 Claim 的完整资料
# ============================================================


@claims_bp.route("/review/<int:claim_id>", methods=["GET"])
@login_required
@role_required("treasurer")
def review_claim_details(claim_id):

    # 根据 Primary Key 查询 Claim ORM Object
    # 返回值：Claim Object；找不到则返回 None
    claim = db.session.get(Claim, claim_id)

    # Claim 不存在时返回 HTTP 404
    if claim is None:
        abort(404)

    # 通过 ORM Relationship 获取 Claim 对应的 Event
    event = claim.event

    # 计算 Event 的预算状况
    # 返回值：Dictionary，包含 allocated、committed、remaining 等
    budget_info = calculate_event_budget(event)

    # 检查当前 Pending Claim 是否超过剩余预算
    # 只有 Pending Claim 才需要判断能否批准
    is_over_budget = (
        claim.status == "pending" and claim.amount > budget_info["remaining"]
    )

    # 将 Claim、Budget Information、Validation Result 传给 HTML
    return render_template(
        "review_claim_details.html",
        claim=claim,
        budget_info=budget_info,
        is_over_budget=is_over_budget,
    )


@claims_bp.route("/review/<int:claim_id>/approve", methods=["POST"])
@login_required
@role_required("treasurer")
def approve_claim(claim_id):

    # 根据 Claim ID 查询 Database
    # db.session.get()：通过 Primary Key 获取 Claim ORM Object
    # 返回值：Claim Object；如果不存在则返回 None
    claim = db.session.get(Claim, claim_id)

    # 如果 Claim 不存在，返回 HTTP 404
    if claim is None:
        abort(404)

    # 只有 Pending Claim 才允许批准
    # 防止重复批准或修改已经处理的 Claim
    if claim.status != "pending":
        flash("Only pending claims can be approved.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # ============================================================
    # Budget Validation
    # ============================================================

    # 通过 ORM Relationship 获取当前 Claim 对应的 Event
    event = claim.event

    # 调用预算计算函数，获取当前 Event 的预算统计
    # 返回值：Dictionary，包含 allocated、committed、remaining 等
    budget_info = calculate_event_budget(event)

    # 从 Dictionary 取得剩余预算
    # remaining 通常是 Decimal，适合进行金额比较
    remaining_budget = budget_info["remaining"]

    # 检查 Claim Amount 是否超过 Event Remaining Budget
    if claim.amount > remaining_budget:
        # 显示预算不足的错误消息
        flash(
            f"Insufficient event budget. "
            f"Remaining: RM {remaining_budget:.2f}, "
            f"Claim Amount: RM {claim.amount:.2f}.",
            "danger",
        )

        # 预算不足时直接返回，不修改 Claim Status
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # ============================================================
    # Approve Claim
    # ============================================================

    # 预算足够，修改 Claim Status
    claim.status = "approved"

    # 记录 Treasurer 批准 Claim 的操作
    record_audit_log(
        user_id=current_user.id,       # 当前登录的 Treasurer ID
        action="approve_claim",        # 操作类型：批准 Claim
        target_type="Claim",           # 被操作的 ORM Model 类型
        target_id=claim.id,            # 被批准的 Claim ID
        details="Claim approved by treasurer."
    )

    # 提交 Database Transaction
    db.session.commit()

    # 显示成功消息
    flash("Claim approved successfully.", "success")

    # 返回 Treasurer Claim Details 页面
    return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


# ============================================================
# Treasurer Reject Claim Route
# POST /claims/review/<int:claim_id>/reject
# 功能：允许 Treasurer 拒绝 Pending Claim
# ============================================================


@claims_bp.route("/review/<int:claim_id>/reject", methods=["POST"])
@login_required
@role_required("treasurer")
def reject_claim(claim_id):

    # 根据 Primary Key 查询指定的 Claim Object
    claim = db.session.get(Claim, claim_id)

    # 如果 Claim 不存在，返回 HTTP 404
    if claim is None:
        abort(404)

    # 检查 Claim 是否仍处于 Pending 状态
    if claim.status != "pending":
        flash("This claim has already been reviewed.", "warning")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    rejection_reason = request.form.get("rejection_reason","").strip()

    if not rejection_reason:
        flash("Please provide a reason for rejecting this claim.", "danger")
        return redirect(
            url_for("claims.review_claim_details", claim_id = claim_id)
        )

    # 修改 Claim ORM Object 的 status Attribute
    claim.status = "rejected"

    rejection_reason = request.form.get("rejection_reason", "").strip()

    if not rejection_reason:
        flash("Please provide a reason for rejecting this claim.", "danger")
        return redirect(
            url_for("claims.review_claim_details", claim_id=claim.id)
        )

    # 记录 Treasurer 拒绝 Claim 的操作
    record_audit_log(
        user_id=current_user.id,       # 当前执行操作的 Treasurer ID
        action="reject_claim",         # 操作类型：拒绝 Claim
        target_type="Claim",           # 被操作的资源类型
        target_id=claim.id,            # 被拒绝的 Claim ID
        details=rejection_reason
    )

    # 将修改后的 Claim Status 保存到 SQLite Database
    db.session.commit()

    # 显示审核成功的信息
    flash("Claim rejected successfully!", "success")

    # 返回 Treasurer Claim Details Page
    return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


# ============================================================
# Treasurer Budget Management Route
# GET /claims/budgets
# 功能：查询所有 Events，并计算每个 Event 的 Budget Information
# ============================================================
@claims_bp.route("/budgets", methods=["GET"])
@login_required
@role_required("treasurer")
def manage_budgets():

    # 创建 SQL Statement：查询所有 Events，按照名称升序排列
    statement = db.select(Event).order_by(Event.name)

    # 执行 SQL Statement，取得 Event ORM Objects List
    events = db.session.execute(statement).scalars().all()

    # 创建空 List，用来保存每个 Event 的预算计算结果
    budget_rows = []

    # 遍历每一个 Event ORM Object
    for event in events:
        # 调用之前创建的 Budget Calculation Function
        # 返回 Dictionary：allocated、committed、remaining、utilization_percentage
        budget_info = calculate_event_budget(event)

        # 将 Event Object 和 Budget Dictionary 组合成一个 Dictionary
        row = {"event": event, "budget": budget_info}

        # 将当前 Event 的预算信息加入 List
        budget_rows.append(row)

    # 渲染 Budget Management Template
    # budget_rows：传递所有 Event 的预算计算结果
    return render_template("manage_budgets.html", budget_rows=budget_rows)


# ============================================================
# Treasurer Update Budget Route
# POST /claims/budgets/<int:event_id>/update
# 功能：为 Event 创建 Budget 或修改现有 Budget
# ============================================================
@claims_bp.route("/budgets/<int:event_id>/update", methods=["POST"])
@login_required
@role_required("treasurer")
def update_budget(event_id):

    # ============================================================
    # 1. 查询 Event
    # ============================================================

    # 根据 Primary Key 查询 Event ORM Object
    # 返回值：Event Object；不存在则返回 None
    event = db.session.get(Event, event_id)

    # Event 不存在时返回 HTTP 404
    if event is None:
        abort(404)

    # ============================================================
    # 2. 读取并验证 New Allocated Budget
    # ============================================================

    # 从 HTML Form 读取 allocated_amount
    # 返回值：str；如果字段不存在则返回空字符串
    amount_str = request.form.get("allocated_amount", "").strip()

    # 将字符串转换成 Decimal
    try:
        new_allocated_amount = Decimal(amount_str)

    # 如果字符串无法转换成 Decimal，则拒绝更新
    except InvalidOperation:
        flash("Invalid budget amount.", "danger")
        return redirect(url_for("claims.manage_budgets"))

    # 确保金额为有限数值，且不能小于 0
    if not new_allocated_amount.is_finite() or new_allocated_amount < 0:
        flash("Budget amount must be a non-negative number.", "danger")
        return redirect(url_for("claims.manage_budgets"))

    # 金额最多保留两位小数
    if new_allocated_amount.as_tuple().exponent < -2:
        flash("Budget amount cannot exceed two decimal places.", "danger")
        return redirect(url_for("claims.manage_budgets"))

    # Numeric(10, 2)：最多 8 位整数和 2 位小数
    if new_allocated_amount >= Decimal("100000000"):
        flash("Budget amount is too large.", "danger")
        return redirect(url_for("claims.manage_budgets"))

    # ============================================================
    # 3. Budget Adjustment Validation
    # ============================================================

    # 计算当前 Event 的预算状况
    # 返回值：Dictionary，包含 allocated、committed、remaining 等
    budget_info = calculate_event_budget(event)

    # 取得当前 Event 已经 Committed 的金额
    # Committed 包含 Approved 和 Paid Claims
    committed_amount = budget_info["committed"]

    # 如果 New Allocated Budget 小于 Committed Amount
    # 则禁止修改 Budget
    if new_allocated_amount < committed_amount:

        # 显示错误消息
        flash(
            f"Budget adjustment rejected. "
            f"Committed amount: RM {committed_amount:.2f}. "
            f"New allocated budget cannot be lower than "
            f"the committed amount.",
            "danger"
        )

        # 直接返回 Budget Management 页面
        # 不修改 Database 中的 Allocated Budget
        return redirect(url_for("claims.manage_budgets"))

    # ============================================================
    # 4. Update Budget
    # ============================================================

    # 记录修改前的 Budget Amount
    # 如果 Event 尚未创建 Budget Record，则原金额为 0
    old_allocated_amount = (
        event.budget.allocated_amount
        if event.budget is not None
        else Decimal("0.00")
    )

    # 如果 Event 尚未存在 Budget Record，则创建
    if event.budget is None:

        # 创建新的 Budget ORM Object
        budget = Budget(
            event_id=event.id,
            allocated_amount=new_allocated_amount
        )

        # 将新 Budget Object 加入 Database Session
        db.session.add(budget)

    else:
        # 如果 Budget Record 已经存在，则更新金额
        event.budget.allocated_amount = new_allocated_amount

    # ============================================================
    # 5. Record Audit Log
    # ============================================================

    # 记录 Event Budget 的修改操作
    record_audit_log(
        user_id=current_user.id,          # 当前登录的 Treasurer ID
        action="update_budget",           # 操作类型：修改 Budget
        target_type="Budget",             # 被操作的资源类型
        target_id=event.id,               # 使用 Event ID 标识对应预算
        details=(
            f"Event ID: {event.id}; "
            f"Old Amount: RM {old_allocated_amount:.2f}; "
            f"New Amount: RM {new_allocated_amount:.2f}"
        )
    )

    # 同时提交 Budget Update 和 Audit Log
    db.session.commit()

    # 显示更新成功消息
    flash("Event budget updated successfully.", "success")

    # 返回 Budget Management 页面
    return redirect(url_for("claims.manage_budgets"))



@claims_bp.route("/review/<int:claim_id>/pay", methods=["POST"])
@login_required
@role_required("treasurer")
def mark_claim_paid(claim_id):

    # 根据 Claim ID 查询 Database
    # db.session.get()：根据 Primary Key 查询 ORM Object
    # 返回值：Claim Object；找不到则返回 None
    claim = db.session.get(Claim, claim_id)


    # 如果 Claim 不存在，返回 HTTP 404
    if claim is None:
        abort(404)

    # 只有 Approved Claim 才允许确认付款
    if claim.status != "approved":
        flash("Only approved claims can be marked as paid.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # 从 HTML Form 获取 Payment Method
    # request.form.get()：根据 name 属性读取表单数据
    # strip()：移除字符串前后的空白字符
    payment_method = request.form.get("payment_method", "").strip()

    # 从 HTML Form 获取 Reference Number
    reference_number = request.form.get("reference_number", "").strip()

    # 验证付款方式是否属于允许的选项
    if payment_method not in ("Bank Transfer", "Cash"):
        flash("Invalid payment method.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # 验证 Reference Number 长度
    if len(reference_number) > 100:
        flash("Reference number is too long.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # 检查当前 Claim 是否已经存在 Payment Record
    # 防止重复创建付款记录
    if claim.payment is not None:
        flash("A payment record already exists for this claim.", "danger")
        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # 创建 Payment ORM Object
    # 此时只是创建 Python Object，还没有写入 Database
    payment = Payment(
        claim_id=claim.id,
        amount=claim.amount,
        payment_method=payment_method,
        reference_number=reference_number or None,
        paid_at=datetime.now(timezone.utc).replace(tzinfo=None),  # noqa: UP017
        paid_by_id=current_user.id,
    )

    # 更新 Claim Status
    # SQLAlchemy 会追踪 ORM Object 的属性变化
    claim.status = "paid"

    # 将新 Payment Object 加入当前 Database Session
    db.session.add(payment)

    # 提交 Transaction
    # 同时保存 Claim Status 和 Payment Record
    try:
        # 同时提交 Claim Status 更新和 Payment Record
        db.session.commit()

    # IntegrityError：违反 Database Constraint
    # 例如 Payment.claim_id 的 Unique Constraint
    except IntegrityError:
        # 撤销本次 Transaction
        db.session.rollback()

        # 提示用户付款记录可能已存在
        flash(
            "Payment could not be saved because of a database constraint. "
            "Please refresh and verify the claim's payment status.",
            "danger",
        )

        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # SQLAlchemyError：处理其他 Database Errors
    except SQLAlchemyError:
        # 撤销本次 Transaction
        db.session.rollback()

        flash("Failed to save payment record.", "danger")

        return redirect(url_for("claims.review_claim_details", claim_id=claim.id))

    # 显示付款确认成功消息
    flash("Claim marked as paid successfully.", "success")

    # 返回 Treasurer Claim Details 页面
    return redirect(url_for("claims.review_claim_details", claim_id=claim.id))


# ============================================================
# Treasurer Export Claims Report
# GET /claims/export
# 功能：将所有 Claims 导出为 CSV 文件
# ============================================================


@claims_bp.route("/export", methods=["GET"])
@login_required
@role_required("treasurer")
def export_claims():

    # 创建 SQL Statement：查询所有 Claims
    # 按照 Claim ID 升序排列
    statement = db.select(Claim).order_by(Claim.id)

    # 执行 SQL Statement，取得 Claim ORM Objects List
    claims = db.session.execute(statement).scalars().all()

    # 创建内存中的文本缓冲区
    # newline=""：配合 csv.writer 避免多余空行
    output = io.StringIO(newline="")

    # 创建 CSV Writer
    # writerow()：负责将 List 写入 CSV 的一行
    # 先将内容写入output 缓冲区 然后之后再retrieve 写入csv
    writer = csv.writer(output)

    # 写入 CSV Header
    writer.writerow(
        ["Claim ID", "Member", "Event", "Amount (RM)", "Status", "Submitted At"]
    )

    # 遍历 Database 中的每个 Claim
    for claim in claims:
        # 写入当前 Claim 的一行数据
        writer.writerow(
            [
                claim.id,
                claim.member.name,
                claim.event.name,
                f"{claim.amount:.2f}",
                claim.status,
                claim.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )

    # output.getvalue()：取得 StringIO 缓冲区中保存的全部 CSV 文本
    # 返回值：str（字符串）

    # "\ufeff"：Unicode BOM（Byte Order Mark）的 Unicode 字符表示
    # 在 UTF-8 编码时，它对应 EF BB BF 三个字节
    # 作用：帮助 Microsoft Excel 将 CSV 文件识别为 UTF-8 编码
    # 避免中文等非 ASCII 字符出现乱码

    # +：字符串拼接运算符，将 BOM 添加到 CSV 文本最前面

    # csv_content：接收拼接后的完整 CSV 字符串
    # 重点： \ufeff 不属于 CSV 的数据内容，而是添加在文件开头的 Unicode BOM 字符，用来帮助 Excel 识别 UTF-8 编码。
    csv_content = "\ufeff" + output.getvalue()

    # 关闭内存缓冲区
    output.close()

    # 返回 CSV Response，让浏览器下载文件
    return Response(
        csv_content,
        # 指定 Response 的 MIME Type
        mimetype="text/csv",
        # Content-Disposition：要求浏览器下载附件
        headers={"Content-Disposition": "attachment; filename=claims_report.csv"},
    )

@claims_bp.route("/<int:claim_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("member")
def edit_claim(claim_id):
    # 根据 URL 中的 ID 查找报销单
    claim = db.session.get(Claim, claim_id)

    # 报销单不存在时返回 404
    if claim is None:
        abort(404)

    # 只能编辑自己名下的草稿或被拒申请
    if claim.member_id != current_user.id:
        abort(404)

    if claim.status not in {"draft", "rejected"}:
        abort(403)

    # 查询活动，供编辑表单选择
    events = db.session.execute(
        db.select(Event).order_by(Event.name)
    ).scalars().all()

    # GET：打开编辑页面
    if request.method == "GET":
        return render_template(
            "edit_claim.html",
            claim=claim,
            events=events,
        )

    # POST：接收编辑后的表单数据
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    amount_text = request.form.get("amount", "").strip()
    event_id_text = request.form.get("event_id", "").strip()
    action = request.form.get("action", "submit")

    # 只允许保存草稿或重新提交
    if action not in {"draft", "submit"}:
        flash("Invalid claim action.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    # 检查必填内容
    if not title or not amount_text or not event_id_text:
        flash("Please fill in all required fields.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    # 检查标题长度
    if len(title) > 150:
        flash("Claim title must not exceed 150 characters.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    # 转换并验证金额
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

    # 转换并确认所选活动存在
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

    # 正式提交或重新提交时必须已有收据
    file = request.files.get("receipt")
    has_file = file is not None and bool(file.filename)

    if action == "submit" and not has_file and not claim.receipts:
        flash("Please upload a receipt before submitting the claim.", "danger")
        return render_template(
            "edit_claim.html", claim=claim, events=events
        )

    # 如果用户上传了新收据，先验证并保存文件
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

    # 更新 Claim 字段
    claim.title = title
    claim.description = description
    claim.amount = amount
    claim.event_id = event.id

    # 如果上传了新收据，将它关联到报销单
    if new_receipt is not None:
        claim.receipts.append(new_receipt)

    # 按钮决定保存草稿，或重新进入审核队列
    if action == "draft":
        claim.status = "draft"
        message = "Draft updated."
    else:
        claim.status = "pending"
        message = "Claim resubmitted for review."

    # 保存修改；如果数据库失败，清理刚上传的新文件
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

        if stored_filename is not None:
            file_path = (
                Path(current_app.instance_path)
                / "uploads"
                / "receipts"
                / stored_filename
            )
            file_path.unlink(missing_ok=True)

        raise

    flash(message, "success")
    return redirect(
        url_for("claims.claim_details", claim_id=claim.id)
    )