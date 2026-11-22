from pathlib import Path

from uuid import uuid4

from flask import current_app

from werkzeug.utils import secure_filename

from werkzeug.datastructures import FileStorage


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}

MAX_RECEIPT_SIZE = 5 * 1024 * 1024


# Detect a file's real type from its content (JPG, PNG or PDF).
def detect_file_type(data: bytes):

    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    if data.startswith(b"%PDF-"):
        return "pdf"

    return None


# Check an uploaded JPG/PNG/PDF (name, size, real content); returns (original_filename, data, stored_filename).
def validate_upload(file: FileStorage, label="receipt"):

    if file is None or not file.filename:
        raise ValueError(f"Please select a {label} file.")

    original_filename = secure_filename(file.filename)

    if not original_filename:
        raise ValueError("Invalid filename.")

    extension = Path(original_filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Only JPG, PNG and PDF files are allowed.")

    data = file.stream.read(MAX_RECEIPT_SIZE + 1)

    if not data:
        raise ValueError(f"The {label} file is empty.")

    if len(data) > MAX_RECEIPT_SIZE:
        raise ValueError(f"{label.capitalize()} file must not exceed 5 MB.")

    detected_type = detect_file_type(data)

    if detected_type is None:
        raise ValueError(f"Unsupported or invalid {label} file.")

    expected_type = "jpg" if extension in {".jpg", ".jpeg"} else extension[1:]

    if detected_type != expected_type:
        raise ValueError("File extension does not match file content.")

    stored_extension = f".{detected_type}"

    stored_filename = f"{uuid4().hex}{stored_extension}"

    return original_filename, data, stored_filename


# Check an uploaded receipt and save it to disk; returns (original_filename, stored_filename).
def save_receipt(file: FileStorage):

    original_filename, data, stored_filename = validate_upload(file, "receipt")

    upload_folder = receipt_upload_folder()

    upload_folder.mkdir(parents=True, exist_ok=True)

    file_path = upload_folder / stored_filename

    with file_path.open("xb") as output:
        output.write(data)

    return original_filename, stored_filename


# Read a stored receipt from disk; returns None if the file is missing.
def read_stored_receipt(stored_filename: str):

    file_path = receipt_upload_folder() / stored_filename

    if not file_path.is_file():
        return None

    return file_path.read_bytes()


RECEIPT_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


# MIME type of a stored receipt, based on its file extension.
def receipt_mime_type(stored_filename: str):

    extension = Path(stored_filename).suffix.lower()

    return RECEIPT_MIME_TYPES.get(extension, "application/octet-stream")


# Delete a stored receipt file from disk (call after the database commit).
def delete_stored_receipt(stored_filename: str):

    file_path = receipt_upload_folder() / stored_filename

    file_path.unlink(missing_ok=True)

# Folder where receipt files are stored on disk.
def receipt_upload_folder():
    return Path(current_app.instance_path) / "uploads" / "receipts"
