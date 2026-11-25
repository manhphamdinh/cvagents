"""Email_Agent: soạn và gửi thư kết quả sàng lọc cho ứng viên (mục 2.2.2).

Có tài khoản SMTP thì gửi thật; không có thì lưu từng thư thành tệp .eml trong thư mục outbox để xem trước.
Tài khoản đọc từ biến môi trường SMTP_USER, SMTP_PASSWORD (không ghi mật khẩu vào config.toml).
"""
from __future__ import annotations

import json
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from cvagents.common.config import load_config, load_resource
from cvagents.common.schemas import AnalysisReport

INVITE, REJECT, ACK = "moi_phong_van", "tu_choi", "xac_nhan"


@dataclass
class Draft:
    to: str
    ten: str | None
    loai: str  # INVITE | REJECT
    subject: str
    body: str


@dataclass
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str

    @classmethod
    def from_env(cls) -> SmtpSettings | None:
        cfg = load_config()["email"]
        user, password = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
        if not (user and password):
            return None
        return cls(os.environ.get("SMTP_HOST", cfg["smtp_host"]),
                   int(os.environ.get("SMTP_PORT", cfg["smtp_port"])), user, password)

    def check(self) -> None:
        """Đăng nhập thử, không gửi thư; không kết nối được hoặc sai tài khoản thì ném ngoại lệ của smtplib."""
        with smtplib.SMTP(self.host, self.port, timeout=30) as s:
            s.starttls()
            s.login(self.user, self.password)


@dataclass
class SendResult:
    to: str
    status: str  # "sent" | "saved" | "error"
    detail: str = ""


class _Keep(dict):
    """Giữ nguyên {khoá} chưa có giá trị thay vì báo lỗi khi người dùng sửa mẫu thư."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def decide(report: AnalysisReport) -> str:
    """Loại thư mặc định theo kết luận của CV/JD_Analysis_Agent; nhà tuyển dụng có thể đổi trước khi gửi."""
    return INVITE if report.phu_hop else REJECT


class EmailAgent:
    def __init__(self, smtp: SmtpSettings | None = None, outbox: str | Path | None = None,
                 cong_ty: str | None = None, nguoi_gui: str | None = None,
                 templates: dict[str, dict[str, str]] | None = None):
        cfg = load_config()["email"]
        self.smtp = smtp
        self.templates = {**self.default_templates(), **(templates or {})}
        self.outbox = Path(outbox or cfg["outbox"])
        self.cong_ty = cong_ty if cong_ty is not None else cfg["cong_ty"]
        self.nguoi_gui = nguoi_gui if nguoi_gui is not None else cfg["nguoi_gui"]

    @staticmethod
    def default_templates() -> dict[str, dict[str, str]]:
        return {k: dict(v) for k, v in load_resource("email_templates.json").items()}

    def compose(self, to: str, ten: str | None, vi_tri: str, loai: str,
                template: dict[str, str] | None = None) -> Draft:
        tpl = template or self.templates[loai]
        values = _Keep(ten=ten or "Anh/Chị", vi_tri=vi_tri or "ứng tuyển",
                       cong_ty=self.cong_ty, nguoi_gui=self.nguoi_gui)
        return Draft(to=to, ten=ten, loai=loai,
                     subject=tpl["subject"].format_map(values), body=tpl["body"].format_map(values))

    def to_message(self, d: Draft) -> EmailMessage:
        sender = self.smtp.user if self.smtp else "noreply@localhost"
        msg = EmailMessage()
        msg["Subject"] = d.subject
        msg["From"] = formataddr((self.nguoi_gui, sender))
        msg["To"] = formataddr((d.ten or "", d.to))
        msg["Message-ID"] = make_msgid()
        msg.set_content(d.body)
        return msg

    def send(self, drafts: list[Draft], dry_run: bool = False) -> list[SendResult]:
        """Gửi qua SMTP; nếu chưa cấu hình SMTP hoặc dry_run thì lưu .eml vào outbox."""
        if dry_run or self.smtp is None:
            return [self._save(d) for d in drafts]
        out = []
        with smtplib.SMTP(self.smtp.host, self.smtp.port, timeout=30) as s:
            s.starttls()
            s.login(self.smtp.user, self.smtp.password)  # sai tài khoản thì báo lỗi ngay, không gửi thư nào
            for d in drafts:
                try:
                    s.send_message(self.to_message(d))
                    out.append(SendResult(d.to, "sent"))
                except smtplib.SMTPException as e:
                    out.append(SendResult(d.to, "error", str(e)))
        return out

    def _save(self, d: Draft) -> SendResult:
        self.outbox.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe_to = re.sub(r"[^\w.@-]", "_", d.to)
        path = self.outbox / f"{stamp}_{d.loai}_{safe_to}.eml"
        path.write_bytes(bytes(self.to_message(d)))
        return SendResult(d.to, "saved", str(path))


class SentLog:
    """Nhật ký các thư đã gửi thật (không tính thư nháp), để đánh giá lại cùng CV với cùng JD không gửi trùng."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or load_config()["email"]["sent_log"])
        self._keys: set[str] = set(json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else set()

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str) -> None:
        self._keys.add(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self._keys), indent=1), encoding="utf-8")
