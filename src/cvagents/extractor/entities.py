"""Bước 6: trích xuất thực thể (regex, gazetteer + rapidfuzz, quy tắc)."""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from rapidfuzz import fuzz

from cvagents.common.config import load_config, load_resource
from cvagents.common.text import norm_key

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+84|0)(?:3|5|7|8|9)\d{8}")
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w.-]+", re.I)
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[\w]+\.)?linkedin\.com/in/[\w%.-]+", re.I)

_ORG_WORDS = re.compile(
    r"\b(c[oô]ng ty|tnhh|c[oổ] ph[aầ]n|t[aậ]p [dđ]o[aà]n|ng[aâ]n h[aà]ng|tr[uư][oờ]ng|[dđ][aạ]i h[oọ]c|"
    r"h[oọ]c vi[eệ]n|cao [dđ][aẳ]ng|co\.?|ltd|jsc|corp|inc|group|bank|university|college|institute|company)\b",
    re.I,
)

# ---------------------------------------------------------------- liên hệ
def find_email(text: str) -> str | None:
    m = EMAIL_RE.search(text)
    return m.group(0) if m else None


def find_phone(text: str) -> str | None:
    for m in re.finditer(r"[+\d][\d\s.\-()]{8,}\d", text):
        digits = re.sub(r"[\s.\-()]", "", m.group(0))
        pm = PHONE_RE.fullmatch(digits)
        if pm:
            d = pm.group(0)
            return "+84" + d[1:] if d.startswith("0") else d
    return None


def find_link(rx: re.Pattern, text: str) -> str | None:
    m = rx.search(text)
    return m.group(0) if m else None


def find_name(lines: list[str]) -> str | None:
    """Không có mô hình NER: lấy dòng đầu gồm 2–5 từ viết hoa chữ cái đầu, không chứa chữ số hay '@'."""
    for ln in lines:
        words = ln.split()
        if 2 <= len(words) <= 5 and not re.search(r"[\d@:/]", ln) and all(w[0].isupper() for w in words if w[0].isalpha()):
            return ln
    return None


def is_org(line: str) -> bool:
    return bool(_ORG_WORDS.search(line))


# ---------------------------------------------------------------- thời gian
_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_OPEN = r"nay|hi[eệ]n t[aạ]i|present|now|current"
_DATE = (
    r"(?:(?:th[aá]ng\s*)?(?P<m{n}>\d{{1,2}})\s*[/\-.]\s*(?P<y{n}>(?:19|20)\d{{2}})"
    r"|(?P<mn{n}>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s*(?P<yn{n}>(?:19|20)\d{{2}})"
    r"|(?P<yo{n}>(?:19|20)\d{{2}}))"
)
RANGE_RE = re.compile(
    rf"{_DATE.format(n=1)}\s*(?:-|–|—|đến|den|to)\s*(?:{_DATE.format(n=2)}|(?P<open>{_OPEN}))",
    re.I,
)


def month_index(y: int, q: int) -> int:
    """m(y, q) = 12y + q − 1  (2.9)."""
    return 12 * y + q - 1


def _end(m: re.Match, n: int, start: bool, today: date) -> int:
    g = m.groupdict()
    if g.get(f"y{n}"):
        return month_index(int(g[f"y{n}"]), int(g[f"m{n}"]))
    if g.get(f"yn{n}"):
        return month_index(int(g[f"yn{n}"]), _MON[g[f"mn{n}"].lower()[:3]])
    return month_index(int(g[f"yo{n}"]), 1 if start else 12)


def parse_range(text: str, today: date | None = None) -> tuple[int, int, str] | None:
    """Trả về (tháng bắt đầu, tháng kết thúc, chuỗi gốc) hoặc None."""
    today = today or date.today()
    m = RANGE_RE.search(text)
    if not m:
        return None
    start = _end(m, 1, True, today)
    end = month_index(today.year, today.month) if m.group("open") else _end(m, 2, False, today)
    if end < start:
        return None
    return start, end, m.group(0)


def total_months(intervals: list[tuple[int, int]]) -> int:
    """M = Σ (e_k − s_k + 1) trên hợp các khoảng rời nhau  (2.10)."""
    merged: list[list[int]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return sum(e - s + 1 for s, e in merged)


# ---------------------------------------------------------------- kỹ năng
@lru_cache(maxsize=1)
def gazetteer() -> dict[str, list[str]]:
    return {g: [g, *aliases] for g, aliases in load_resource("skills.json").items()}


def _protect(text: str) -> str:
    """Giữ nguyên các tên kỹ năng có ký tự phân tách (CI/CD, C/C++) trước khi tách cụm."""
    for forms in gazetteer().values():
        for a in forms:
            if "/" in a:
                text = re.sub(re.escape(a), a.replace("/", "\x00"), text, flags=re.I)
    return text


def split_skill_line(line: str) -> list[str]:
    """Tách thành tập cụm U; 'Python (DuckDB, Polars)' → Python, DuckDB, Polars."""
    if ":" in line[:40]:
        line = line.split(":", 1)[1]
    parts = re.split(r"[,;|/•()\[\]]", _protect(line))
    return [p.replace("\x00", "/").strip() for p in parts if p.strip()]


def canonical_skills(phrases: list[str]) -> list[str]:
    """K = {g ∈ G : ∃u ∈ U, a ∈ alias(g), sim(n(u), n(a)) ≥ τ_kn}  (2.8)."""
    cfg = load_config()["extractor"]
    tau, short = cfg["tau_skill"], cfg["min_alias_fuzzy"]
    found: dict[str, None] = {}
    for u in phrases:
        nu = norm_key(u)
        for g, forms in gazetteer().items():
            for a in forms:
                na = norm_key(a)
                ok = nu == na if len(na) < short else fuzz.ratio(nu, na) >= tau
                if ok:
                    found[g] = None
                    break
    return list(found)


# ---------------------------------------------------------------- học vấn
_DEGREES = [
    (5, ["tien si", "phd", "ph.d"]),
    (4, ["thac si", "master"]),
    (3, ["dai hoc", "cu nhan", "ky su", "bachelor", "engineer"]),
    (2, ["cao dang"]),
    (1, ["trung cap"]),
]


def degree_level(text: str) -> int | None:
    key = norm_key(text)
    for level, kws in _DEGREES:
        if any(re.search(rf"\b{re.escape(k)}\b", key) for k in kws):
            return level
    return None


GPA_RE = re.compile(
    r"(?:gpa|diem|cgpa)?\D{0,3}?(\d{1,2}(?:[.,]\d{1,2})?)\s*/\s*(4(?:[.,]0+)?|10(?:[.,]0+)?)\b", re.I
)
_GPA_BARE = re.compile(r"\bgpa\s*[:\-]?\s*(\d{1,2}(?:[.,]\d{1,2})?)\b", re.I)


def gpa_to_4(text: str) -> float | None:
    """gpa4 = gpa · 4 / thang, thang ∈ {4, 10}  (2.11)."""
    m = GPA_RE.search(text)
    if m:
        gpa, scale = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
    else:
        m = _GPA_BARE.search(text)
        if not m:
            return None
        gpa = float(m.group(1).replace(",", "."))
        scale = 10.0 if gpa > 4 else 4.0
    if gpa <= 0 or gpa > scale:
        return None
    return round(gpa * 4 / scale, 2)
