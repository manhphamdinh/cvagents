"""CV_Collector_Agent dòng lệnh: thu thập hồ sơ từ các nguồn, tuỳ chọn chuyển ngay cho Extractor → Analysis → Email.

    python scripts/collect.py --folder data/incoming                                   # một lần
    python scripts/collect.py --folder data/incoming --imap --watch --interval 300     # theo dõi liên tục
    python scripts/collect.py --web http://127.0.0.1:8765/ --api http://127.0.0.1:8765/api/ung-tuyen
    python scripts/collect.py --url https://a.github.io/cv.pdf --url-file ds_url.txt        # danh sách URL theo dõi
    python scripts/collect.py --search "\"curriculum vitae\" python filetype:pdf" --search-api brave
    python scripts/collect.py --web http://127.0.0.1:8765/ --at 08:00,20:00               # đặt lịch: giờ cố định
    python scripts/collect.py --web http://127.0.0.1:8765/ --depth 2 --max-pages 30 --delay 2 --nguong-thay-doi 0.05
    python scripts/collect.py --trang                # các trang đã crawl, số phiên bản đã lưu, thay đổi gần nhất
    python scripts/collect.py --dataset resume_dataset.zip
    python scripts/collect.py --drive <folder_id> --drive-credentials key.json
    python scripts/collect.py --imap --jd data/raw/sample_jd.txt --vi-tri "Backend Developer" [--send]
    python scripts/collect.py --list moi            # liệt kê hồ sơ theo trạng thái
    python scripts/collect.py --purge               # xoá hồ sơ quá hạn (luu_toi_da_ngay)

--imap đọc tài khoản từ biến môi trường IMAP_USER, IMAP_PASSWORD (Gmail: App Password). Có --jd thì sau mỗi lần thu
thập, các hồ sơ mới được đánh giá với JD đó và ứng viên Đạt được gửi thư mời (mặc định lưu nháp, --send để gửi thật).
--search dùng API máy tìm kiếm (searxng | brave | google, xem sources/search.py); khoá ở SEARCH_API_KEY, GOOGLE_CSE_ID.
Các ngưỡng không truyền trên dòng lệnh lấy theo mục [collector] của config.toml.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # thay cho PYTHONPATH=src (xem README)

from cvagents.collector import (  # noqa: E402
    STATUS_LABEL, CollectorStore, CVCollectorAgent, RunReport, parse_times, run_periodically, setup_logging,
)
from cvagents.collector.sources import (  # noqa: E402
    ApiSource, DatasetSource, FolderSource, GoogleDriveSource, ImapSource, JobBoardSource, SearchSource, UrlListSource,
)
from cvagents.collector.sources.net import HttpClient  # noqa: E402
from cvagents.mailer import EmailAgent, SentLog, SmtpSettings  # noqa: E402

log = logging.getLogger("cvagents.collector")


def build_sources(args) -> list:
    sources = [FolderSource(p) for p in args.folder]
    if args.imap:
        src = ImapSource.from_env(folder=args.imap_folder)
        if src is None:
            raise SystemExit("Chưa đặt IMAP_USER / IMAP_PASSWORD nên không đọc được hộp thư.")
        sources.append(src)
    limits = dict(max_pages=args.max_pages, max_files=args.max_files, threshold=args.nguong_thay_doi)
    client = lambda: HttpClient(delay=args.delay)  # noqa: E731  (mỗi nguồn một bộ đếm giãn cách)
    sources += [JobBoardSource(u, max_depth=args.depth, client=client(), **limits) for u in args.web]
    urls = list(args.url)
    for f in args.url_file:
        urls += [x.strip() for x in Path(f).read_text(encoding="utf-8-sig").splitlines()
                 if x.strip() and not x.startswith("#")]
    if urls:
        sources.append(UrlListSource(urls, client=client(), **limits))
    if args.search:
        sources.append(SearchSource(args.search, provider=args.search_api, api_url=args.search_url, client=client(),
                                    **limits))
    sources += [ApiSource(u, token=args.api_token) for u in args.api]
    sources += [DatasetSource(p) for p in args.dataset]
    sources += [GoogleDriveSource(f, credentials=args.drive_credentials) for f in args.drive]
    return sources


def print_report(r: RunReport) -> None:
    print(f"\nLần chạy #{r.id} ({r.bat_dau} → {r.ket_thuc})")
    print(f"{'Nguồn':45} {'Tệp':>5} {'Hợp lệ':>7} {'Trùng':>6} {'Gần trùng':>9} {'Không HL':>8} {'Lỗi tải':>7} {'s/hồ sơ':>8}")
    for s in [*r.nguon, r.total()]:
        print(f"{s.ten[:45]:45} {s.thu:5} {s.hop_le:7} {s.trung:6} {s.gan_trung:9} {s.khong_hop_le:8} {s.loi_tai:7} "
              f"{s.giay_moi_ho_so:8.3f}" + (f"   LỖI: {s.loi}" if s.loi else ""))
    pages = [s for s in r.nguon if s.trang]
    for s in pages:
        print(f"  {s.ten[:45]:45} {s.trang} trang: {s.trang_moi} mới, {s.trang_thay_doi} thay đổi")


def print_pages(store: CollectorStore) -> None:
    print(f"{'Kiểm tra lần cuối':19} {'Lần':>4} {'Phiên bản':>9} {'Thay đổi cuối':>13} {'Lưu gần nhất':19}  URL")
    for p in store.pages_seen():
        print(f"{p['lan_cuoi'].replace('T', ' '):19} {p['so_lan']:4} {p['so_phien_ban']:9} "
              f"{p['ty_le_lan_cuoi']:13.1%} {(p['thay_doi_cuoi'] or '').replace('T', ' '):19}  {p['url']}")


def make_screener(args, collector: CVCollectorAgent):
    """Hàm chạy sau mỗi lần thu thập: Extractor → Analysis → gửi thư mời cho hồ sơ mới."""
    from cvagents.analysis import CVJDAnalysisAgent
    from cvagents.analysis.embedder import HashingEmbedder, SentenceTransformerEmbedder, model_for_lang
    from cvagents.extractor import CVExtractorAgent
    from cvagents.screening import STATUS_LABEL as SCREEN_LABEL, content_key, screen_files, send_invites

    jd_text = Path(args.jd).read_text(encoding="utf-8")
    extractor, agents = CVExtractorAgent(), {}

    def analysis_for_lang(lang: str):
        model = "hashing" if args.hashing else model_for_lang(lang)
        if model not in agents:
            agents[model] = CVJDAnalysisAgent(embedder=HashingEmbedder() if args.hashing else
                                              SentenceTransformerEmbedder(model_name=model))
        return agents[model], model

    smtp = SmtpSettings.from_env() if args.send else None
    if args.send and smtp is None:
        raise SystemExit("Chưa đặt SMTP_USER / SMTP_PASSWORD nên không gửi được.")
    mailer = EmailAgent(smtp=smtp)

    def screen(_report: RunReport) -> None:
        pending = collector.pending()
        if args.vi_tri_loc:
            pending = [c for c in pending if (c.vi_tri or "").lower() == args.vi_tri_loc.lower()]
        if not pending:
            return
        by_key = {c.key: c for c in pending}

        def done(r):
            c = by_key.get(r.key)
            if c:
                collector.mark(c, r.profile, r.error)

        results = screen_files([(c.ten_tep, Path(c.duong_dan)) for c in pending], jd_text, extractor,
                               analysis_for_lang, on_done=done)
        send_invites(results, mailer, args.vi_tri or "ứng tuyển", content_key(jd_text), SentLog())
        for r in results:
            score = f"{r.report.diem_tong:.2f}" if r.report else "  — "
            print(f"  {score}  {SCREEN_LABEL[r.status]:16} {r.file}  {r.email or r.error}")

    return screen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--folder", action="append", default=[], help="thư mục chứa CV (lặp lại được)")
    ap.add_argument("--imap", action="store_true", help="đọc tệp đính kèm trong hộp thư IMAP (biến môi trường)")
    ap.add_argument("--imap-folder", default=None, help="thư mục / nhãn Gmail, mặc định theo config.toml")
    ap.add_argument("--web", action="append", default=[], help="URL trang tuyển dụng để crawl theo liên kết")
    ap.add_argument("--url", action="append", default=[], help="URL tệp CV / trang chứa CV cần theo dõi")
    ap.add_argument("--url-file", action="append", default=[], help="tệp văn bản, mỗi dòng một URL theo dõi")
    ap.add_argument("--search", action="append", default=[], help="truy vấn tìm CV qua API máy tìm kiếm")
    ap.add_argument("--search-api", default=None, choices=["searxng", "brave", "google"])
    ap.add_argument("--search-url", default=None, help="địa chỉ SearXNG (mặc định theo config.toml)")
    ap.add_argument("--depth", type=int, default=None, help="ngưỡng: số bước liên kết tối đa từ trang bắt đầu")
    ap.add_argument("--max-pages", type=int, default=None, help="ngưỡng: số trang tối đa mỗi nguồn mỗi lần chạy")
    ap.add_argument("--max-files", type=int, default=None, help="ngưỡng: số tệp CV tối đa mỗi nguồn mỗi lần chạy")
    ap.add_argument("--delay", type=float, default=None, help="ngưỡng: số giây tối thiểu giữa hai yêu cầu")
    ap.add_argument("--nguong-thay-doi", type=float, default=None,
                    help="tỷ lệ chữ thay đổi tối thiểu (0–1) để lưu phiên bản trang mới")
    ap.add_argument("--api", action="append", default=[], help="URL API danh sách hồ sơ (JSON)")
    ap.add_argument("--api-token", default=None)
    ap.add_argument("--dataset", action="append", default=[], help="thư mục hoặc .zip bộ dữ liệu CV")
    ap.add_argument("--drive", action="append", default=[], help="id thư mục Google Drive")
    ap.add_argument("--drive-credentials", default=None, help="tệp khoá service account")
    ap.add_argument("--inbox", default=None, help="thư mục lưu hồ sơ và sổ cái (mặc định theo config.toml)")
    ap.add_argument("--ack", action="store_true", help="gửi thư xác nhận đã nhận hồ sơ")
    ap.add_argument("--watch", action="store_true", help="thu thập định kỳ tới khi Ctrl+C")
    ap.add_argument("--interval", type=float, default=None, help="chu kỳ (giây) ở chế độ --watch")
    ap.add_argument("--at", type=parse_times, default=None,
                    help="đặt lịch: thu thập vào các giờ cố định mỗi ngày, vd. 08:00,20:00 (chạy tới khi Ctrl+C)")
    ap.add_argument("--jd", default=None, help="đánh giá ngay hồ sơ mới với JD này")
    ap.add_argument("--vi-tri", default=None, help="tên vị trí dùng trong thư mời")
    ap.add_argument("--vi-tri-loc", default=None, help="chỉ đánh giá hồ sơ ứng tuyển vị trí này")
    ap.add_argument("--hashing", action="store_true", help="dùng embedder băm (thử nhanh, không cần mô hình)")
    ap.add_argument("--send", action="store_true", help="gửi thư thật qua SMTP")
    ap.add_argument("--list", nargs="?", const="all", default=None, help="liệt kê hồ sơ (moi, trung, …) rồi thoát")
    ap.add_argument("--trang", action="store_true", help="liệt kê các trang đã crawl và phiên bản đã lưu rồi thoát")
    ap.add_argument("--purge", action="store_true", help="xoá hồ sơ quá hạn rồi thoát")
    args = ap.parse_args()

    setup_logging()
    store = CollectorStore(args.inbox)
    if args.list:
        for c in store.all(None if args.list == "all" else args.list):
            print(f"{c.id:5} {STATUS_LABEL[c.trang_thai]:15} {c.nguon:16} {c.ten_tep[:40]:40} {c.vi_tri or '':20} "
                  f"{c.nguoi_gui or c.email or '':28} {c.ly_do}")
        return
    if args.trang:
        print_pages(store)
        return
    smtp = SmtpSettings.from_env()
    collector = CVCollectorAgent(store, build_sources(args), mailer=EmailAgent(smtp=smtp), sent_log=SentLog(),
                                 ack=args.ack or None)
    if args.purge:
        print(f"Đã xoá {collector.purge()} hồ sơ quá hạn.")
        return
    if not collector.sources:
        raise SystemExit("Chưa chọn nguồn nào (--folder, --imap, --web, --url, --search, --api, --dataset, --drive).")

    screen = make_screener(args, collector) if args.jd else None

    def on_run(r: RunReport) -> None:
        print_report(r)
        if screen:
            screen(r)

    try:
        run_periodically(collector, args.interval, max_runs=None if args.watch or args.at else 1, on_run=on_run,
                         at=args.at)
    except KeyboardInterrupt:
        print("Đã dừng.")


if __name__ == "__main__":
    main()
