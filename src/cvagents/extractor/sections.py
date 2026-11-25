"""Bước 4–5: nhận diện tiêu đề mục, định tuyến nội dung và chuẩn hoá bảng (Bảng 2.1)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz

from cvagents.common.config import load_resource
from cvagents.common.text import norm_key
from cvagents.extractor.models import Block
from cvagents.extractor.normalize import is_bullet, normalize_text

PERSONAL = "thong_tin_ca_nhan"
_DEGREE_LINE = re.compile(
    r"\b(c[uử] nh[aâ]n|k[yỹ] s[uư]|bachelor|engineer|th[aạ]c s[iĩ]|master|ti[eế]n s[iĩ]|ph\.?d)\b", re.I
)


@dataclass
class Line:
    text: str
    bullet: bool = False
    bold: bool = False
    record: dict[str, str] | None = None  # bảng có hàng tiêu đề: mỗi hàng là một bản ghi


@dataclass
class Sections:
    content: dict[str, list[Line]] = field(default_factory=dict)

    def get(self, name: str) -> list[Line]:
        return self.content.get(name, [])

    def text(self, name: str) -> str:
        return "\n".join(ln.text for ln in self.get(name))


@lru_cache(maxsize=1)
def _synonyms() -> list[tuple[str, str]]:
    """(tên mục, dạng không dấu của tên đồng nghĩa)."""
    return [(sec, norm_key(syn)) for sec, syns in load_resource("sections.json").items() for syn in syns]


def match_heading(text: str, fuzzy: bool = False) -> tuple[str, float] | None:
    key = norm_key(text.strip().rstrip(":").strip())
    if not key or len(key.split()) > 6:
        return None
    best: tuple[str, float] | None = None
    for sec, syn in _synonyms():
        score = 100.0 if key == syn else (fuzz.ratio(key, syn) if fuzzy else 0.0)
        if score >= (90 if fuzzy else 100) and (best is None or score > best[1]):
            best = (sec, score)
    return best


def _table_lines(rows: list[list[str]]) -> tuple[str | None, list[Line]]:
    """Bước 5: bảng → khoá-giá trị (2 cột) hoặc danh sách bản ghi (có hàng tiêu đề)."""
    rows = [[normalize_text(c) for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        return None, []
    head = match_heading(rows[0][0]) or (match_heading(rows[0][0], fuzzy=True))
    target = head[0] if head else None
    if len(rows[0]) == 2:
        body = rows[1:] if target and len(rows) > 1 else rows
        return target, [Line(f"{k}: {v}" if k else v) for k, v in body if k or v]
    header, body = rows[0], rows[1:]
    if target:  # ô đầu là tên mục: hàng tiêu đề thực sự là hàng kế tiếp
        header, body = (rows[1], rows[2:]) if len(rows) > 1 else (header, [])
    out = []
    for r in body:
        rec = {h: c for h, c in zip(header, r) if h and c}
        if rec:
            out.append(Line(" | ".join(rec.values()), record=rec))
    return target, out


# Bộ đọc PDF đôi khi gộp nhiều gạch đầu dòng liền nhau thành một khối "• a • b • c": tách lại thành từng dòng.
_INLINE_BULLET = re.compile(r"\s+[•▪●■◦‣∙·]\s+")


def _block_lines(b: Block) -> list[Line]:
    bold = b.bold or b.label in ("title", "section_header")
    lines = []
    for raw in b.text.split("\n"):
        parts = _INLINE_BULLET.split(raw)
        many = len(parts) > 1
        for part in parts:
            t = normalize_text(part)
            if t:
                lines.append(Line(t, bullet=many or b.label == "list_item" or is_bullet(part), bold=bold and not many))
    return lines


def route(blocks: list[Block]) -> Sections:
    sec = Sections()
    current: str | None = None
    for b in blocks:
        if b.table is not None:
            target, lines = _table_lines(b.table)
            name = target or current or PERSONAL
            sec.content.setdefault(name, []).extend(lines)
            if target:
                current = target
            continue
        for ln in _block_lines(b):
            is_header_block = b.label == "section_header"
            hit = match_heading(ln.text, fuzzy=is_header_block or not ln.bullet)
            if hit and not ln.bullet:
                current = hit[0]
                sec.content.setdefault(current, [])
                continue
            if current is None and _DEGREE_LINE.search(ln.text):
                sec.content.setdefault("hoc_van", []).append(ln)
            else:
                sec.content.setdefault(current or PERSONAL, []).append(ln)
    return sec
