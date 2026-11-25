"""Tiện ích chuỗi dùng chung: chuẩn hoá Unicode, bỏ dấu."""
from __future__ import annotations

import re
import unicodedata


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return unicodedata.normalize("NFC", "".join(c for c in s if unicodedata.category(c) != "Mn"))


_VI_MARKED = frozenset(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
)


def detect_lang(text: str, min_ratio: float = 0.05) -> str:
    """"vi" nếu tỷ lệ chữ cái mang dấu tiếng Việt ≥ min_ratio, ngược lại "en".

    Văn bản tiếng Việt có dấu thường có 25–40% chữ cái mang dấu; một CV tiếng Anh chỉ có vài chữ có dấu ở họ tên.
    """
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return "vi"
    return "vi" if sum(c in _VI_MARKED for c in letters) / len(letters) >= min_ratio else "en"


def norm_key(s: str) -> str:
    """n(·) của công thức (2.8): viết thường, bỏ dấu, bỏ khoảng trắng thừa."""
    return re.sub(r"\s+", " ", strip_accents(s).lower()).strip()
