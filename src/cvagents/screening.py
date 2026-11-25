"""Điều phối các tác tử cho một đợt sàng lọc: (CV_Collector →) CV_Extractor → CV/JD_Analysis → trạng thái → Email_Agent.

Trạng thái sau đánh giá:
    Đạt              điểm ≥ τS                       → tự động gửi thư mời
    Cần xem xét lại  τS − review_margin ≤ điểm < τS  → nhà tuyển dụng duyệt (gửi thư mời) hoặc loại
    Không đạt        còn lại
"""
from __future__ import annotations

import hashlib
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cvagents.analysis.scoring import must_check
from cvagents.common.config import load_config
from cvagents.common.schemas import AnalysisReport, CVProfile, JobDescription
from cvagents.common.text import detect_lang
from cvagents.extractor.models import RawDocument
from cvagents.mailer import INVITE, EmailAgent, SentLog

PASS, REVIEW, FAIL, ERROR = "dat", "xem_xet", "khong_dat", "loi"
STATUS_LABEL = {PASS: "Đạt", REVIEW: "Cần xem xét lại", FAIL: "Không đạt", ERROR: "Lỗi xử lý"}
EMAIL_LABEL = {"sent": "đã gửi thư mời", "saved": "đã lưu thư nháp", "sent_before": "đã gửi thư mời trước đó",
               "no_email": "CV không có email"}
_STATUS_RANK = {PASS: 3, REVIEW: 2, FAIL: 1, ERROR: 0}


@dataclass
class CVResult:
    key: str   # băm nội dung tệp
    file: str
    data: bytes = b""
    lang: str = ""
    model: str = ""  # mô hình nhúng đã dùng cho CV này
    raw: RawDocument | None = None
    profile: CVProfile | None = None
    report: AnalysisReport | None = None
    status: str = ERROR
    reviewed: bool = False  # trạng thái do nhà tuyển dụng quyết định sau khi xem xét
    email: str = ""         # khoá trong EMAIL_LABEL hoặc "error: …"
    error: str = ""

    @property
    def label(self) -> str:
        s = STATUS_LABEL[self.status] + (" (đã xem xét)" if self.reviewed else "")
        if self.email:
            s += " · " + EMAIL_LABEL.get(self.email, self.email.replace("error:", "lỗi gửi thư:"))
        return s


def rank_key(r: CVResult) -> tuple[int, float]:
    """Thứ tự hiển thị: trạng thái trước (Đạt → Cần xem xét lại → Không đạt → Lỗi), rồi điểm tổng giảm dần."""
    return _STATUS_RANK[r.status], r.report.diem_tong if r.report else 0.0


def content_key(data: bytes | str) -> str:
    return hashlib.sha1(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()[:16]


def screening_status(report: AnalysisReport, pass_threshold: float | None = None) -> str:
    """Phân loại theo kỹ năng bắt buộc rồi τS; pass_threshold cho phép giao diện ghi đè ngưỡng trong config.toml.

    Đáp ứng dưới must_min kỹ năng bắt buộc → Không đạt; thiếu một phần → tối đa Cần xem xét lại."""
    cfg = load_config()["analysis"]
    if not report.diem_thanh_phan:
        return FAIL
    enough, complete = must_check(report.diem_thanh_phan)
    if not enough:
        return FAIL
    t = cfg["pass_threshold"] if pass_threshold is None else pass_threshold
    if report.diem_tong >= t:
        return PASS if complete else REVIEW
    if report.diem_tong >= t - cfg["review_margin"]:
        return REVIEW
    return FAIL


def send_invites(results: list[CVResult], mailer: EmailAgent, vi_tri: str, jd_key: str, log: SentLog) -> None:
    """Gửi thư mời cho các CV đang ở trạng thái Đạt; bỏ qua CV đã gửi thật cho cùng JD (theo SentLog)."""
    todo = []
    for r in results:
        if r.status != PASS or r.email in ("sent", "sent_before"):
            continue
        to = r.profile.thong_tin_ca_nhan.email
        if not to:
            r.email = "no_email"
            continue
        key = f"{jd_key}:{r.key}:{INVITE}"
        if key in log:
            r.email = "sent_before"
            continue
        todo.append((r, key, mailer.compose(to, r.profile.thong_tin_ca_nhan.ho_ten, vi_tri, INVITE)))
    if not todo:
        return
    try:
        sent = mailer.send([d for _, _, d in todo])
    except (smtplib.SMTPException, OSError) as e:  # không kết nối / đăng nhập được: chưa thư nào được gửi
        for r, _, _ in todo:
            r.email = f"error: {e}"
        raise
    for (r, key, _), s in zip(todo, sent):
        r.email = s.status if s.status != "error" else f"error: {s.detail}"
        if s.status == "sent":
            log.add(key)


def screen_files(files: list[tuple[str, Path]], jd: JobDescription | str, extractor,
                 analysis_for_lang: Callable[[str], tuple[object, str]],
                 on_done: Callable[[CVResult], None] | None = None) -> list[CVResult]:
    """Chạy Extractor → Analysis → trạng thái cho các tệp (tên hiển thị, đường dẫn), không cần giao diện.

    `analysis_for_lang(lang)` trả về (CVJDAnalysisAgent, tên mô hình) theo ngôn ngữ CV. `on_done` được gọi sau
    mỗi CV, ví dụ để CV_Collector ghi lại kết quả chuyển giao. Một CV lỗi không làm dừng cả lô.
    """
    results = []
    for name, path in files:
        data = Path(path).read_bytes()
        r = CVResult(key=content_key(data), file=name, data=data)
        try:
            r.raw, ocr = extractor.read(path)
            r.profile = extractor.process(r.raw, ocr)
            r.lang = detect_lang("\n".join(b.text for b in r.raw.blocks))
            agent, r.model = analysis_for_lang(r.lang)
            r.report = agent.analyze(r.profile, jd)
            r.status = screening_status(r.report)
        except Exception as e:  # một CV lỗi không làm dừng cả lô
            r.error = f"{type(e).__name__}: {e}"
        results.append(r)
        if on_done:
            on_done(r)
    results.sort(key=rank_key, reverse=True)
    return results
