"""Sổ cái của CV_Collector: SQLite (collector.db) + thư mục tệp đã nhận, trong `inbox` của config.toml.

Bảng cv     mỗi tệp nhận được một dòng (kể cả trùng, không hợp lệ) để truy vết và tính độ đo K3
Bảng state  trạng thái thu thập tăng dần của từng nguồn (UID thư cuối, URL đã tải, tệp đã đọc …)
Bảng runs   thống kê từng lần chạy
Bảng trang      trang web đã crawl: lần đầu / lần cuối kiểm tra, số lần kiểm tra, tỷ lệ thay đổi ở lần kiểm tra cuối
Bảng phien_ban  các phiên bản đã lưu của từng trang (HTML + phần chữ trong pages/) để phát hiện và xem lại thay đổi
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from cvagents.collector.models import ACCEPTED, CollectedCV, PageVersion, RunReport, SourceStats
from cvagents.common.config import load_config

_COLS = [c for c in CollectedCV.model_fields if c != "id"]
_INT = {"kich_thuoc", "so_trang", "lan_chay"}


class CollectorStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or load_config()["collector"]["inbox"])
        self.files = self.root / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.pages = self.root / "pages"
        self._lock = threading.Lock()  # giao diện Streamlit và bộ lập lịch có thể dùng chung một kho
        self.db = sqlite3.connect(self.root / "collector.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        cols = ", ".join(f"{c} {'INTEGER' if c in _INT else 'TEXT'}" for c in _COLS)
        with self.db:
            self.db.execute(f"CREATE TABLE IF NOT EXISTS cv (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols})")
            self.db.execute("CREATE INDEX IF NOT EXISTS cv_key ON cv(key)")
            self.db.execute("CREATE TABLE IF NOT EXISTS state (nguon TEXT, khoa TEXT, gia_tri TEXT, "
                            "PRIMARY KEY (nguon, khoa))")
            self.db.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, bat_dau TEXT, "
                            "ket_thuc TEXT, thong_ke TEXT)")
            self.db.execute("CREATE TABLE IF NOT EXISTS trang (url TEXT PRIMARY KEY, nguon TEXT, lan_dau TEXT, "
                            "lan_cuoi TEXT, so_lan INTEGER, ty_le_lan_cuoi REAL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS phien_ban (url TEXT, phien_ban INTEGER, thoi_diem TEXT, "
                            "sha1 TEXT, ty_le_thay_doi REAL, duong_dan TEXT, nguon TEXT, PRIMARY KEY (url, phien_ban))")

    def close(self) -> None:
        self.db.close()

    # -- hồ sơ ---------------------------------------------------------------
    def add(self, cv: CollectedCV) -> CollectedCV:
        row = cv.model_dump(exclude={"id"})
        with self._lock, self.db:
            cur = self.db.execute(f"INSERT INTO cv ({', '.join(_COLS)}) VALUES ({', '.join('?' * len(_COLS))})",
                                  [row[c] for c in _COLS])
        cv.id = cur.lastrowid
        return cv

    def update(self, cv_id: int, **fields) -> None:
        with self._lock, self.db:
            self.db.execute(f"UPDATE cv SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                            [*fields.values(), cv_id])

    def _rows(self, where: str = "", args: tuple = ()) -> list[CollectedCV]:
        with self._lock:
            rows = self.db.execute(f"SELECT * FROM cv {where} ORDER BY id", args).fetchall()
        return [CollectedCV(**dict(r)) for r in rows]

    def get(self, cv_id: int) -> CollectedCV | None:
        rows = self._rows("WHERE id = ?", (cv_id,))
        return rows[0] if rows else None

    def accepted(self, key: str) -> CollectedCV | None:
        """Bản gốc (đã nhận) của một khoá nội dung."""
        rows = self._rows(f"WHERE key = ? AND trang_thai IN ({', '.join('?' * len(ACCEPTED))})", (key, *ACCEPTED))
        return rows[0] if rows else None

    def all(self, trang_thai: str | tuple[str, ...] | None = None, nguon: str | None = None) -> list[CollectedCV]:
        conds, args = [], []
        if trang_thai:
            ts = (trang_thai,) if isinstance(trang_thai, str) else trang_thai
            conds.append(f"trang_thai IN ({', '.join('?' * len(ts))})")
            args += ts
        if nguon:
            conds.append("nguon = ?")
            args.append(nguon)
        return self._rows(("WHERE " + " AND ".join(conds)) if conds else "", tuple(args))

    def fingerprints(self, loai: str) -> list[tuple[str, int]]:
        with self._lock:
            rows = self.db.execute(
                f"SELECT key, van_tay FROM cv WHERE loai_van_tay = ? AND trang_thai IN ({', '.join('?' * len(ACCEPTED))})",
                (loai, *ACCEPTED)).fetchall()
        return [(r["key"], int(r["van_tay"], 16)) for r in rows]

    def find_identity(self, email: str | None, sdt: str | None) -> CollectedCV | None:
        conds = [c for c, v in (("email = ?", email), ("sdt = ?", sdt)) if v]
        if not conds:
            return None
        rows = self._rows(f"WHERE ({' OR '.join(conds)}) AND trang_thai IN ({', '.join('?' * len(ACCEPTED))})",
                          (*[v for v in (email, sdt) if v], *ACCEPTED))
        return rows[0] if rows else None

    def save_file(self, key: str, suffix: str, data: bytes) -> Path:
        p = self.files / f"{key}{suffix}"
        if not p.exists():
            p.write_bytes(data)
        return p

    # -- trạng thái nguồn ------------------------------------------------------
    def get_state(self, nguon: str, khoa: str) -> str | None:
        with self._lock:
            r = self.db.execute("SELECT gia_tri FROM state WHERE nguon = ? AND khoa = ?", (nguon, khoa)).fetchone()
        return r[0] if r else None

    def set_state(self, nguon: str, khoa: str, gia_tri: str = "1") -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)", (nguon, khoa, gia_tri))

    def reset_state(self, nguon: str) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM state WHERE nguon = ?", (nguon,))

    # -- trang web đã crawl -----------------------------------------------------
    def page_versions(self, url: str) -> list[PageVersion]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM phien_ban WHERE url = ? ORDER BY phien_ban", (url,)).fetchall()
        return [PageVersion(**dict(r)) for r in rows]

    def last_page_version(self, url: str) -> PageVersion | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM phien_ban WHERE url = ? ORDER BY phien_ban DESC LIMIT 1",
                                (url,)).fetchone()
        return PageVersion(**dict(r)) if r else None

    def add_page_version(self, url: str, nguon: str, html: bytes, text: str, sha1: str,
                         ratio: float) -> PageVersion:
        last = self.last_page_version(url)
        n = last.phien_ban + 1 if last else 1
        d = self.pages / hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        d.mkdir(parents=True, exist_ok=True)
        v = PageVersion(url, n, now_iso(), sha1, round(ratio, 4), str(d / str(n)), nguon)
        Path(v.duong_dan + ".html").write_bytes(html)
        Path(v.duong_dan + ".txt").write_text(text, encoding="utf-8")
        with self._lock, self.db:
            self.db.execute("INSERT INTO phien_ban (url, phien_ban, thoi_diem, sha1, ty_le_thay_doi, duong_dan, nguon) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (v.url, v.phien_ban, v.thoi_diem, v.sha1, v.ty_le_thay_doi, v.duong_dan, v.nguon))
        return v

    def touch_page(self, url: str, nguon: str, ratio: float) -> None:
        """Ghi một lần kiểm tra trang (kể cả khi không đổi)."""
        t = now_iso()
        with self._lock, self.db:
            self.db.execute("INSERT INTO trang VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(url) DO UPDATE SET "
                            "nguon = excluded.nguon, lan_cuoi = excluded.lan_cuoi, so_lan = so_lan + 1, "
                            "ty_le_lan_cuoi = excluded.ty_le_lan_cuoi", (url, nguon, t, t, round(ratio, 4)))

    def pages_seen(self) -> list[dict]:
        """Các trang đã crawl kèm số phiên bản đã lưu, trang kiểm tra gần nhất đứng trước."""
        with self._lock:
            rows = self.db.execute(
                "SELECT t.*, COUNT(p.phien_ban) AS so_phien_ban, MAX(p.thoi_diem) AS thay_doi_cuoi FROM trang t "
                "LEFT JOIN phien_ban p ON p.url = t.url GROUP BY t.url ORDER BY t.lan_cuoi DESC").fetchall()
        return [dict(r) for r in rows]

    # -- lần chạy ------------------------------------------------------------
    def start_run(self) -> int:
        with self._lock, self.db:
            return self.db.execute("INSERT INTO runs (bat_dau) VALUES (?)", (now_iso(),)).lastrowid

    def finish_run(self, report: RunReport) -> None:
        stats = json.dumps([asdict(s) for s in report.nguon], ensure_ascii=False)
        with self._lock, self.db:
            self.db.execute("UPDATE runs SET ket_thuc = ?, thong_ke = ? WHERE id = ?",
                            (report.ket_thuc, stats, report.id))

    def runs(self, limit: int = 50) -> list[RunReport]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM runs WHERE ket_thuc IS NOT NULL ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [RunReport(r["id"], r["bat_dau"], r["ket_thuc"],
                          [SourceStats(**s) for s in json.loads(r["thong_ke"] or "[]")]) for r in rows]

    # -- dọn dẹp -------------------------------------------------------------
    def purge(self, days: int, now: datetime | None = None) -> int:
        """Xoá tệp và bản ghi nhận trước `days` ngày (hồ sơ là dữ liệu cá nhân, không giữ vô hạn), cùng các phiên bản
        trang lưu trước `days` ngày, trừ phiên bản mới nhất của mỗi trang (mốc để phát hiện thay đổi)."""
        cutoff = ((now or datetime.now()) - timedelta(days=days)).isoformat(timespec="seconds")
        old = self._rows("WHERE thoi_diem_nhan < ?", (cutoff,))
        keep = {c.duong_dan for c in self._rows("WHERE thoi_diem_nhan >= ?", (cutoff,))}
        for c in old:
            if c.duong_dan and c.duong_dan not in keep:
                Path(c.duong_dan).unlink(missing_ok=True)
        with self._lock, self.db:
            self.db.execute("DELETE FROM cv WHERE thoi_diem_nhan < ?", (cutoff,))
            old_pages = "FROM phien_ban WHERE thoi_diem < ? AND phien_ban < " \
                        "(SELECT MAX(q.phien_ban) FROM phien_ban q WHERE q.url = phien_ban.url)"
            for (path,) in self.db.execute(f"SELECT duong_dan {old_pages}", (cutoff,)).fetchall():
                for ext in (".html", ".txt"):
                    Path(path + ext).unlink(missing_ok=True)
            self.db.execute(f"DELETE {old_pages}", (cutoff,))
        return len(old)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
