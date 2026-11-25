"""Bước 8: độ tin cậy trích xuất κ (2.12), mức đầy đủ φ (2.13) và cảnh báo."""
from __future__ import annotations

from cvagents.common.config import load_config
from cvagents.common.schemas import CVProfile
from cvagents.extractor.models import Block


def extraction_confidence(ocr_blocks: list[Block] | None) -> float:
    """κ = Σ p_k·|t_k| / Σ |t_k| với nhánh OCR; κ = 1 với nhánh lớp văn bản."""
    if not ocr_blocks:
        return 1.0
    total = sum(len(b.text) for b in ocr_blocks)
    return sum(b.conf * len(b.text) for b in ocr_blocks) / total if total else 0.0


def completeness(p: CVProfile) -> float:
    """φ = (1/|F|) Σ 1[f có dữ liệu], F = {liên hệ, học vấn, kinh nghiệm hoặc dự án, kỹ năng}."""
    c = p.thong_tin_ca_nhan
    present = [
        bool(c.email or c.so_dien_thoai),
        bool(p.hoc_van),
        bool(p.kinh_nghiem or p.du_an),
        bool(p.ky_nang.chuan_hoa or p.ky_nang.goc),
    ]
    return sum(present) / len(present)


def warnings(p: CVProfile) -> list[str]:
    cfg = load_config()["extractor"]
    c, out = p.thong_tin_ca_nhan, []
    if not (c.email or c.so_dien_thoai):
        out.append("THIEU_LIEN_HE")
    if p.do_tin_cay < cfg["confidence_min"]:
        out.append("DO_TIN_CAY_THAP")
    if p.muc_day_du < cfg["completeness_min"]:
        out.append("THIEU_TRUONG")
    return out
