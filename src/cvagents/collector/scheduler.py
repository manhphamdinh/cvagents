"""Lập lịch cho CV_Collector: thu thập định kỳ theo chu kỳ hoặc vào các giờ cố định trong ngày (chế độ theo dõi),
dọn hồ sơ quá hạn mỗi ngày một lần."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from cvagents.collector.agent import CVCollectorAgent
from cvagents.collector.models import RunReport
from cvagents.common.config import load_config

log = logging.getLogger("cvagents.collector")
DAY = 86400


def parse_times(spec: str) -> list[str]:
    """Chuỗi giờ "8:00, 20:30" → ["08:00", "20:30"]; ValueError nếu sai định dạng giờ."""
    out = []
    for part in spec.replace(";", ",").split(","):
        if part.strip():
            t = datetime.strptime(part.strip(), "%H:%M")
            out.append(f"{t.hour:02d}:{t.minute:02d}")
    if not out:
        raise ValueError("Chưa có giờ chạy nào (vd. 08:00, 20:00)")
    return sorted(set(out))


def _slots(times: list[str], day: datetime) -> list[datetime]:
    return [day.replace(hour=int(t[:2]), minute=int(t[3:]), second=0, microsecond=0) for t in times]


def next_slot(times: list[str], now: datetime | None = None) -> datetime:
    """Mốc giờ chạy kế tiếp (sau `now`)."""
    now = now or datetime.now()
    return min(s for s in _slots(times, now) + _slots(times, now + timedelta(days=1)) if s > now)


def previous_slot(times: list[str], now: datetime | None = None) -> datetime:
    """Mốc giờ chạy gần nhất đã qua (≤ `now`)."""
    now = now or datetime.now()
    return max(s for s in _slots(times, now - timedelta(days=1)) + _slots(times, now) if s <= now)


def run_periodically(agent: CVCollectorAgent, interval: float | None = None, stop: threading.Event | None = None,
                     max_runs: int | None = None, on_run: Callable[[RunReport], None] | None = None,
                     at: list[str] | None = None) -> int:
    """Chạy `agent.collect()` mỗi `interval` giây, hoặc vào các giờ `at` (["08:00", "20:00"]) nếu có, cho tới khi
    `stop` được đặt (hoặc đủ `max_runs` lần, Ctrl+C). Theo chu kỳ thì chạy ngay lần đầu; theo giờ thì chờ tới giờ.

    `on_run` nhận báo cáo từng lần chạy, ví dụ để chuyển ngay hồ sơ mới cho Extractor. Trả về số lần đã chạy.
    """
    interval = load_config()["collector"]["chu_ky_giay"] if interval is None else interval
    stop = stop or threading.Event()
    runs, last_purge, when = 0, 0.0, datetime.min
    while not stop.is_set():
        if at:
            when = next_slot(at, max(datetime.now(), when))  # không chạy hai lần cùng một mốc khi thức dậy sớm
            log.info("Lần thu thập kế tiếp lúc %s", when.isoformat(sep=" ", timespec="minutes"))
            if stop.wait((when - datetime.now()).total_seconds()):
                break
        if time.monotonic() - last_purge > DAY or not last_purge:
            agent.purge()
            last_purge = time.monotonic()
        report = agent.collect()
        runs += 1
        if on_run:
            try:
                on_run(report)
            except Exception:  # lỗi ở bước sau (Extractor, Email) không làm dừng việc thu thập
                log.exception("Lỗi khi xử lý kết quả lần chạy %s", report.id)
        if max_runs and runs >= max_runs:
            break
        if not at:
            stop.wait(interval)
    return runs


def setup_logging(path: str | Path | None = None, level: int = logging.INFO) -> Path:
    """Ghi nhật ký của tác tử ra tệp (data/logs/collector.log) và màn hình."""
    p = Path(path or load_config()["collector"]["log_file"])
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    lg = logging.getLogger("cvagents")
    lg.setLevel(level)
    if not any(isinstance(h, logging.FileHandler) and Path(h.baseFilename) == p.resolve() for h in lg.handlers):
        fh = logging.FileHandler(p, encoding="utf-8")
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    if not any(type(h) is logging.StreamHandler for h in lg.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        lg.addHandler(sh)
    return p
