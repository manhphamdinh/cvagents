"""Tổng hợp điểm theo nhiều tiêu chí (không dựa vào một tiêu chí duy nhất)."""
from __future__ import annotations

from cvagents.common.config import load_config
from cvagents.common.schemas import CVProfile, JobDescription, RequirementMatch


def _coverage(ms: list[RequirementMatch], kind: str) -> float | None:
    sel = [m for m in ms if m.loai == kind]
    return sum(m.dat for m in sel) / len(sel) if sel else None


def component_scores(cv: CVProfile, jd: JobDescription, ms: list[RequirementMatch], embedder=None) -> dict[str, float]:
    """Chỉ trả về các thành phần mà JD thực sự có yêu cầu; trọng số sẽ được chuẩn hoá lại trên tập này."""
    out: dict[str, float] = {}
    if embedder is not None and cv.van_ban_goc.strip() and jd.van_ban_goc.strip():
        # tương đồng toàn văn: cosine giữa vector của toàn bộ CV và toàn bộ JD (vector đã chuẩn hoá L2)
        v = embedder.encode([cv.van_ban_goc, jd.van_ban_goc])
        out["toan_van"] = min(max(float(v[0] @ v[1]), 0.0), 1.0)
    if (c := _coverage(ms, "bat_buoc")) is not None:
        out["bat_buoc"] = c
    if (c := _coverage(ms, "uu_tien")) is not None:
        out["uu_tien"] = c
    sem = [m for m in ms if m.loai == "khac"] or ms
    if sem:  # tương đồng ngữ nghĩa: trung bình cosine tốt nhất của các yêu cầu dạng câu (không có thì mọi yêu cầu)
        out["ngu_nghia"] = sum(min(max(m.cosine, 0.0), 1.0) for m in sem) / len(sem)
    if jd.so_thang_kinh_nghiem_toi_thieu > 0:
        out["kinh_nghiem"] = min(cv.tong_thang_kinh_nghiem / jd.so_thang_kinh_nghiem_toi_thieu, 1.0)
    if jd.bac_hoc_toi_thieu:
        lv = max((e.bac_hoc or 0 for e in cv.hoc_van), default=0)
        out["hoc_van"] = min(lv / jd.bac_hoc_toi_thieu, 1.0)
    return out


_WEIGHT_KEY = {
    "toan_van": "w_fulltext",
    "bat_buoc": "w_must", "uu_tien": "w_nice", "ngu_nghia": "w_semantic",
    "kinh_nghiem": "w_experience", "hoc_van": "w_education",
}


def weights() -> dict[str, float]:
    cfg = load_config()["analysis"]
    return {k: float(cfg.get(w, 0.0)) for k, w in _WEIGHT_KEY.items()}


def must_check(parts: dict[str, float]) -> tuple[bool, bool]:
    """(đủ, trọn vẹn) của kỹ năng bắt buộc: đủ khi tỷ lệ đáp ứng ≥ must_min, trọn vẹn khi đáp ứng hết.
    Luật loại cứng, áp dụng trước điểm tổng: thiếu kỹ năng bắt buộc thì điểm ngữ nghĩa cao cũng không bù được.
    JD không có kỹ năng bắt buộc nào thì coi như đủ."""
    c = parts.get("bat_buoc")
    if c is None:
        return True, True
    return c >= load_config()["analysis"]["must_min"], c >= 1.0


def total_score(parts: dict[str, float], w: dict[str, float] | None = None) -> float:
    w = w or weights()
    wsum = sum(w[k] for k in parts)
    if not wsum:  # mọi thành phần hiện có đều trọng số 0 (vd. không có toàn văn khi trọng số học dồn hết vào đó)
        return sum(parts.values()) / len(parts) if parts else 0.0
    return sum(w[k] * v for k, v in parts.items()) / wsum
