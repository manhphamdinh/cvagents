"""Nguồn trang tuyển dụng: crawler cho trang quản lý hồ sơ ứng tuyển (trang giả lập của đề tài, cổng tuyển dụng
của công ty, …) và danh sách URL theo dõi. Chỉ thu thập từ nguồn được phép:

- chỉ đi theo liên kết cùng tên miền với các trang bắt đầu; tuân thủ robots.txt (Disallow, Crawl-delay), giãn cách
  `crawl_delay` giây giữa hai yêu cầu, thử lại lỗi tạm thời (net.HttpClient);
- ngưỡng mỗi lần chạy: tối đa `crawl_max_pages` trang, `crawl_max_depth` bước liên kết, `crawl_max_tep` tệp CV;
  `crawl_max_loi_lien_tiep` lỗi tải liên tiếp thì dừng nguồn (trang có thể đã chặn crawler);
- mỗi trang HTML được so với phiên bản đã lưu (changes.track_page): trang mới hoặc thay đổi ≥ `nguong_thay_doi`
  được lưu thành phiên bản mới;
- tệp chưa tải thì tải; tệp đã tải chỉ tải lại khi trang chứa liên kết mới / đã thay đổi (hoặc tệp là URL bắt đầu),
  và chỉ chuyển cho tác tử khi nội dung khác lần trước.

Vị trí ứng tuyển lấy từ thẻ <h1> (hoặc <title>) của trang chứa liên kết tới CV; tên ứng viên lấy từ chữ trên liên
kết, hoặc từ thuộc tính data-ung-vien / data-email nếu trang có.
"""
from __future__ import annotations

import hashlib
import re
import urllib.error
from collections import deque
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urldefrag, urljoin, urlsplit

from cvagents.collector.changes import track_page
from cvagents.collector.models import PAGE_CHANGED, PAGE_NEW, PAGE_SAME, SOURCE_LABEL, URL_LIST, WEB, Incoming
from cvagents.collector.sources.base import SourceState
from cvagents.collector.sources.net import HttpClient
from cvagents.common.config import load_config

CV_SUFFIXES = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg"}
FILE_TYPES = ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/msword", "application/octet-stream", "image/png", "image/jpeg")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str, dict]] = []  # (href, chữ trên liên kết, thuộc tính)
        self.h1 = self.title = ""
        self._in: str | None = None
        self._a: tuple[str, dict] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._a, self._text = (a["href"], a), []
        elif tag in ("h1", "title"):
            self._in = tag

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            self.links.append((self._a[0], " ".join("".join(self._text).split()), self._a[1]))
            self._a = None
        elif tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._a:
            self._text.append(data)
        if self._in == "h1" and not self.h1:
            self.h1 = data.strip()
        elif self._in == "title":
            self.title += data


def _is_file(url: str) -> bool:
    return PurePosixPath(urlsplit(url).path).suffix.lower() in CV_SUFFIXES


class JobBoardSource:
    """Crawler theo liên kết từ một hoặc nhiều URL bắt đầu (độ sâu tối đa `max_depth`)."""
    nguon = WEB

    def __init__(self, start_url: str | list[str], ten: str | None = None, max_pages: int | None = None,
                 client: HttpClient | None = None, include: str | None = None, max_depth: int | None = None,
                 max_files: int | None = None, max_errors: int | None = None, threshold: float | None = None):
        cfg = load_config()["collector"]
        self.start_urls = [start_url] if isinstance(start_url, str) else list(start_url)
        self.ten = ten or f"{SOURCE_LABEL[self.nguon]}: " + (
            self.start_urls[0] if len(self.start_urls) == 1 else f"{len(self.start_urls)} URL")
        self.max_pages = max_pages or cfg["crawl_max_pages"]
        self.max_depth = cfg["crawl_max_depth"] if max_depth is None else max_depth
        self.max_files = max_files or cfg["crawl_max_tep"]
        self.max_errors = max_errors or cfg["crawl_max_loi_lien_tiep"]
        self.threshold = cfg["nguong_thay_doi"] if threshold is None else threshold  # changes.track_page
        self.client = client or HttpClient()
        self.include = re.compile(include) if include else None  # chỉ đi theo URL khớp (vd. r"/jobs/")
        self._sites: set[str] = set()

    def _same_site(self, url: str) -> bool:
        p = urlsplit(url)
        return p.netloc in self._sites and p.scheme in ("http", "https")

    def run_start_urls(self) -> list[str]:
        """URL bắt đầu của lần chạy này (nguồn tìm kiếm lấy từ kết quả tìm kiếm)."""
        return self.start_urls

    def fetch(self, state: SourceState) -> Iterator[Incoming]:
        starts = self.run_start_urls()
        self._sites = {urlsplit(u).netloc for u in starts}
        queue, visited = deque((u, 0) for u in starts), set(starts)
        pages = files = errors = 0
        while queue and pages < self.max_pages and files < self.max_files:
            url, depth = queue.popleft()
            try:
                r = self.client.get(url)
            except PermissionError:
                continue
            except (urllib.error.URLError, OSError) as e:
                if starts == [url] and not _is_file(url):
                    raise ConnectionError(f"Không truy cập được {url}: {e}") from e
                if depth == 0 and _is_file(url):  # tệp trong danh sách theo dõi: ghi lỗi tải vào sổ cái
                    yield Incoming(PurePosixPath(urlsplit(url).path).name, b"", self.nguon, tham_chieu=url,
                                   error=f"Không tải được: {e}")
                errors += 1
                if errors >= self.max_errors:  # ngưỡng lỗi liên tiếp: dừng nguồn, không tải tiếp
                    raise ConnectionError(f"Dừng sau {errors} lỗi tải liên tiếp, lỗi cuối: {url}: {e}") from e
                continue
            errors = 0
            pages += 1
            if r.content_type != "text/html":
                if depth == 0:  # URL bắt đầu là chính tệp CV (danh sách URL, kết quả tìm kiếm): luôn kiểm tra lại
                    files += 1
                    yield from self._accept(r, url, state, tieu_de=url)
                continue
            status, _ = track_page(state.store, url, self.ten, r.body, self.threshold)
            state.stats.trang += 1
            state.stats.trang_moi += status == PAGE_NEW
            state.stats.trang_thay_doi += status == PAGE_CHANGED
            page = _Page()
            page.feed(r.body.decode("utf-8", "replace"))
            vi_tri = page.h1 or page.title.strip() or None
            for href, text, attrs in page.links:
                link = urldefrag(urljoin(r.url, href))[0]
                if not self._same_site(link) or link in visited:
                    continue
                visited.add(link)
                if _is_file(link) or "download" in attrs:
                    # tệp mới thì tải; tệp đã tải chỉ kiểm tra lại khi trang chứa liên kết mới hoặc đã thay đổi
                    if (not state.seen(link) or status != PAGE_SAME) and files < self.max_files:
                        files += 1
                        yield from self._download(link, text, attrs, vi_tri, url, state)
                elif depth < self.max_depth and (not self.include or self.include.search(link)):
                    queue.append((link, depth + 1))

    def _download(self, link, text, attrs, vi_tri, page_url, state) -> Iterator[Incoming]:
        name = attrs.get("data-ung-vien") or (text if text and not _is_file("/" + text) else None)
        try:
            r = self.client.get(link)
        except PermissionError:
            return
        except (urllib.error.URLError, OSError) as e:
            yield Incoming(PurePosixPath(urlsplit(link).path).name, b"", self.nguon, tham_chieu=link,
                           ten_nguoi_gui=name, vi_tri=vi_tri, error=f"Không tải được: {e}")
            return
        yield from self._accept(r, link, state, nguoi_gui=attrs.get("data-email"), ten_nguoi_gui=name,
                                tieu_de=page_url, vi_tri=attrs.get("data-vi-tri") or vi_tri)

    def _accept(self, r, link, state, **meta) -> Iterator[Incoming]:
        """Chuyển tệp cho tác tử nếu là tệp mới hoặc nội dung khác lần tải trước (SHA-1 lưu trong state)."""
        sha = hashlib.sha1(r.body).hexdigest()
        if state.get(link) == sha:
            return
        yield Incoming(r.filename(), r.body, self.nguon, tham_chieu=link, **meta)
        state.set(link, sha)


class UrlListSource(JobBoardSource):
    """Danh sách URL theo dõi: mỗi URL là tệp CV hoặc trang chứa CV (trang cá nhân, trang hồ sơ công khai).

    Mỗi lần chạy kiểm tra lại từng URL: tệp đổi nội dung thì nhận lại, trang thay đổi thì lưu phiên bản mới và kiểm
    tra lại các tệp CV mà trang liên kết tới. Mặc định không đi theo liên kết sang trang khác (max_depth = 0).
    """
    nguon = URL_LIST

    def __init__(self, urls: list[str], ten: str | None = None, max_depth: int = 0, **kw):
        super().__init__(list(urls), ten=ten, max_depth=max_depth, **kw)
