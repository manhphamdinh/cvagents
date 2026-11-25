"""Nguồn máy tìm kiếm: tìm CV công khai trên Internet theo truy vấn, rồi kiểm tra từng kết quả như danh sách URL.

Dùng API của máy tìm kiếm, không cào trang kết quả của Google / Bing (robots.txt của các trang này cấm):

    searxng  SearXNG tự cài, miễn phí, cần bật định dạng json (search.formats trong settings.yml); `tim_kiem_url`
    brave    Brave Search API, khoá ở biến môi trường SEARCH_API_KEY
    google   Google Programmable Search (Custom Search JSON API), SEARCH_API_KEY và GOOGLE_CSE_ID

Truy vấn gợi ý: `"curriculum vitae" python developer filetype:pdf`, `CV "kinh nghiệm làm việc" kế toán filetype:pdf`.
Kết quả là tệp thì tải như CV; là trang HTML thì lưu phiên bản trang và tải các tệp CV cùng tên miền mà trang liên kết
tới. Kết quả vẫn qua robots.txt, giãn cách và các ngưỡng của JobBoardSource. CV tìm được là dữ liệu cá nhân của người
khác: chỉ dùng cho mục đích được phép và để bộ dọn dẹp (`luu_toi_da_ngay`) xoá đúng hạn.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from cvagents.collector.models import SEARCH, SOURCE_LABEL
from cvagents.collector.sources.api import _get
from cvagents.collector.sources.net import HttpClient
from cvagents.collector.sources.web import JobBoardSource
from cvagents.common.config import load_config

PROVIDERS = ("searxng", "brave", "google")
MAX_RESULT_PAGES = 5  # số trang kết quả tối đa của một truy vấn


class SearchSource(JobBoardSource):
    nguon = SEARCH

    def __init__(self, queries: list[str], ten: str | None = None, provider: str | None = None,
                 api_url: str | None = None, api_key: str | None = None, cse_id: str | None = None,
                 max_results: int | None = None, api_client: HttpClient | None = None, max_depth: int = 0, **kw):
        cfg = load_config()["collector"]
        self.queries = [q.strip() for q in queries if q.strip()]
        super().__init__([], ten=ten or f"{SOURCE_LABEL[SEARCH]}: {'; '.join(self.queries)}", max_depth=max_depth,
                         **kw)
        self.provider = provider or cfg["tim_kiem_api"]
        self.api_url = api_url or cfg["tim_kiem_url"]
        self.api_key = api_key or os.environ.get("SEARCH_API_KEY")
        self.cse_id = cse_id or os.environ.get("GOOGLE_CSE_ID")
        self.max_results = max_results or cfg["tim_kiem_max_ket_qua"]
        headers = {"Accept": "application/json"}
        if self.provider == "brave" and self.api_key:
            headers["X-Subscription-Token"] = self.api_key
        self.api = api_client or HttpClient(headers=headers, respect_robots=False)  # API có khoá, như ApiSource

    def run_start_urls(self) -> list[str]:
        if self.provider not in PROVIDERS:
            raise ValueError(f"tim_kiem_api phải là một trong {', '.join(PROVIDERS)}, không phải {self.provider!r}")
        if self.provider in ("brave", "google") and not self.api_key:
            raise ValueError("Chưa đặt SEARCH_API_KEY cho API tìm kiếm " + self.provider)
        if self.provider == "google" and not self.cse_id:
            raise ValueError("Chưa đặt GOOGLE_CSE_ID (mã công cụ tìm kiếm của Programmable Search)")
        urls: list[str] = []
        for q in self.queries:
            try:
                urls += self.search(q)
            except (OSError, ValueError) as e:  # URLError là OSError; ValueError: JSON hỏng
                raise ConnectionError(f"Không tìm được {q!r} qua {self.provider}: {e}") from e
        return list(dict.fromkeys(u for u in urls if u.startswith(("http://", "https://"))))

    def search(self, q: str) -> list[str]:
        """URL kết quả của một truy vấn, tối đa `max_results`."""
        out: list[str] = []
        for page in range(MAX_RESULT_PAGES):
            if self.provider == "searxng":
                url, items, field = f"{self.api_url}?{urlencode({'q': q, 'format': 'json', 'pageno': page + 1})}", \
                    "results", "url"
            elif self.provider == "brave":
                url, items, field = "https://api.search.brave.com/res/v1/web/search?" + urlencode(
                    {"q": q, "count": 20, "offset": page}), "web.results", "url"
            else:
                url, items, field = "https://www.googleapis.com/customsearch/v1?" + urlencode(
                    {"key": self.api_key, "cx": self.cse_id, "q": q, "num": 10, "start": 1 + 10 * page}), "items", "link"
            found = [it[field] for it in _get(json.loads(self.api.get(url).body.decode("utf-8")), items) or []
                     if isinstance(it, dict) and it.get(field)]
            out += found
            if not found or len(out) >= self.max_results:
                break
        return out[:self.max_results]
