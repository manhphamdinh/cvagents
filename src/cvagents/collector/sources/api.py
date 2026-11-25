"""Nguồn API: danh sách hồ sơ ứng tuyển dạng JSON (API của ATS / nền tảng tuyển dụng, API giả lập của đề tài).

Mặc định nhận dạng

    {"items": [{"id": "...", "ho_ten": "...", "email": "...", "vi_tri": "...",
                "ten_tep": "cv.pdf", "cv_url": "https://..."  hoặc  "cv_base64": "..."}],
     "next": "https://...?page=2"}

Tên các trường đổi được qua `fields` để khớp API thật. Hồ sơ đã lấy (theo id) không tải lại.
"""
from __future__ import annotations

import base64
import binascii
import json
import urllib.error
from collections.abc import Iterator
from urllib.parse import urljoin

from cvagents.collector.models import API, SOURCE_LABEL, Incoming
from cvagents.collector.sources.base import SourceState
from cvagents.collector.sources.net import HttpClient

DEFAULT_FIELDS = {"items": "items", "next": "next", "id": "id", "ho_ten": "ho_ten", "email": "email",
                  "vi_tri": "vi_tri", "ten_tep": "ten_tep", "cv_url": "cv_url", "cv_base64": "cv_base64",
                  "thoi_diem": "thoi_diem"}


def _get(obj: dict, path: str):
    for k in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


class ApiSource:
    nguon = API

    def __init__(self, url: str, ten: str | None = None, token: str | None = None,
                 fields: dict[str, str] | None = None, client: HttpClient | None = None, max_pages: int = 100):
        self.url = url
        self.ten = ten or f"{SOURCE_LABEL[self.nguon]}: {url}"
        self.f = {**DEFAULT_FIELDS, **(fields or {})}
        headers = {"Accept": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
        self.client = client or HttpClient(delay=0, headers=headers, respect_robots=False)
        if client and token:
            self.client.headers.update(headers)
        self.max_pages = max_pages

    def fetch(self, state: SourceState) -> Iterator[Incoming]:
        url, pages = self.url, 0
        while url and pages < self.max_pages:
            try:
                r = self.client.get(url)
                body = json.loads(r.body.decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise ConnectionError(f"Không đọc được {url}: {e}") from e
            pages += 1
            items = body if isinstance(body, list) else (_get(body, self.f["items"]) or [])
            for it in items:
                yield from self._item(it, url, state)
            nxt = None if isinstance(body, list) else _get(body, self.f["next"])
            url = urljoin(url, nxt) if nxt else None

    def _item(self, it: dict, page_url: str, state: SourceState) -> Iterator[Incoming]:
        g = lambda k: _get(it, self.f[k])  # noqa: E731
        item_id = str(g("id") or "")
        khoa = item_id or str(g("cv_url") or "")
        if not khoa or state.seen(khoa):
            return
        meta = dict(tham_chieu=f"{self.url}#{item_id}" if item_id else str(g("cv_url")),
                    nguoi_gui=g("email"), ten_nguoi_gui=g("ho_ten"), vi_tri=g("vi_tri"))
        name = g("ten_tep") or f"{item_id or 'cv'}"
        data, err = b"", ""
        if g("cv_base64"):
            try:
                data = base64.b64decode(g("cv_base64"), validate=True)
            except (binascii.Error, ValueError) as e:
                err = f"cv_base64 không hợp lệ: {e}"
        elif g("cv_url"):
            try:
                r = self.client.get(urljoin(page_url, g("cv_url")))
                data = r.body
                if "." not in name:
                    name = r.filename()
            except (urllib.error.URLError, OSError) as e:
                err = f"Không tải được CV: {e}"
        else:
            err = "Hồ sơ không có tệp CV"
        yield Incoming(name, data, self.nguon, error=err, **meta)
        if not err:
            state.set(khoa)
