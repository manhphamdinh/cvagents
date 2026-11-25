"""Nguồn Google Drive: thư mục Drive nơi form tuyển dụng (Google Forms) hoặc đồng nghiệp lưu CV.

Cần `pip install google-api-python-client google-auth` và một service account được chia sẻ quyền xem thư mục
(tệp khoá JSON đặt bằng biến môi trường GOOGLE_APPLICATION_CREDENTIALS hoặc tham số `credentials`).
Google Docs được xuất thành DOCX. Tệp đã lấy (theo id + thời điểm sửa) không tải lại.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

from cvagents.collector.models import DRIVE, SOURCE_LABEL, Incoming
from cvagents.collector.sources.base import SourceState

GDOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FOLDER_MIME = "application/vnd.google-apps.folder"


def build_service(credentials: str | None = None):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    path = credentials or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        raise ValueError("Chưa có tệp khoá service account (GOOGLE_APPLICATION_CREDENTIALS)")
    creds = service_account.Credentials.from_service_account_file(
        path, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


class GoogleDriveSource:
    nguon = DRIVE

    def __init__(self, folder_id: str, ten: str | None = None, credentials: str | None = None, service=None,
                 recursive: bool = True):
        self.folder_id = folder_id
        self.ten = ten or f"{SOURCE_LABEL[self.nguon]}: {folder_id}"
        self._service, self._credentials = service, credentials
        self.recursive = recursive

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self._credentials)
        return self._service

    def _list(self, folder_id: str, path: str = "") -> Iterator[tuple[dict, str]]:
        token = None
        while True:
            resp = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed = false", pageToken=token, pageSize=200,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, owners(displayName, emailAddress))",
            ).execute()
            for f in resp.get("files", []):
                if f["mimeType"] == FOLDER_MIME:
                    if self.recursive:
                        yield from self._list(f["id"], f["name"])
                else:
                    yield f, path
            token = resp.get("nextPageToken")
            if not token:
                return

    def fetch(self, state: SourceState) -> Iterator[Incoming]:
        for f, sub in self._list(self.folder_id):
            khoa = f"{f['id']}|{f.get('modifiedTime', '')}"
            if state.seen(khoa):
                continue
            name, err, data = f["name"], "", b""
            try:
                if f["mimeType"] == GDOC:
                    data = self.service.files().export_media(fileId=f["id"], mimeType=DOCX).execute()
                    name += ".docx"
                else:
                    data = self.service.files().get_media(fileId=f["id"]).execute()
            except Exception as e:  # googleapiclient.errors.HttpError, lỗi mạng
                err = f"Không tải được từ Drive: {e}"
            owner = (f.get("owners") or [{}])[0]
            when = f.get("modifiedTime")
            yield Incoming(name, data, self.nguon, tham_chieu=f"https://drive.google.com/file/d/{f['id']}",
                           nguoi_gui=owner.get("emailAddress"), ten_nguoi_gui=owner.get("displayName"),
                           vi_tri=sub or None, error=err,
                           thoi_diem=datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None)
            if not err:
                state.set(khoa)
