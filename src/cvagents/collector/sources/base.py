"""Giao diện chung của các nguồn hồ sơ.

Mỗi nguồn là một bộ sinh `fetch(state)` trả về các `Incoming`. Nguồn ghi trạng thái (đã đọc thư nào, đã tải URL nào)
SAU câu lệnh `yield`, tức là sau khi tác tử đã lưu tệp, nên dừng giữa chừng không làm mất hồ sơ.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Protocol

from cvagents.collector.models import Incoming, SourceStats
from cvagents.collector.store import CollectorStore


class SourceState:
    """Trạng thái thu thập tăng dần của một nguồn, lưu trong bảng state của sổ cái. `stats` là thống kê của lần chạy
    hiện tại, nguồn web ghi số trang đã đọc / mới / thay đổi vào đó."""

    def __init__(self, store: CollectorStore, ten: str, stats: SourceStats | None = None):
        self.store, self.ten = store, ten
        self.stats = stats or SourceStats(ten, "")

    def get(self, khoa: str) -> str | None:
        return self.store.get_state(self.ten, khoa)

    def set(self, khoa: str, gia_tri: str = "1") -> None:
        self.store.set_state(self.ten, khoa, gia_tri)

    def seen(self, khoa: str) -> bool:
        return self.get(khoa) is not None


class Source(Protocol):
    nguon: str  # loại nguồn (models.FOLDER, IMAP …)
    ten: str    # tên cấu hình, duy nhất; là khoá của trạng thái

    def fetch(self, state: SourceState) -> Iterator[Incoming]: ...


_APPLY_RE = re.compile(
    r"(?:ứng\s*tuyển|ung\s*tuyen|apply(?:ing)?(?:\s+for)?|application\s+for|cv\s+(?:ứng\s*tuyển\s+)?(?:vị\s*trí)?|"
    r"v/v)\s*(?:vị\s*trí|vi\s*tri|position|role|the)?\s*[:\-–]?\s*(?P<vt>.+)", re.I)
_TAIL_RE = re.compile(r"\s+(?:[-–|_]|tại|at)\s+.*$", re.I)
_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|tl|tr)\s*:\s*)+", re.I)


def job_from_subject(subject: str | None) -> str | None:
    """Vị trí ứng tuyển từ tiêu đề thư: "[Ứng tuyển] Backend Developer - Nguyễn Văn A" → "Backend Developer"."""
    if not subject:
        return None
    s = _PREFIX_RE.sub("", subject).replace("[", " ").replace("]", " ")
    m = _APPLY_RE.search(s)
    if not m:
        return None
    vt = _TAIL_RE.sub("", m.group("vt")).strip(" .:-–")
    return vt or None
