import smtplib
from email import message_from_bytes, policy

import pytest

from cvagents.common.schemas import AnalysisReport
from cvagents.mailer import INVITE, REJECT, EmailAgent, SmtpSettings, decide


def report(ok: bool) -> AnalysisReport:
    return AnalysisReport(diem_tong=0.8 if ok else 0.3, diem_thanh_phan={}, phu_hop=ok)


def test_decide_follows_analysis_verdict():
    assert decide(report(True)) == INVITE
    assert decide(report(False)) == REJECT


def test_compose_fills_placeholders_and_keeps_unknown():
    agent = EmailAgent(cong_ty="ABC", nguoi_gui="HR")
    d = agent.compose("a@x.vn", "Nguyễn Văn An", "Backend Developer", INVITE)
    assert "Backend Developer" in d.subject and "ABC" in d.subject
    assert d.body.startswith("Chào Nguyễn Văn An,") and "HR" in d.body
    d = agent.compose("a@x.vn", None, "Dev", REJECT, {"subject": "{vi_tri} {la}", "body": "{ten}"})
    assert d.subject == "Dev {la}" and d.body == "Anh/Chị"


def test_send_without_smtp_saves_eml(tmp_path):
    agent = EmailAgent(outbox=tmp_path, cong_ty="ABC", nguoi_gui="HR")
    res = agent.send([agent.compose("an@x.vn", "Nguyễn Văn An", "Dev", INVITE)])
    assert res[0].status == "saved"
    msg = message_from_bytes(open(res[0].detail, "rb").read(), policy=policy.default)
    assert msg["To"].addresses[0].addr_spec == "an@x.vn"
    assert "Nguyễn Văn An" in msg.get_content()


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout):
        self.host = host

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
        if msg["To"].addresses[0].addr_spec == "bad@x.vn":
            raise smtplib.SMTPRecipientsRefused({"bad@x.vn": (550, b"no")})
        FakeSMTP.sent.append(msg)


def test_send_smtp_reports_each_recipient(monkeypatch, tmp_path):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    agent = EmailAgent(smtp=SmtpSettings("h", 587, "hr@x.vn", "ok"), outbox=tmp_path)
    drafts = [agent.compose(t, None, "Dev", INVITE) for t in ("a@x.vn", "bad@x.vn")]
    res = agent.send(drafts)
    assert [r.status for r in res] == ["sent", "error"]
    assert len(FakeSMTP.sent) == 1 and not list(tmp_path.iterdir())


def test_send_smtp_bad_login_raises(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    agent = EmailAgent(smtp=SmtpSettings("h", 587, "hr@x.vn", "sai"))
    with pytest.raises(smtplib.SMTPAuthenticationError):
        agent.send([agent.compose("a@x.vn", None, "Dev", INVITE)])


def test_smtp_check_logs_in_without_sending(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    SmtpSettings("h", 587, "hr@x.vn", "ok").check()
    assert FakeSMTP.sent == []
    with pytest.raises(smtplib.SMTPAuthenticationError):
        SmtpSettings("h", 587, "hr@x.vn", "sai").check()


def test_smtp_settings_from_env(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert SmtpSettings.from_env() is None
    monkeypatch.setenv("SMTP_USER", "hr@x.vn")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    s = SmtpSettings.from_env()
    assert (s.user, s.host, s.port) == ("hr@x.vn", "smtp.gmail.com", 587)
