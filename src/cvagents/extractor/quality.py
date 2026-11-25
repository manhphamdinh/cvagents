"""Bước 1: kiểm tra chất lượng lớp văn bản, công thức (2.2)–(2.5)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from cvagents.common.config import load_config, load_resource
from cvagents.common.text import nfc, norm_key

_VI_LOWER = (
    "aàáảãạăằắẳẵặâầấẩẫậeèéẻẽẹêềếểễệiìíỉĩịoòóỏõọôồốổỗộơờớởỡợuùúủũụưừứửữựyỳýỷỹỵđ"
    "bcdfghjklmnpqrstvwxz"
)
_LETTERS = set(_VI_LOWER) | set(_VI_LOWER.upper())
_PUNCT = set(".,;:!?()[]{}<>'\"“”‘’-–—_/\\|@#+*&%$=~^•·…")

# âm tiết tiếng Việt: phụ âm đầu + nguyên âm + phụ âm cuối (bỏ dấu để kiểm tra cấu trúc)
_SYLLABLE = re.compile(
    r"^(ngh|ng|nh|ch|gh|gi|kh|ph|th|tr|qu|[bcdghklmnpqrstvx]|)[aeiouy]+(ch|ng|nh|c|m|n|p|t|)$"
)
_GLYPH = re.compile(r"GLYPH<[^>]*>")


def letter_count(text: str) -> int:
    return sum(1 for c in text if c.isalpha())


def char_density(text: str, n_pages: int) -> float:
    """ρ = N_chữ / N_trang  (2.2)."""
    return letter_count(text) / max(n_pages, 1)


def odd_char_ratio(text: str) -> float:
    """r_lạ (2.3): tỷ lệ ký tự ngoài Σ trên T′ (đã bỏ khoảng trắng); GLYPH<…> tính là ký tự lạ."""
    glyph_chars = sum(len(m.group(0)) for m in _GLYPH.finditer(text))
    t = _GLYPH.sub("", re.sub(r"\s+", "", text))
    total = len(t) + glyph_chars
    if total == 0:
        return 0.0
    odd = glyph_chars + sum(1 for c in t if not (c in _LETTERS or c.isdigit() or c in _PUNCT))
    return odd / total


@lru_cache(maxsize=1)
def _vocab() -> frozenset[str]:
    words = {norm_key(w) for w in load_resource("english_words.json")}
    for canon, aliases in load_resource("skills.json").items():
        for s in [canon, *aliases]:
            words.update(norm_key(p) for p in s.split())
    return frozenset(words)


def _is_vi_syllable(w: str) -> bool:
    return bool(_SYLLABLE.match(norm_key(w)))


def dictionary_ratio(text: str) -> float:
    """r_td (2.4): tỷ lệ từ thuộc V = âm tiết tiếng Việt ∪ từ điển Anh ∪ từ điển kỹ năng."""
    words = []
    for tok in text.split():
        w = tok.strip(".,;:!?()[]{}<>'\"“”‘’-–—_/\\|•·…*").lower()
        if any(c.isalpha() for c in w):
            words.append(w)
    if not words:
        return 0.0
    vocab = _vocab()
    hit = sum(1 for w in words if norm_key(w) in vocab or _is_vi_syllable(w))
    return hit / len(words)


@dataclass
class QualityReport:
    rho: float
    r_la: float
    r_td: float
    need_ocr: bool


def assess(text: str, n_pages: int) -> QualityReport:
    """Điều kiện chuyển sang OCR toàn trang (2.5)."""
    cfg = load_config()["extractor"]
    text = nfc(text)
    rho, r_la, r_td = char_density(text, n_pages), odd_char_ratio(text), dictionary_ratio(text)
    need = rho < cfg["tau_rho"] or r_la > cfg["tau_la"] or r_td < cfg["tau_td"]
    return QualityReport(rho, r_la, r_td, need)
