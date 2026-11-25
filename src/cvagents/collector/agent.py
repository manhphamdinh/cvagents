"""CV_Collector_Agent: thu thập hồ sơ từ nhiều nguồn, kiểm tra định dạng, loại trùng, lưu kèm metadata và chuyển
giao cho CV_Extractor_Agent (mục 2.2.2, kịch bản K3).

    nguồn → giải nén .zip → kiểm tra định dạng → loại trùng byte → loại gần trùng → nhận diện cùng ứng viên → lưu
          → (tuỳ chọn) thư xác nhận đã nhận hồ sơ

Hồ sơ hợp lệ có trạng thái `moi`; sau khi qua Extractor được đánh dấu `da_xu_ly` / `loi_xu_ly` bằng `mark`.
"""
from __future__ import annotations

import io
import logging
import smtplib
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from cvagents.collector import fingerprint as fp
from cvagents.collector.models import (
    DONE, DUP, FAILED, FETCH_ERR, INVALID, NEAR_DUP, NEW, CollectedCV, Incoming, RunReport, SourceStats,
)
from cvagents.collector.sources.base import Source, SourceState
from cvagents.collector.store import CollectorStore, now_iso
from cvagents.collector.validate import SUFFIX, sniff, validate, zip_too_big
from cvagents.common.config import load_config
from cvagents.common.schemas import CVProfile
from cvagents.mailer import ACK, EmailAgent, SentLog

log = logging.getLogger("cvagents.collector")


class CVCollectorAgent:
    def __init__(self, store: CollectorStore | None = None, sources: Iterable[Source] = (),
                 mailer: EmailAgent | None = None, sent_log: SentLog | None = None, ack: bool | None = None,
                 cfg: dict | None = None):
        self.cfg = {**load_config()["collector"], **(cfg or {})}
        self.store = store or CollectorStore()
        self.sources: list[Source] = list(sources)
        self.mailer = mailer
        self.sent_log = sent_log
        self.ack = self.cfg["gui_xac_nhan"] if ack is None else ack
        self._run: int | None = None

    # -- một lần thu thập -------------------------------------------------------
    def collect(self, sources: Iterable[Source] | None = None) -> RunReport:
        report = RunReport(bat_dau=now_iso())
        report.id = self._run = self.store.start_run()
        for src in (self.sources if sources is None else sources):
            st = SourceStats(src.ten, src.nguon)
            t0 = time.perf_counter()
            try:
                for inc in src.fetch(SourceState(self.store, src.ten, st)):
                    for cv in self.ingest(inc, ten_nguon=src.ten):
                        _count(st, cv)
                        if cv.trang_thai == NEW:
                            report.moi.append(cv)
            except Exception as e:  # một nguồn lỗi (sai mật khẩu, mất mạng) không làm dừng các nguồn khác
                st.loi = f"{type(e).__name__}: {e}"
                log.error("Nguồn %s lỗi: %s", src.ten, st.loi)
            st.giay = round(time.perf_counter() - t0, 3)
            report.nguon.append(st)
            log.info("Nguồn %s: %d tệp, %d hợp lệ, %d trùng, %d gần trùng, %d không hợp lệ, %d lỗi tải (%.1f s)",
                     src.ten, st.thu, st.hop_le, st.trung, st.gan_trung, st.khong_hop_le, st.loi_tai, st.giay)
            if st.trang:
                log.info("Nguồn %s: %d trang, %d trang mới, %d trang thay đổi", src.ten, st.trang, st.trang_moi,
                         st.trang_thay_doi)
        report.ket_thuc = now_iso()
        self.store.finish_run(report)
        self._run = None
        return report

    # -- nhận một tệp ----------------------------------------------------------
    def ingest(self, inc: Incoming, ten_nguon: str = "") -> list[CollectedCV]:
        """Nhận một tệp (từ nguồn, form ứng tuyển hoặc giao diện tải lên); tệp .zip được giải nén một cấp."""
        if not inc.error and sniff(inc.data) == "zip":
            why = zip_too_big(inc.data, self.cfg)
            if why:
                return [self._record(inc, ten_nguon, INVALID, why)]
            out = []
            with zipfile.ZipFile(io.BytesIO(inc.data)) as z:
                for info in z.infolist():
                    p = PurePosixPath(info.filename)
                    if info.is_dir() or p.name.startswith((".", "~$")) or "__MACOSX" in p.parts:
                        continue
                    sub = Incoming(**{**inc.__dict__, "name": p.name, "data": z.read(info),
                                      "tham_chieu": f"{inc.tham_chieu or inc.name}!{info.filename}"})
                    out.append(self._ingest_one(sub, ten_nguon))
            return out
        return [self._ingest_one(inc, ten_nguon)]

    def _ingest_one(self, inc: Incoming, ten_nguon: str) -> CollectedCV:
        from cvagents.screening import content_key

        if inc.error:
            return self._record(inc, ten_nguon, FETCH_ERR, inc.error)
        v = validate(inc.name, inc.data, self.cfg)
        key = content_key(inc.data)
        if not v.ok:
            return self._record(inc, ten_nguon, INVALID, v.reason, key=key, kind=v.kind)
        orig = self.store.accepted(key)
        if orig:
            return self._record(inc, ten_nguon, DUP, f"Trùng byte với {orig.ten_tep} ({orig.nguon})",
                                key=key, kind=v.kind, trung_voi=orig.key)

        text = fp.text_of(v.kind, inc.data)
        f = fp.fingerprint(v.kind, inc.data, text, self.cfg["min_tu_van_tay"])
        if f:
            limit = self.cfg["simhash_max_bits"] if f[0] == "text" else self.cfg["dhash_max_bits"]
            near = min(((fp.hamming(f[1], h), k) for k, h in self.store.fingerprints(f[0])), default=None)
            if near and near[0] <= limit:
                o = self.store.accepted(near[1])
                return self._record(inc, ten_nguon, NEAR_DUP,
                                    f"Gần trùng với {o.ten_tep} (khác {near[0]}/64 bit vân tay)",
                                    key=key, kind=v.kind, trung_voi=near[1], van_tay=f)

        email, sdt = fp.identity(text) if text else (None, None)
        same = self.store.find_identity(email, sdt)
        note = v.reason
        if same:
            note = "; ".join(x for x in (note, f"Cùng ứng viên với {same.ten_tep} "
                                               f"({'email' if email and same.email == email else 'SĐT'} trùng)") if x)
        path = self.store.save_file(key, SUFFIX[v.kind], inc.data)
        cv = self._record(inc, ten_nguon, NEW, note, key=key, kind=v.kind, trung_voi=same.key if same else None,
                          van_tay=f, path=path, pages=v.so_trang, email=email, sdt=sdt)
        if self.ack:
            self._acknowledge(cv)
        return cv

    def _record(self, inc: Incoming, ten_nguon: str, status: str, reason: str = "", key: str = "",
                kind: str | None = None, trung_voi: str | None = None, van_tay: tuple[str, int] | None = None,
                path: Path | None = None, pages: int | None = None, email: str | None = None,
                sdt: str | None = None) -> CollectedCV:
        name = inc.name
        if kind in SUFFIX and Path(name).suffix.lower() not in (SUFFIX[kind], ".jpeg" if kind == "jpg" else ""):
            name = Path(name).stem + SUFFIX[kind]
        cv = CollectedCV(
            key=key, ten_tep=name, nguon=inc.nguon, ten_nguon=ten_nguon or inc.nguon, tham_chieu=inc.tham_chieu,
            dinh_dang=kind or "", kich_thuoc=len(inc.data), so_trang=pages, nguoi_gui=inc.nguoi_gui,
            ten_nguoi_gui=inc.ten_nguoi_gui, tieu_de=inc.tieu_de, vi_tri=inc.vi_tri,
            thoi_diem_nhan=now_iso(), trang_thai=status, ly_do=reason, trung_voi=trung_voi,
            duong_dan=str(path) if path else None, van_tay=f"{van_tay[1]:016x}" if van_tay else None,
            loai_van_tay=van_tay[0] if van_tay else None, email=email, sdt=sdt, lan_chay=self._run)
        self.store.add(cv)
        (log.info if status == NEW else log.warning)("%s %s [%s] %s", status, name, ten_nguon or inc.nguon, reason)
        return cv

    def _acknowledge(self, cv: CollectedCV) -> None:
        to = cv.nguoi_gui or cv.email
        if not (self.mailer and to):
            return
        key = f"ack:{cv.key}"
        if self.sent_log is not None and key in self.sent_log:
            return
        try:
            r = self.mailer.send([self.mailer.compose(to, cv.ten_nguoi_gui, cv.vi_tri or "", ACK)])[0]
        except (smtplib.SMTPException, OSError) as e:
            log.warning("Không gửi được thư xác nhận cho %s: %s", to, e)
            return
        if r.status == "sent" and self.sent_log is not None:
            self.sent_log.add(key)

    # -- chuyển giao cho CV_Extractor -------------------------------------------
    def pending(self) -> list[CollectedCV]:
        return self.store.all(NEW)

    def mark(self, cv: CollectedCV, profile: CVProfile | None = None, error: str = "") -> None:
        """Ghi kết quả của CV_Extractor; email / SĐT trích xuất được dùng để nhận diện cùng ứng viên về sau."""
        fields = {"trang_thai": FAILED if error else DONE}
        if error:
            fields["ly_do"] = "; ".join(x for x in (cv.ly_do, f"Extractor: {error}") if x)
        if profile:
            c = profile.thong_tin_ca_nhan
            fields.update(email=(c.email or "").lower() or cv.email, sdt=c.so_dien_thoai or cv.sdt)
        self.store.update(cv.id, **fields)
        for k, v in fields.items():
            setattr(cv, k, v)

    def purge(self, days: int | None = None) -> int:
        n = self.store.purge(self.cfg["luu_toi_da_ngay"] if days is None else days)
        if n:
            log.info("Đã xoá %d hồ sơ quá hạn", n)
        return n


def _count(st: SourceStats, cv: CollectedCV) -> None:
    st.thu += 1
    if cv.trang_thai == NEW:
        st.hop_le += 1
        st.cung_ung_vien += bool(cv.trung_voi)
    else:
        attr = {DUP: "trung", NEAR_DUP: "gan_trung", INVALID: "khong_hop_le", FETCH_ERR: "loi_tai"}[cv.trang_thai]
        setattr(st, attr, getattr(st, attr) + 1)
