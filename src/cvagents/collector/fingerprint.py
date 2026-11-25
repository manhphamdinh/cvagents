"""Vân tay nội dung để loại trùng ba mức.

    Mức 1  trùng byte          khoá băm SHA-1 của tệp (screening.content_key)
    Mức 2  gần trùng           simhash 64 bit của văn bản (PDF xuất lại, DOCX lưu lại) hoặc dHash của ảnh (ảnh nén lại)
    Mức 3  cùng ứng viên       email / số điện thoại đọc được trong CV, chỉ đánh dấu chứ không loại
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile

from cvagents.common.text import norm_key
from cvagents.extractor import entities as ent

_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"\w+")


def text_of(kind: str, data: bytes) -> str:
    """Văn bản thô của tệp, đọc nhanh (không qua Docling). Rỗng nếu không đọc được (PDF quét, thiếu thư viện)."""
    try:
        if kind == "docx":
            xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8", "replace")
            return _TAG_RE.sub(" ", xml.replace("</w:p>", "\n"))
        if kind == "pdf":
            return _pdf_text(data)
    except Exception:  # tệp đã qua kiểm tra định dạng; lỗi ở đây chỉ làm mất vân tay, không làm hỏng việc thu thập
        return ""
    return ""


def _pdf_text(data: bytes) -> str:
    try:
        import pypdfium2 as pdfium  # đi kèm docling
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    pdf = pdfium.PdfDocument(data)
    try:
        return "\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    finally:
        pdf.close()


def _h64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(text: str) -> int | None:
    """Simhash 64 bit trên các bộ ba từ liên tiếp (đã bỏ dấu, viết thường)."""
    words = _WORD_RE.findall(norm_key(text))
    if len(words) < 3:
        return None
    v = [0] * 64
    for i in range(len(words) - 2):
        h = _h64(" ".join(words[i:i + 3]))
        for b in range(64):
            v[b] += 1 if h >> b & 1 else -1
    return sum(1 << b for b in range(64) if v[b] > 0)


def dhash(data: bytes) -> int | None:
    """Difference hash 64 bit của ảnh: bền với nén lại, đổi kích thước, đổi định dạng."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            px = im.convert("L").resize((9, 8)).tobytes()
    except Exception:
        return None
    return sum(1 << (r * 8 + c) for r in range(8) for c in range(8) if px[r * 9 + c] > px[r * 9 + c + 1])


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def fingerprint(kind: str, data: bytes, text: str, min_words: int) -> tuple[str, int] | None:
    """("text", simhash) cho tệp có lớp văn bản đủ dài, ("image", dhash) cho ảnh; None nếu không lấy được."""
    if kind in ("png", "jpg"):
        h = dhash(data)
        return ("image", h) if h is not None else None
    if len(_WORD_RE.findall(text)) < min_words:
        return None
    h = simhash(text)
    return ("text", h) if h is not None else None


def identity(text: str) -> tuple[str | None, str | None]:
    """(email, SĐT dạng +84…) của ứng viên trong văn bản CV."""
    email = ent.find_email(text)
    return (email.lower() if email else None), ent.find_phone(text)
