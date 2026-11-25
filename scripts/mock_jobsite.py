"""Chạy trang tuyển dụng giả lập (kịch bản K3) từ một thư mục CV.

    python scripts/mock_jobsite.py data/raw --port 8765
    python scripts/collect.py --web http://127.0.0.1:8765/ --api http://127.0.0.1:8765/api/ung-tuyen
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cvagents.collector.mocksite import MockJobSite  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    site = MockJobSite(args.folder)
    srv, url = site.serve(port=args.port)
    print(f"Trang giả lập: {url}  ({len(site.files)} tệp, {len(site.jobs)} tin)  API: {url}api/ung-tuyen")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
