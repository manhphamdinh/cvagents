"""Bước 7: gom trường (kinh nghiệm, học vấn, dự án) và chuẩn hoá giá trị."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from cvagents.common.schemas import Education, Experience, Project
from cvagents.extractor import entities as ent
from cvagents.extractor.sections import Line


@dataclass
class _Group:
    head: list[Line] = field(default_factory=list)  # các dòng đầu của phần tử
    desc: list[Line] = field(default_factory=list)  # mục danh sách phía sau
    rng: tuple[int, int, str] | None = None

    def text(self) -> str:
        return " ".join(ln.text for ln in [*self.head, *self.desc])


def group_lines(lines: list[Line], today: date | None = None) -> list[_Group]:
    """Phần tử mới bắt đầu tại dòng chứa khoảng thời gian hoặc dòng in đậm không phải mục danh sách."""
    groups: list[_Group] = []
    cur: _Group | None = None
    for ln in lines:
        if ln.record is not None:
            groups.append(_from_record(ln, today))
            cur = None
            continue
        rng = None if ln.bullet else ent.parse_range(ln.text, today)
        done = cur is not None and (cur.rng is not None or bool(cur.desc))
        starts = (rng is not None and (cur is None or done)) or (
            ln.bold and not ln.bullet and (cur is None or done)
        )
        if cur is None or starts:
            cur = _Group()
            groups.append(cur)
        if ln.bullet and cur.head:
            cur.desc.append(ln)
        else:
            cur.head.append(ln)
            if rng and cur.rng is None:
                cur.rng = rng
    return groups


def _from_record(ln: Line, today: date | None) -> _Group:
    g = _Group(head=[Line(v) for v in (ln.record or {}).values()])
    g.rng = ent.parse_range(ln.text, today)
    return g


def _head_split(g: _Group) -> tuple[str | None, str | None, str | None]:
    """(tổ chức, thời gian, phần còn lại) từ các dòng đầu."""
    org = rest = None
    for ln in g.head:
        t = ln.text
        if g.rng:
            t = t.replace(g.rng[2], "")
        t = ent.GPA_RE.sub("", t)
        for part in re.split(r"\s+-\s+|\s*\|\s*", t):
            part = re.sub(r"(\s*,)+\s*(?=,|$)", "", part).strip(" -|,()")
            if not part:
                continue
            if org is None and ent.is_org(part):
                org = part
            elif rest is None:
                rest = part
    return org, g.rng[2] if g.rng else None, rest


def build_experience(lines: list[Line], today: date | None = None) -> list[Experience]:
    out = []
    for g in group_lines(lines, today):
        org, _, title = _head_split(g)
        out.append(Experience(
            cong_ty=org, chuc_danh=title,
            thang_bat_dau=g.rng[0] if g.rng else None, thang_ket_thuc=g.rng[1] if g.rng else None,
            mo_ta=[ln.text for ln in g.desc],
        ))
    return out


def build_education(lines: list[Line], today: date | None = None) -> list[Education]:
    out = []
    for g in group_lines(lines, today):
        org, when, rest = _head_split(g)
        out.append(Education(
            truong=org, chuyen_nganh=rest, thoi_gian=when,
            bac_hoc=ent.degree_level(g.text()), gpa4=ent.gpa_to_4(g.text()),
        ))
    return out


def build_projects(lines: list[Line], today: date | None = None) -> list[Project]:
    out = []
    for g in group_lines(lines, today):
        _, when, rest = _head_split(g)
        out.append(Project(ten=rest, thoi_gian=when, mo_ta=[ln.text for ln in g.desc]))
    return out
