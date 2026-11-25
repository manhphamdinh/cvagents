"""HTTP dùng chung cho các nguồn web: User-Agent riêng, robots.txt, giãn cách giữa các yêu cầu, thử lại có giãn cách.

Lỗi tạm thời (429, 5xx, hết thời gian chờ, mất kết nối) được thử lại tối đa `crawl_retries` lần; lần thứ k chờ
`crawl_backoff · 2^k` giây, hoặc theo Retry-After của máy chủ (tối đa 60 giây). Lỗi 4xx khác không thử lại.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from urllib.robotparser import RobotFileParser

from cvagents.common.config import load_config

MAX_BACKOFF = 60.0


def _transient(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    return True  # hết thời gian chờ, mất kết nối, máy chủ ngắt giữa chừng


def _retry_after(e: Exception) -> float | None:
    v = e.headers.get("Retry-After") if isinstance(e, urllib.error.HTTPError) and e.headers else None
    try:
        return float(v) if v else None
    except ValueError:  # dạng ngày giờ HTTP: dùng giãn cách mặc định
        return None


@dataclass
class Response:
    url: str
    status: int
    headers: Message
    body: bytes

    @property
    def content_type(self) -> str:
        return (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()

    def filename(self) -> str:
        """Tên tệp theo Content-Disposition, nếu không có thì theo đường dẫn URL."""
        cd = self.headers.get("Content-Disposition") or ""
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
        if m:
            return unquote(m.group(1)).strip()
        return unquote(PurePosixPath(urlsplit(self.url).path).name) or "tai_ve"


class HttpClient:
    def __init__(self, delay: float | None = None, timeout: float | None = None, user_agent: str | None = None,
                 headers: dict[str, str] | None = None, respect_robots: bool = True, sleep=time.sleep,
                 retries: int | None = None, backoff: float | None = None):
        cfg = load_config()["collector"]
        self.delay = cfg["crawl_delay"] if delay is None else delay
        self.retries = cfg["crawl_retries"] if retries is None else retries
        self.backoff = cfg["crawl_backoff"] if backoff is None else backoff
        self.timeout = timeout or cfg["http_timeout"]
        self.user_agent = user_agent or cfg["user_agent"]
        self.headers = headers or {}
        self.respect_robots = respect_robots
        self._sleep = sleep
        self._last = 0.0
        self._robots: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlsplit(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                r = self._get(base + "/robots.txt")
                rp.parse(r.body.decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as e:  # 4xx: không có robots.txt, được phép; 5xx: coi như cấm
                rp = None if e.code < 500 else False
            except (urllib.error.URLError, OSError):
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        if rp is False:
            return False
        return rp is None or rp.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float:
        p = urlsplit(url)
        rp = self._robots.get(f"{p.scheme}://{p.netloc}")
        d = rp.crawl_delay(self.user_agent) if rp else None
        return max(self.delay, float(d or 0))

    def get(self, url: str) -> Response:
        if not self.allowed(url):
            raise PermissionError(f"robots.txt không cho phép tải {url}")
        for attempt in range(self.retries + 1):
            wait = self.crawl_delay(url) - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            try:
                return self._get(url)
            except (urllib.error.URLError, OSError) as e:
                if attempt == self.retries or not _transient(e):
                    raise
                self._sleep(min(_retry_after(e) or self.backoff * 2 ** attempt, MAX_BACKOFF))
            finally:
                self._last = time.monotonic()

    def _get(self, url: str) -> Response:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, **self.headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return Response(r.geturl(), r.status, r.headers, r.read())
