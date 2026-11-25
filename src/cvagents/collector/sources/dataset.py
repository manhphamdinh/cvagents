"""Nguồn bộ dữ liệu: thư mục hoặc tệp .zip của một bộ CV công khai (ví dụ bộ Resume Dataset trên Kaggle, mỗi thư
mục con là một ngành nghề), có thể kèm tệp CSV metadata.

CSV (UTF-8, có dòng tiêu đề) được ghép theo tên tệp; nhận các cột ten_tep/file/filename, vi_tri/category/nhan,
ho_ten/name, email. Không có CSV thì vị trí lấy theo tên thư mục chứa tệp.
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from cvagents.collector.models import DATASET, SOURCE_LABEL, Incoming
from cvagents.collector.sources.base import SourceState
from cvagents.collector.validate import zip_too_big
from cvagents.common.config import load_config

_COLS = {"ten_tep": ("ten_tep", "file", "filename", "file_name", "id"),
         "vi_tri": ("vi_tri", "category", "nhan", "label", "position"),
         "ho_ten": ("ho_ten", "name", "candidate"), "email": ("email",)}


def read_metadata(text: str) -> dict[str, dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return {}
    cols = {k.lower().strip(): k for k in rows[0]}
    pick = {f: next((cols[c] for c in cand if c in cols), None) for f, cand in _COLS.items()}
    if not pick["ten_tep"]:
        return {}
    out = {}
    for r in rows:
        key = PurePosixPath(r[pick["ten_tep"]].strip()).name
        out[key] = {f: r[c].strip() for f, c in pick.items() if c and f != "ten_tep" and r.get(c)}
        out.setdefault(PurePosixPath(key).stem, out[key])  # CSV có thể ghi mã hồ sơ không kèm đuôi
    return out


class DatasetSource:
    nguon = DATASET

    def __init__(self, path: str | Path, ten: str | None = None, metadata: str | Path | None = None):
        self.path = Path(path)
        self.ten = ten or f"{SOURCE_LABEL[self.nguon]}: {self.path}"
        self.metadata = Path(metadata) if metadata else None

    def _entries(self) -> Iterator[tuple[str, bytes | None, str]]:
        """(đường dẫn tương đối, nội dung, lỗi)."""
        if self.path.is_dir():
            for p in sorted(self.path.rglob("*")):
                if p.is_file():
                    yield p.relative_to(self.path).as_posix(), p.read_bytes(), ""
            return
        data = self.path.read_bytes()
        why = zip_too_big(data, load_config()["collector"])
        if why:
            raise ValueError(f"{self.path.name}: {why}")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if not info.is_dir():
                    yield info.filename, z.read(info), ""

    def fetch(self, state: SourceState) -> Iterator[Incoming]:
        entries = list(self._entries())
        meta_text = self.metadata.read_text(encoding="utf-8-sig") if self.metadata else next(
            (d.decode("utf-8-sig", "replace") for n, d, _ in entries if n.lower().endswith(".csv")), "")
        meta = read_metadata(meta_text) if meta_text else {}
        for rel, data, err in entries:
            p = PurePosixPath(rel)
            if p.suffix.lower() in (".csv", ".md", ".txt", ".json") or p.name.startswith((".", "__MACOSX")) \
                    or "__MACOSX" in p.parts or state.seen(rel):
                continue
            m = meta.get(p.name) or meta.get(p.stem) or {}
            yield Incoming(p.name, data or b"", self.nguon, tham_chieu=f"{self.path.name}/{rel}",
                           ten_nguoi_gui=m.get("ho_ten"), nguoi_gui=m.get("email"),
                           vi_tri=m.get("vi_tri") or (p.parent.name if len(p.parts) > 1 else None), error=err)
            state.set(rel)
