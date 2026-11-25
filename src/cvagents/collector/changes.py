"""Phát hiện thay đổi của trang web giữa các lần crawl và lưu các phiên bản đã crawl.

So sánh phần chữ hiển thị của trang (bỏ thẻ, script, style, khoảng trắng thừa) chứ không so HTML thô, vì HTML hay
đổi ở những chỗ không mang nội dung (mã chống giả mạo, thời điểm sinh trang, mã quảng cáo):

    δ = 1 − 2M / (|A| + |B|)        A, B: các dòng chữ của phiên bản đã lưu gần nhất và của bản vừa tải,
                                    M: tổng độ dài các đoạn dòng khớp nhau (difflib.SequenceMatcher)

    δ = 0 khi hai bản giống hệt, δ = 1 khi không còn dòng nào chung. Trang được coi là thay đổi khi δ ≥ nguong_thay_doi;
    khi đó bản mới được lưu thành phiên bản kế tiếp và làm mốc cho lần so sau. Thay đổi nhỏ hơn ngưỡng (bộ đếm lượt xem,
    ngày giờ) không được lưu nhưng vẫn được cộng dồn, vì luôn so với phiên bản đã lưu chứ không so với lần tải trước.
"""
from __future__ import annotations

import difflib
import hashlib
from html.parser import HTMLParser

from cvagents.collector.models import PAGE_CHANGED, PAGE_NEW, PAGE_SAME
from cvagents.collector.store import CollectorStore

_SKIP = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"title", "p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul",
          "ol", "header", "footer", "td", "th", "dt", "dd", "pre", "blockquote", "form"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def page_text(html: str) -> str:
    """Phần chữ hiển thị của trang, mỗi khối một dòng, đã gộp khoảng trắng."""
    p = _Text()
    p.feed(html)
    lines = (" ".join(line.split()) for line in "".join(p.parts).splitlines())
    return "\n".join(line for line in lines if line)


def change_ratio(old: str, new: str) -> float:
    """Tỷ lệ thay đổi δ ∈ [0, 1] giữa hai phần chữ (công thức ở đầu tệp)."""
    if old == new:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False).ratio()


def diff_lines(old: str, new: str, old_label: str = "trước", new_label: str = "sau") -> str:
    """Khác biệt dạng unified diff để xem lại thay đổi giữa hai phiên bản."""
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), old_label, new_label, lineterm=""))


def track_page(store: CollectorStore, url: str, nguon: str, html: bytes, threshold: float) -> tuple[str, float]:
    """So trang vừa tải với phiên bản đã lưu gần nhất; lưu phiên bản mới nếu là trang mới hoặc δ ≥ `threshold`.

    Trả về (PAGE_NEW | PAGE_CHANGED | PAGE_SAME, δ).
    """
    text = page_text(html.decode("utf-8", "replace"))
    sha = hashlib.sha1(text.encode("utf-8")).hexdigest()
    last = store.last_page_version(url)
    if last is None:
        status, ratio = PAGE_NEW, 1.0
    else:
        ratio = 0.0 if last.sha1 == sha else change_ratio(last.text(), text)
        status = PAGE_CHANGED if ratio >= threshold and ratio > 0 else PAGE_SAME
    if status != PAGE_SAME:
        store.add_page_version(url, nguon, html, text, sha, ratio)
    store.touch_page(url, nguon, ratio)
    return status, ratio
