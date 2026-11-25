"""Nguồn thư mục: một thư mục cục bộ / thư mục đồng bộ (OneDrive, Google Drive Desktop) mà nhà tuyển dụng thả CV vào.

Tên thư mục con được dùng làm vị trí ứng tuyển: inbox/Backend Developer/cv.pdf → "Backend Developer".
Ở chế độ theo dõi, bộ lập lịch gọi lại `fetch`; chỉ tệp mới hoặc đã sửa (khác kích thước / thời điểm sửa) được đọc.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from cvagents.collector.models import FOLDER, SOURCE_LABEL, Incoming
from cvagents.collector.sources.base import SourceState
from cvagents.common.config import load_config

SKIP_PREFIX = ("~$", ".", "_")  # tệp khoá của Word, tệp ẩn


class FolderSource:
    nguon = FOLDER

    def __init__(self, path: str | Path, ten: str | None = None, recursive: bool = True,
                 settle: float | None = None):
        self.path = Path(path)
        self.ten = ten or f"{SOURCE_LABEL[self.nguon]}: {self.path}"
        self.recursive = recursive
        self.settle = load_config()["collector"]["on_dinh_giay"] if settle is None else settle

    def files(self) -> list[Path]:
        if not self.path.is_dir():
            raise FileNotFoundError(f"Không có thư mục {self.path}")
        it = self.path.rglob("*") if self.recursive else self.path.iterdir()
        return sorted(p for p in it if p.is_file() and not p.name.startswith(SKIP_PREFIX)
                      and not any(part.startswith(".") for part in p.relative_to(self.path).parts[:-1]))

    def fetch(self, state: SourceState) -> Iterator[Incoming]:
        now = time.time()
        for p in self.files():
            st = p.stat()
            if now - st.st_mtime < self.settle:  # có thể đang được chép vào, lấy ở lần sau
                continue
            rel = p.relative_to(self.path)
            khoa = rel.as_posix()
            version = f"{st.st_size}:{st.st_mtime_ns}"
            if state.get(khoa) == version:
                continue
            sub = rel.parts[0] if len(rel.parts) > 1 else None
            try:
                data, err = p.read_bytes(), ""
            except OSError as e:
                data, err = b"", f"Không đọc được tệp: {e}"
            yield Incoming(p.name, data, self.nguon, tham_chieu=str(p), vi_tri=sub,
                           thoi_diem=datetime.fromtimestamp(st.st_mtime), error=err)
            if not err:
                state.set(khoa, version)
