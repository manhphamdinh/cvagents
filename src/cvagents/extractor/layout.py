"""Bước 2: lọc vùng OCR (2.6) và kiểm tra thứ tự đọc hai cột (2.7)."""
from __future__ import annotations

from cvagents.common.config import load_config
from cvagents.extractor.models import Block


def filter_ocr(blocks: list[Block]) -> list[Block]:
    """O′ = {(t,p,b) ∈ O : p ≥ τ_ocr}  (2.6)."""
    tau = load_config()["extractor"]["tau_ocr"]
    return [b for b in blocks if b.conf >= tau]


def kmeans_1d(xs: list[float], iters: int = 50) -> tuple[float, float]:
    """k-means một chiều với k = 2; khởi tạo tại min và max."""
    mu1, mu2 = min(xs), max(xs)
    for _ in range(iters):
        g1 = [x for x in xs if abs(x - mu1) <= abs(x - mu2)]
        g2 = [x for x in xs if abs(x - mu1) > abs(x - mu2)]
        n1 = sum(g1) / len(g1) if g1 else mu1
        n2 = sum(g2) / len(g2) if g2 else mu2
        if (n1, n2) == (mu1, mu2):
            break
        mu1, mu2 = n1, n2
    return mu1, mu2


def reading_order(blocks: list[Block], page_width: float) -> list[Block]:
    """Một trang: nếu là hai cột thì đọc hết cột trái rồi cột phải, ngược lại giữ nguyên thứ tự."""
    cfg = load_config()["extractor"]
    if len(blocks) < 2 * cfg["two_col_min_blocks"]:
        return blocks
    mu1, mu2 = kmeans_1d([b.cx for b in blocks])
    if abs(mu1 - mu2) <= cfg["two_col_gap"] * page_width:
        return blocks
    lo, hi = min(mu1, mu2), max(mu1, mu2)
    col = lambda b: 0 if abs(b.cx - lo) <= abs(b.cx - hi) else 1  # noqa: E731
    n_left = sum(1 for b in blocks if col(b) == 0)
    if min(n_left, len(blocks) - n_left) < cfg["two_col_min_blocks"]:
        return blocks
    return sorted(blocks, key=lambda b: (col(b), b.y1))


def order_document(blocks: list[Block], page_width: float) -> list[Block]:
    pages = sorted({b.page for b in blocks})
    out: list[Block] = []
    for p in pages:
        out.extend(reading_order([b for b in blocks if b.page == p], page_width))
    return out
