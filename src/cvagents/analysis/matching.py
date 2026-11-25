"""Đối chiếu từng yêu cầu của JD với bằng chứng trong hồ sơ CV.

Yêu cầu kỹ năng (bắt buộc, ưu tiên) là tên kỹ năng chuẩn trong gazetteer: đạt khi CV có kỹ năng đó (một trong các lựa
chọn nếu JD ghi "A hoặc B") trong mục kỹ năng hoặc ở bất kỳ đâu trong văn bản CV (dự án, kinh nghiệm). Không dùng
cosine cho tên kỹ năng: hai tên công nghệ khác nhau vẫn có cosine cao (C#–Java ≈ 0,61) vì cùng là công nghệ lập trình.
Yêu cầu dạng câu (yeu_cau_khac) đối chiếu bằng cosine với bằng chứng gần nhất (2.19; vector L2 là tích vô hướng, 2.15).
"""
from __future__ import annotations

import numpy as np

from cvagents.analysis.embedder import Embedder
from cvagents.analysis.jd_parser import alternatives, scan_skills
from cvagents.common.schemas import CVProfile, JobDescription, RequirementMatch

# kỹ năng được đáp ứng khi CV có một kỹ năng cụ thể hơn: hệ quản trị CSDL ⇒ SQL, framework ⇒ ngôn ngữ của nó
IMPLIED_BY = {
    "SQL": ("SQL Server", "PostgreSQL", "MySQL"),
    "Python": ("FastAPI", "Django", "Flask"),
    "Java": ("Spring Boot", "Hibernate"),
    "PHP": ("Laravel",),
    "Dart": ("Flutter",),
}


def cv_skills(cv: CVProfile) -> dict[str, str]:
    """Kỹ năng chuẩn của CV (chữ thường) → nguồn bằng chứng."""
    have = {s.lower(): "ky_nang" for s in cv.ky_nang.chuan_hoa}
    for s in scan_skills(cv.van_ban_goc):
        have.setdefault(s.lower(), "van_ban")
    for g, subs in IMPLIED_BY.items():
        sub = next((s for s in subs if s.lower() in have), None)
        if sub:
            have.setdefault(g.lower(), _evidence(have[sub.lower()], sub))
    return have


def _evidence(src: str, skill: str) -> str:
    return src if ":" in src else f"{src}: {skill}"


def evidence_units(cv: CVProfile) -> list[tuple[str, str]]:
    """(nguồn, văn bản): mỗi đơn vị ngắn là một kỹ năng, một gạch đầu dòng hoặc một chức danh."""
    units: list[tuple[str, str]] = [("ky_nang", s) for s in cv.ky_nang.chuan_hoa]
    units += [("ky_nang", s) for s in cv.ky_nang.goc]
    for e in cv.kinh_nghiem:
        title = " - ".join(x for x in (e.chuc_danh, e.cong_ty) if x)
        if title:
            units.append(("kinh_nghiem", title))
        units += [("kinh_nghiem", m) for m in e.mo_ta]
    for p in cv.du_an:
        if p.ten:
            units.append(("du_an", p.ten))
        units += [("du_an", m) for m in p.mo_ta]
    if cv.muc_tieu:
        units.append(("muc_tieu", cv.muc_tieu))
    return units


def match_requirements(
    cv: CVProfile, jd: JobDescription, embedder: Embedder, threshold: float
) -> list[RequirementMatch]:
    reqs = (
        [(r, "bat_buoc") for r in jd.ky_nang_bat_buoc]
        + [(r, "uu_tien") for r in jd.ky_nang_uu_tien]
        + [(r, "khac") for r in jd.yeu_cau_khac]
    )
    if not reqs:
        return []
    have = cv_skills(cv)
    units = evidence_units(cv)
    texts = [r for r, k in reqs if k == "khac"]
    sims = None
    if units and texts:
        u = embedder.encode([t for _, t in units])
        q = embedder.encode(texts)
        sims = q @ u.T  # (số yêu cầu dạng câu) × (số bằng chứng)

    out, i = [], 0
    for req, kind in reqs:
        if kind != "khac":  # yêu cầu kỹ năng: khớp chính xác / bí danh, không dùng cosine
            hit = next((a for a in alternatives(req) if a.lower() in have), None)
            out.append(RequirementMatch(yeu_cau=req, loai=kind, cosine=1.0 if hit else 0.0, dat=hit is not None,
                                        bang_chung=_evidence(have[hit.lower()], hit) if hit else None))
            continue
        if sims is None:
            out.append(RequirementMatch(yeu_cau=req, loai=kind, cosine=0.0, dat=False))
        else:
            j = int(np.argmax(sims[i]))
            c = float(sims[i, j])
            out.append(RequirementMatch(
                yeu_cau=req, loai=kind, cosine=round(c, 4),
                bang_chung=f"{units[j][0]}: {units[j][1]}", dat=c >= threshold,
            ))
        i += 1
    return out
