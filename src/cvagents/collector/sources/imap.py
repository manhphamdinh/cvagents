"""Nguồn email: tệp đính kèm trong hộp thư tuyển dụng qua IMAP (Gmail, Outlook, máy chủ công ty).

Thu thập tăng dần theo UID: chỉ đọc thư có UID lớn hơn UID cuối đã xử lý; nếu máy chủ đổi UIDVALIDITY (hộp thư bị
tạo lại) thì đọc lại từ đầu, tệp trùng sẽ bị loại ở bước loại trùng. Hộp thư được mở chỉ đọc, không đánh dấu đã đọc.
Tài khoản đọc từ biến môi trường IMAP_USER, IMAP_PASSWORD (Gmail cần bật IMAP và dùng App Password).
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from collections.abc import Iterator
from datetime import date
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime

from cvagents.collector.models import IMAP, SOURCE_LABEL, Incoming
from cvagents.collector.sources.base import SourceState, job_from_subject
from cvagents.common.config import load_config

_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY (\d+)")
_SKIP_TYPES = ("image/gif",)  # biểu tượng trong chữ ký thư


class ImapSource:
    nguon = IMAP

    def __init__(self, user: str, password: str, host: str | None = None, port: int | None = None,
                 folder: str | None = None, since: date | None = None, ten: str | None = None,
                 conn_factory=None):
        cfg = load_config()["collector"]
        self.user, self.password = user, password
        self.host, self.port = host or cfg["imap_host"], int(port or cfg["imap_port"])
        self.folder = folder or cfg["imap_folder"]
        self.since = since
        self.ten = ten or f"{SOURCE_LABEL[self.nguon]}: {user}/{self.folder}"
        self._conn_factory = conn_factory or (lambda: imaplib.IMAP4_SSL(self.host, self.port, timeout=30))

    @classmethod
    def from_env(cls, **kw) -> ImapSource | None:
        user, password = os.environ.get("IMAP_USER", ""), os.environ.get("IMAP_PASSWORD", "")
        if not (user and password):
            return None
        return cls(user, password, host=os.environ.get("IMAP_HOST"), **kw)

    def fetch(self, state: SourceState) -> Iterator[Incoming]:
        conn = self._conn_factory()
        try:
            conn.login(self.user, self.password)  # sai tài khoản: imaplib.IMAP4.error, cả nguồn dừng
            typ, data = conn.select(_quote(self.folder), readonly=True)
            if typ != "OK":
                raise imaplib.IMAP4.error(f"Không mở được thư mục {self.folder}")
            validity = self._uidvalidity(conn)
            last = int(state.get(f"uid:{validity}") or 0)
            crit = f"UID {last + 1}:*"
            if self.since:
                crit += f" SINCE {self.since.strftime('%d-%b-%Y')}"
            typ, data = conn.uid("SEARCH", None, crit)
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last)  # "n:*" luôn trả về UID cuối
            for uid in uids:
                typ, msg_data = conn.uid("FETCH", str(uid), "(RFC822)")
                raw = next((p[1] for p in msg_data if isinstance(p, tuple)), None)
                if typ == "OK" and raw:
                    yield from self.attachments(email.message_from_bytes(raw, policy=policy.default), uid)
                state.set(f"uid:{validity}", str(uid))
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def _uidvalidity(self, conn) -> str:
        typ, data = conn.response("UIDVALIDITY")
        if data and data[0]:
            return data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        typ, data = conn.status(_quote(self.folder), "(UIDVALIDITY)")
        m = _UIDVALIDITY_RE.search(data[0] if data else b"")
        return m.group(1).decode() if m else "0"

    def attachments(self, msg: EmailMessage, uid: int | str = "") -> Iterator[Incoming]:
        """Mọi tệp đính kèm của thư, kể cả trong thư được chuyển tiếp dạng đính kèm (.eml)."""
        ten, addr = parseaddr(str(msg.get("From", "")))
        subject = str(msg.get("Subject", "")) or None
        try:
            when = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
        except (TypeError, ValueError):
            when = None
        ref = str(msg.get("Message-ID", "")) or f"uid:{uid}"
        for part in msg.walk():
            if part.is_multipart() or part.get_content_type() in _SKIP_TYPES:
                continue
            name = part.get_filename()
            if not name or (part.get_content_disposition() == "inline" and part.get_content_maintype() == "image"):
                continue
            data = part.get_payload(decode=True) or b""
            yield Incoming(name, data, self.nguon, tham_chieu=ref, nguoi_gui=addr.lower() or None,
                           ten_nguoi_gui=ten or None, tieu_de=subject, vi_tri=job_from_subject(subject),
                           thoi_diem=when)


def _quote(folder: str) -> str:
    return folder if folder.startswith('"') else f'"{folder}"'
