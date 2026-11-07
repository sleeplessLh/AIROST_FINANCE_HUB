# ============================================================
# File Upload Utilities
# 负责验证收据文件、生成唯一文件名和保存文件
# ============================================================

# Path：安全地处理文件路径
from pathlib import Path

# uuid4：生成随机 UUID，避免文件名冲突
from uuid import uuid4

# current_app：取得当前 Flask Application
from flask import current_app

# secure_filename：清理用户提供的文件名
from werkzeug.utils import secure_filename

# FileStorage：Flask 接收到的 Uploaded File Object
from werkzeug.datastructures import FileStorage


# 允许上传的文件类型
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}

# 单个收据最大允许 5 MB
MAX_RECEIPT_SIZE = 5 * 1024 * 1024


# ============================================================
# 验证文件的实际内容
# ============================================================
def detect_file_type(data: bytes):

    # JPEG 文件通常以 FF D8 FF 开头
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"

    # PNG 文件具有固定的 8 Bytes Signature
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    # PDF 文件通常以 %PDF- 开头
    if data.startswith(b"%PDF-"):
        return "pdf"

    # 无法识别的文件
    return None


# ============================================================
# 保存上传的 Receipt
# Parameter：file 为 FileStorage Object
# Return：(original_filename, stored_filename)
# ============================================================
def save_receipt(file: FileStorage):

    # 检查是否真的有文件
    if file is None or not file.filename:
        raise ValueError("Please select a receipt file.")

    # 清理原始文件名
    original_filename = secure_filename(file.filename)

    # 清理后可能变成空字符串
    if not original_filename:
        raise ValueError("Invalid filename.")

    # 取得原始文件的 Extension，并转换为小写
    extension = Path(original_filename).suffix.lower()

    # 检查 Extension 是否允许
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Only JPG, PNG and PDF files are allowed.")

    # 最多读取 5 MB + 1 Byte，避免一次读取无限大的文件
    data = file.stream.read(MAX_RECEIPT_SIZE + 1)

    # 拒绝空文件
    if not data:
        raise ValueError("The receipt file is empty.")

    # 检查文件是否超过大小限制
    if len(data) > MAX_RECEIPT_SIZE:
        raise ValueError("Receipt file must not exceed 5 MB.")

    # 根据文件内容判断类型
    detected_type = detect_file_type(data)

    # 文件内容不符合支持的格式
    if detected_type is None:
        raise ValueError("Unsupported or invalid receipt file.")

    # 将 JPEG 的两种 Extension 视为同一类型
    expected_type = "jpg" if extension in {".jpg", ".jpeg"} else extension[1:]

    # 文件 Extension 必须与检测到的内容类型一致
    if detected_type != expected_type:
        raise ValueError("File extension does not match file content.")

    # 根据实际内容生成规范的 Extension
    stored_extension = f".{detected_type}"

    # 生成唯一文件名
    stored_filename = f"{uuid4().hex}{stored_extension}"

    # 将文件储存在 Flask Instance Folder 中
    upload_folder = (
        Path(current_app.instance_path) 
        / "uploads" 
        / "receipts"
    )
    # 如果文件夹不存在，自动创建
    upload_folder.mkdir(parents=True, exist_ok=True)

    # 组合完整的文件储存路径
    file_path = upload_folder / stored_filename

    # 将文件内容写入磁盘
    with file_path.open("xb") as output:
        output.write(data)

    # 返回原始文件名和服务器文件名
    return original_filename, stored_filename
