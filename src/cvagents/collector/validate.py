"""Kiểm tra định dạng và tính hợp lệ của tệp CV trước khi chuyển cho CV_Extractor (hình 2.4: "kiểm tra định dạng").

Định dạng xác định theo chữ ký đầu tệp (magic bytes), không tin vào đuôi tên tệp.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from cvagents.common.config import load_config

SUFFIX = {"pdf": ".pdf", "docx": ".docx", "png": ".png", "jpg": ".jpg"}
_ALIAS = {".jpeg": "jpg", ".jpg": "jpg", ".png": "png", ".pdf": "pdf", ".docx": "docx"}
_PAGE_RE = re.compile(rb"/Type\s*/Page(?![a-zA-Z])")
_ENCRYPT_RE = re.compile(rb"/Encrypt\s*(?:\d+\s+\d+\s+R|<<)")


@dataclass
class Validation:
    ok: bool
    kind: str | None          # pdf | docx | png | jpg | zip | doc | … ; None nếu không nhận ra
    reason: str = ""          # lý do loại, hoặc ghi chú khi vẫn hợp lệ (đổi đuôi)
    so_trang: int | None = None


def sniff(data: bytes) -> str | None:
    """Định dạng thật của tệp theo chữ ký đầu tệp."""
    head = data[:8]
    if data[:1024].lstrip().startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "doc"  # OLE2: Word 97–2003 (.doc), cũng có thể là .xls
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return "zip"
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        if "ppt/presentation.xml" in names:
            return "pptx"
        return "zip"
    if head.startswith((b"GIF8", b"RIFF", b"II*\x00", b"MM\x00*")):
        return "anh_khac"
    return None


def pdf_pages(data: bytes) -> int:
    """Đếm số trang PDF thô (đối tượng /Type /Page). Đủ để loại tệp quá dài, không cần thư viện PDF."""
    return len(_PAGE_RE.findall(data))


def zip_too_big(data: bytes, cfg: dict) -> str:
    """Rỗng nếu an toàn; ngược lại là lý do (zip bomb, quá nhiều tệp, tệp hỏng)."""
    try:
        infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except zipfile.BadZipFile:
        return "Tệp nén bị hỏng"
    if len(infos) > cfg["zip_max_tep"]:
        return f"Tệp nén có quá nhiều tệp ({len(infos)} > {cfg['zip_max_tep']})"
    total = sum(i.file_size for i in infos)
    if total > cfg["zip_max_mb"] * 2**20:
        return f"Dung lượng giải nén {total / 2**20:.0f} MB vượt {cfg['zip_max_mb']} MB (nghi zip bomb)"
    return ""


def validate(name: str, data: bytes, cfg: dict | None = None) -> Validation:
    cfg = cfg or load_config()["collector"]
    kind = sniff(data)
    size = len(data)
    if size < cfg["kich_thuoc_min_byte"]:
        return Validation(False, kind, f"Tệp quá nhỏ ({size} byte), có thể rỗng hoặc tải lỗi")
    if size > cfg["kich_thuoc_max_mb"] * 2**20:
        return Validation(False, kind, f"Tệp quá lớn ({size / 2**20:.1f} MB > {cfg['kich_thuoc_max_mb']} MB)")
    if kind is None:
        return Validation(False, None, "Không nhận dạng được định dạng tệp (không phải PDF, DOCX hay ảnh)")
    if kind == "doc":
        return Validation(False, kind, "Định dạng .doc cũ (Word 97–2003): cần lưu lại thành .docx hoặc PDF")
    if kind not in cfg["dinh_dang"]:
        return Validation(False, kind, f"Định dạng {kind} không được hỗ trợ (chỉ nhận {', '.join(cfg['dinh_dang'])})")

    pages = None
    if kind == "pdf":
        tail = data[-2048:]
        if b"%%EOF" not in tail:
            return Validation(False, kind, "PDF bị hỏng hoặc tải chưa hết (thiếu %%EOF)")
        if _ENCRYPT_RE.search(data):
            return Validation(False, kind, "PDF có mật khẩu / mã hoá, không đọc được nội dung")
        pages = pdf_pages(data) or None
        if pages and pages > cfg["so_trang_max"]:
            return Validation(False, kind, f"PDF có {pages} trang (> {cfg['so_trang_max']}), có thể không phải CV",
                              pages)
    elif kind == "docx":
        why = zip_too_big(data, cfg)
        if why:
            return Validation(False, kind, why)
        try:
            zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
        except (zipfile.BadZipFile, KeyError, OSError) as e:
            return Validation(False, kind, f"DOCX bị hỏng ({e})")
    else:  # ảnh
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except Exception as e:  # Pillow ném nhiều loại lỗi khác nhau với ảnh hỏng
            return Validation(False, kind, f"Ảnh bị hỏng ({type(e).__name__})")
        if min(w, h) < cfg["anh_canh_min"]:
            return Validation(False, kind, f"Ảnh quá nhỏ ({w}×{h} px), OCR không đọc được")
        pages = 1

    note = ""
    ext = Path(name).suffix.lower()
    if _ALIAS.get(ext) != kind:
        note = f"Đuôi tệp '{ext or '(không có)'}' nhưng nội dung là {kind}; đã đổi thành {SUFFIX[kind]}"
    return Validation(True, kind, note, pages)
