"""Phân tích văn bản JD thành các yêu cầu có cấu trúc."""
from __future__ import annotations

import re

from cvagents.common.schemas import JobDescription
from cvagents.common.text import norm_key
from cvagents.extractor import entities as ent

_NICE = re.compile(r"\b(uu tien|nice to have|loi the|la mot loi the|bonus|plus|preferred)\b")
_REQ_HEAD = re.compile(r"^(yeu cau|requirements?|qualifications?|bat buoc|must have)\b")
_BENEFIT_HEAD = re.compile(r"^(quyen loi|phuc loi|benefits?|mo ta cong viec|job description|responsibilities)\b")
_YEARS = re.compile(r"(\d+(?:[.,]\d+)?)\s*\+?\s*(?:-\s*\d+\s*)?(nam|years?|yrs?|thang|months?)", re.I)
_EDU_LINE = re.compile(r"(tot nghiep|bang cap|degree|dai hoc|cao dang|cu nhan|ky su|thac si|master|bachelor|phd|tien si)")
_OR = re.compile(r"\b(hoac|or)\b")
ALT = " hoặc "  # một yêu cầu có nhiều lựa chọn: "SQL Server hoặc PostgreSQL"
# bí danh trùng với từ thường ("go", "rest", chữ cái C/R …): chỉ khớp đúng chữ hoa / thường
_CASE_SENSITIVE = {"c", "r", "go", "rest", "ml"}


def scan_skills(text: str) -> list[str]:
    """Tìm kỹ năng chuẩn trong văn bản tự do bằng khớp từ nguyên vẹn trên bí danh, theo thứ tự xuất hiện. Bí danh dài
    được khớp trước và che phần đã khớp, nên "SQL Server" không đếm thêm "SQL", "React Native" không đếm "React"."""
    forms = sorted(((a, g) for g, fs in ent.gazetteer().items() for a in fs), key=lambda x: -len(x[0]))
    buf, first = text, {}
    for a, g in forms:
        pat = re.compile(rf"(?<![\w+#.]){re.escape(a)}(?![\w+#])", 0 if a.lower() in _CASE_SENSITIVE else re.I)
        for m in pat.finditer(buf):
            first[g] = min(first.get(g, m.start()), m.start())
        buf = pat.sub(lambda m: " " * len(m.group()), buf)
    return sorted(first, key=first.get)


def alternatives(req: str) -> list[str]:
    """Các lựa chọn của một yêu cầu kỹ năng ("SQL Server hoặc PostgreSQL" → hai kỹ năng)."""
    return req.split(ALT)


def _line_skills(line: str) -> list[str]:
    """Kỹ năng của một dòng yêu cầu; các kỹ năng nối bằng "hoặc" / "or" trong cùng một vế gộp thành một yêu cầu."""
    out: list[str] = []
    for seg in re.split(r"[,;]", line):
        skills = scan_skills(seg)
        if len(skills) > 1 and _OR.search(norm_key(seg)):
            out.append(ALT.join(skills))
        else:
            out += skills
    return out


def parse_jd(text: str, title: str = "") -> JobDescription:
    jd = JobDescription(tieu_de=title, van_ban_goc=text)
    must: dict[str, None] = {}
    nice: dict[str, None] = {}
    other: list[str] = []
    months = 0
    level: int | None = None
    in_nice = False
    skip = False  # đang ở khối quyền lợi / mô tả công việc
    seen_heading = False

    for raw in text.splitlines():
        line = raw.strip(" \t•-*▪●")
        if not line:
            continue
        key = norm_key(line)
        short = len(key.split()) <= 5
        if not seen_heading and not title and not jd.tieu_de and len(key.split()) <= 8 and not _REQ_HEAD.match(key):
            jd.tieu_de = line  # dòng đầu, trước mọi tiêu đề mục, là tên vị trí
            continue
        if short and _BENEFIT_HEAD.match(key):
            seen_heading = skip = True
            continue
        if short and (_REQ_HEAD.match(key) or key.rstrip(":") in ("nice to have", "uu tien")):
            seen_heading, skip = True, False
            in_nice = bool(_NICE.search(key))
            continue
        if skip:
            continue

        line_nice = in_nice or bool(_NICE.search(key))
        skills = _line_skills(line)
        for g in skills:
            if line_nice:
                nice.setdefault(g)
            else:
                must.setdefault(g)

        for m in _YEARS.finditer(key):
            n = float(m.group(1).replace(",", "."))
            if "nam" in m.group(2) or "year" in m.group(2) or "yr" in m.group(2):
                months = max(months, round(n * 12)) if "kinh nghiem" in key or "experience" in key else months
            elif "kinh nghiem" in key or "experience" in key:
                months = max(months, round(n))
        if _EDU_LINE.search(key):
            lv = ent.degree_level(line)
            if lv is not None:
                level = lv if level is None else min(level, lv)  # "Cử nhân trở lên" → bậc thấp nhất được chấp nhận

        if not skills or len(line.split()) > 6:
            other.append(line)

    jd.ky_nang_bat_buoc = [g for g in must if g not in nice]
    jd.ky_nang_uu_tien = list(nice)
    jd.so_thang_kinh_nghiem_toi_thieu = months
    jd.bac_hoc_toi_thieu = level
    jd.yeu_cau_khac = [o for o in other if not _YEARS.search(norm_key(o)) and not _EDU_LINE.search(norm_key(o))]
    return jd
