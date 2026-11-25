import smtplib

import pytest
from PIL import Image

from cvagents.analysis.embedder import model_for_lang
from cvagents.common.config import load_config
from cvagents.common.schemas import AnalysisReport, Contact, CVProfile
from cvagents.common.text import detect_lang
from cvagents.extractor.models import Block
from cvagents.extractor.visualize import draw_blocks
from cvagents.mailer import EmailAgent, SentLog, SmtpSettings
from cvagents.screening import ERROR, FAIL, PASS, REVIEW, CVResult, rank_key, screening_status, send_invites


def test_detect_lang():
    assert detect_lang("Kỹ năng: Python, SQL. Kinh nghiệm làm việc 2 năm tại Công ty ABC") == "vi"
    en_cv = "Nguyễn Văn An\nSoftware engineer with 3 years of experience building REST APIs in Python and Go. " * 3
    assert detect_lang(en_cv) == "en"
    assert detect_lang("") == "vi"


def test_model_for_lang():
    cfg = load_config()["analysis"]
    assert model_for_lang("vi") == cfg["embed_model_vi"]
    assert model_for_lang("en") == cfg["embed_model"]


def rep(score: float, must: float | None = None) -> AnalysisReport:
    cfg = load_config()["analysis"]
    parts = {"toan_van": score} | ({"bat_buoc": must} if must is not None else {})
    return AnalysisReport(diem_tong=score, diem_thanh_phan=parts, phu_hop=score >= cfg["pass_threshold"])


def test_screening_status_bands():
    cfg = load_config()["analysis"]
    t, m = cfg["pass_threshold"], cfg["review_margin"]
    assert screening_status(rep(t)) == PASS
    assert screening_status(rep(t - m / 2)) == REVIEW
    assert screening_status(rep(t - m)) == REVIEW
    assert screening_status(rep(t - m - 0.01)) == FAIL
    assert screening_status(AnalysisReport(diem_tong=0.0, diem_thanh_phan={}, phu_hop=False)) == FAIL


def test_missing_must_skills_cap_status_regardless_of_score():
    lo = load_config()["analysis"]["must_min"]
    assert screening_status(rep(0.95, must=1.0)) == PASS
    assert screening_status(rep(0.95, must=lo)) == REVIEW             # thiếu một phần: nhà tuyển dụng duyệt
    assert screening_status(rep(0.95, must=lo - 0.01)) == FAIL        # thiếu nhiều: loại dù điểm ngữ nghĩa cao


def test_results_rank_by_status_before_score():
    rs = [CVResult(key=k, file=k, status=s, report=rep(v) if v is not None else None)
          for k, s, v in [("loai_diem_cao", FAIL, 0.9), ("loi", ERROR, None), ("dat", PASS, 0.6),
                          ("xem", REVIEW, 0.7), ("dat_cao", PASS, 0.65)]]
    rs.sort(key=rank_key, reverse=True)
    assert [r.key for r in rs] == ["dat_cao", "dat", "xem", "loai_diem_cao", "loi"]


def result(key: str, status: str, email: str | None) -> CVResult:
    return CVResult(key=key, file=f"{key}.pdf", status=status,
                    profile=CVProfile(thong_tin_ca_nhan=Contact(ho_ten=key.upper(), email=email)))


class FakeSMTP:
    sent: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        if password != "ok":
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg):
        FakeSMTP.sent.append(msg["To"].addresses[0].addr_spec)


def test_send_invites_only_pass_and_never_twice(monkeypatch, tmp_path):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    agent = EmailAgent(smtp=SmtpSettings("h", 587, "hr@x.vn", "ok"), outbox=tmp_path / "out")
    log = SentLog(tmp_path / "log.json")
    rs = [result("a", PASS, "a@x.vn"), result("b", REVIEW, "b@x.vn"), result("c", FAIL, "c@x.vn"),
          result("d", PASS, None)]
    send_invites(rs, agent, "Dev", "jd1", log)
    assert FakeSMTP.sent == ["a@x.vn"]
    assert [r.email for r in rs] == ["sent", "", "", "no_email"]

    again = [result("a", PASS, "a@x.vn")]  # đánh giá lại cùng CV, cùng JD: không gửi trùng (kể cả sau khi khởi động lại)
    send_invites(again, agent, "Dev", "jd1", SentLog(tmp_path / "log.json"))
    assert FakeSMTP.sent == ["a@x.vn"] and again[0].email == "sent_before"

    rs[1].status = PASS  # nhà tuyển dụng duyệt hồ sơ cần xem xét
    send_invites(rs, agent, "Dev", "jd1", log)
    assert FakeSMTP.sent == ["a@x.vn", "b@x.vn"]


def test_send_invites_drafts_are_not_logged(tmp_path):
    agent = EmailAgent(outbox=tmp_path / "out")
    log = SentLog(tmp_path / "log.json")
    rs = [result("a", PASS, "a@x.vn")]
    send_invites(rs, agent, "Dev", "jd1", log)
    assert rs[0].email == "saved" and "jd1:a:moi_phong_van" not in log


def test_send_invites_login_error_marks_and_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    agent = EmailAgent(smtp=SmtpSettings("h", 587, "hr@x.vn", "sai"))
    rs = [result("a", PASS, "a@x.vn")]
    with pytest.raises(smtplib.SMTPAuthenticationError):
        send_invites(rs, agent, "Dev", "jd1", SentLog(tmp_path / "log.json"))
    assert rs[0].email.startswith("error:")


def test_draw_blocks_scales_boxes_to_image():
    img = Image.new("RGB", (200, 100), "white")  # trang 100 × 50 đơn vị, ảnh phóng 2 lần
    out = draw_blocks(img, (100.0, 50.0), [(1, Block("x", 10, 20, 40, 45, label="text"))])
    pad = out.height - img.height  # dải lề phía trên dành cho nhãn
    assert out.width == img.width and pad > 0
    assert out.getpixel((20, 60 + pad)) != (255, 255, 255)   # bên trong khối (toạ độ ảnh = 2 × toạ độ trang)
    assert out.getpixel((150, 60 + pad)) == (255, 255, 255)  # vùng trống
    assert draw_blocks(img, (100.0, 50.0), []).size == img.size  # tắt lớp khối: giữ nguyên ảnh
