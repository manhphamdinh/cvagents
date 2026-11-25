"""Biểu diễn trung gian giữa lớp đọc tài liệu (Docling) và lớp hậu xử lý."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    """Một vùng văn bản trên trang: (t_k, p_k, b_k) của công thức (2.1)."""
    text: str
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    conf: float = 1.0
    label: str = "text"  # title | section_header | text | list_item | table | ...
    page: int = 1
    bold: bool = False
    table: list[list[str]] | None = None  # ô của bảng nếu label == "table"

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2


@dataclass
class RawDocument:
    blocks: list[Block] = field(default_factory=list)
    n_pages: int = 1
    page_width: float = 595.0
    used_ocr: bool = False
    page_sizes: dict[int, tuple[float, float]] = field(default_factory=dict)  # (rộng, cao) theo đơn vị toạ độ khối
    page_images: dict = field(default_factory=dict)  # số trang → ảnh PIL do Docling kết xuất (PDF, ảnh; DOCX không có)
