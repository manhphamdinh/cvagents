"""Kịch bản K3: đo CV_Collector trên ba nguồn (trang tuyển dụng giả lập, API, bộ dữ liệu) để lập Bảng 3.15 và Bảng 3.16 của khoá luận.

    python scripts/eval_collector.py --cv-dir data/k3_cv --extract
    python scripts/eval_collector.py --synthetic 90                  # chưa có CV thật: dùng CV tổng hợp
    python scripts/eval_collector.py --cv-dir data/k3_cv --no-near-dup --out data/processed/k3_C0   # cấu hình C0

Thử nghiệm C (baseline → đầy đủ): C0 chỉ loại trùng byte (SHA-1), tắt loại gần trùng bằng --no-near-dup;
C1 thêm loại gần trùng SimHash / dHash (mặc định). Hai lần chạy cùng --seed nên dùng cùng tập tệp có nhãn.

CV gốc (thư mục --cv-dir và/hoặc --synthetic CV tổng hợp) được chia đều cho ba nguồn. Mỗi nguồn được cài thêm, theo
tỷ lệ cố định và có nhãn: bản sao y hệt (trùng byte, cả trong nguồn và sang nguồn khác), bản lưu lại (gần trùng),
tệp hỏng / sai định dạng, và liên kết lỗi 404 (nguồn web, API). Collector chạy trên kho mới; kết quả được so với nhãn.

Đầu ra: bảng theo nguồn (số hồ sơ thu, tỷ lệ tải thành công, tỷ lệ trùng bị loại, tỷ lệ hợp lệ, tỷ lệ CV_Extractor
xử lý được nếu có --extract, thời gian trung bình mỗi hồ sơ), độ chính xác loại trùng / kiểm tra định dạng so với nhãn,
lưu vào <out>/k3_collector.csv và <out>/k3_collector.json.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import shutil
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cvagents.collector import (  # noqa: E402
    DUP, FETCH_ERR, INVALID, NEAR_DUP, NEW, CollectorStore, CVCollectorAgent, setup_logging,
)
from cvagents.collector.mocksite import MockJobSite  # noqa: E402
from cvagents.collector.models import API, DATASET, SOURCE_LABEL, WEB  # noqa: E402
from cvagents.collector.sources import ApiSource, DatasetSource, JobBoardSource  # noqa: E402
from cvagents.collector.sources.net import HttpClient  # noqa: E402
from cvagents.collector.synthetic import make_docx, make_pdf, make_zip, synthetic_cv  # noqa: E402
from cvagents.collector.validate import sniff  # noqa: E402

CV_EXT = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}
GOC, TRUNG, GAN_TRUNG, HONG, LOI_TAI = "goc", "trung", "gan_trung", "khong_hop_le", "loi_tai"


def resave(data: bytes) -> bytes:
    """Bản "lưu lại" cùng nội dung nhưng khác byte: DOCX nén lại, PDF thêm cập nhật tăng dần, ảnh nén JPEG lại."""
    kind = sniff(data)
    if kind == "docx":
        src, buf = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for i in src.infolist():
                z.writestr(i.filename, src.read(i))
        return buf.getvalue()
    if kind == "pdf":
        return data + b"\n% luu lai\n%%EOF\n"
    from PIL import Image

    out = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(out, "JPEG", quality=80)
    return out.getvalue()


def corrupt(data: bytes, rnd: random.Random) -> tuple[str, bytes]:
    """Một tệp không hợp lệ: PDF tải dở, tệp .doc cũ, hoặc tệp văn bản đặt đuôi .pdf."""
    k = rnd.randrange(3)
    if k == 0 and sniff(data) == "pdf":
        return ".pdf", data[: len(data) // 2]
    if k == 1:
        return ".doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(rnd.randrange(256) for _ in range(4000))
    return ".pdf", ("Thu ung tuyen, CV gui sau.\n" * 200).encode()


def base_cvs(args) -> list[tuple[str, bytes, dict]]:
    out = []
    if args.cv_dir:
        for p in sorted(Path(args.cv_dir).rglob("*")):
            if p.is_file() and p.suffix.lower() in CV_EXT:
                out.append((p.stem + "_" + p.suffix[1:].lower(), p.read_bytes(), {"vi_tri": p.parent.name if p.parent != Path(args.cv_dir) else ""}))
    for i in range(args.synthetic):
        lines, meta = synthetic_cv(i)
        out.append((f"tonghop_{i:03d}", make_pdf(lines) if i % 2 == 0 else make_docx(lines), meta))
    return out


def build(args, root: Path) -> tuple[dict[str, Path], dict[str, tuple[str, str, str]], dict[str, list[str]]]:
    """Dựng ba thư mục nguồn có nhãn.

    Trả về (thư mục nguồn, tên tệp → (nguồn, nhãn, CV gốc), tệp lỗi 404 theo nguồn). Các tệp cùng "CV gốc" là một
    nhóm trùng: Collector đúng khi nhận đúng một tệp của nhóm (tệp nào đến trước) và loại các tệp còn lại.
    """
    rnd = random.Random(args.seed)
    cvs = base_cvs(args)
    if len(cvs) < 3:
        raise SystemExit("Cần ít nhất 3 CV gốc (--cv-dir hoặc --synthetic).")
    rnd.shuffle(cvs)
    groups = {WEB: cvs[0::3], API: cvs[1::3], DATASET: cvs[2::3]}
    dirs, labels, broken = {}, {}, {}
    counter = iter(range(10**6))

    def put(src: str, stem: str, data: bytes, meta: dict, label: str, base: str | None = None) -> str:
        ext = {"pdf": ".pdf", "docx": ".docx", "png": ".png", "jpg": ".jpg"}.get(sniff(data) or "", ".pdf")
        name = f"{src[:3]}_{next(counter):04d}_{stem}{ext}"[:80]
        sub = dirs[src] / (meta.get("vi_tri") or "Ung tuyen chung")
        sub.mkdir(parents=True, exist_ok=True)
        (sub / name).write_bytes(data)
        labels[name] = (src, label, base or stem)
        return name

    for src, items in groups.items():
        dirs[src] = root / src
        broken[src] = []
        for stem, data, meta in items:
            put(src, stem, data, meta, GOC)
        n = len(items)
        for stem, data, meta in rnd.sample(items, max(1, round(n * args.dup_rate))):
            put(src, stem + "_ban_sao", data, meta, TRUNG, stem)
        for stem, data, meta in rnd.sample(items, max(1, round(n * args.near_rate))):
            put(src, stem + "_luu_lai", resave(data), meta, GAN_TRUNG, stem)
        for stem, data, meta in rnd.sample(items, max(1, round(n * args.bad_rate))):
            ext, bad = corrupt(data, rnd)
            name = put(src, stem + "_hong", bad, meta, HONG, stem + "_hong")
            if not name.endswith(ext):
                p = next(dirs[src].rglob(name))
                p.rename(p.with_suffix(ext))
                labels[p.with_suffix(ext).name] = labels.pop(name)
        if src != DATASET:
            for stem, data, meta in rnd.sample(items, max(1, round(n * args.broken_rate))):
                name = put(src, stem + "_404", data + b" ", meta, LOI_TAI, stem)
                broken[src].append(next(dirs[src].rglob(name)).relative_to(dirs[src]).as_posix())
    # trùng sang nguồn khác: ứng viên nộp cùng CV ở hai kênh
    for a, b in ((WEB, API), (API, DATASET), (DATASET, WEB)):
        for stem, data, meta in rnd.sample(groups[a], max(1, round(len(groups[a]) * args.cross_rate))):
            put(b, stem + "_kenh_khac", data, meta, TRUNG, stem)
    return dirs, labels, broken


def extract_rate(cvs, timeout_note: list) -> dict[str, tuple[int, int]]:
    """Số hồ sơ CV_Extractor xử lý được (không lỗi và đọc được họ tên hoặc email) theo nguồn."""
    from cvagents.extractor import CVExtractorAgent

    ex, out = CVExtractorAgent(), {}
    for c in cvs:
        ok = False
        try:
            p = ex.extract(c.duong_dan)
            ok = bool(p.thong_tin_ca_nhan.ho_ten or p.thong_tin_ca_nhan.email)
        except Exception as e:  # một CV lỗi không làm dừng cả lô
            timeout_note.append(f"{c.ten_tep}: {type(e).__name__}: {e}")
        n, k = out.get(c.nguon, (0, 0))
        out[c.nguon] = (n + 1, k + ok)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Kịch bản K3 – đo CV_Collector")
    ap.add_argument("--cv-dir", default=None, help="thư mục CV gốc (thư mục con = vị trí ứng tuyển)")
    ap.add_argument("--synthetic", type=int, default=0, help="thêm N CV tổng hợp")
    ap.add_argument("--extract", action="store_true", help="chạy CV_Extractor trên hồ sơ hợp lệ (cần docling)")
    ap.add_argument("--out", default="data/processed/k3")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--dup-rate", type=float, default=0.10)
    ap.add_argument("--near-rate", type=float, default=0.05)
    ap.add_argument("--bad-rate", type=float, default=0.05)
    ap.add_argument("--broken-rate", type=float, default=0.03)
    ap.add_argument("--cross-rate", type=float, default=0.05)
    ap.add_argument("--delay", type=float, default=0.0, help="giãn cách giữa hai yêu cầu HTTP (giây)")
    ap.add_argument("--no-near-dup", action="store_true", help="cấu hình C0: tắt loại gần trùng (chỉ SHA-1)")
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "nguon").mkdir(parents=True)
    setup_logging(out / "collector.log")
    dirs, labels, broken = build(args, out / "nguon")
    with zipfile.ZipFile(out / "bo_du_lieu.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dirs[DATASET].rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(dirs[DATASET]).as_posix())

    web_srv, web_url = MockJobSite(dirs[WEB], broken=broken[WEB]).serve()
    api_srv, api_url = MockJobSite(dirs[API], broken=broken[API]).serve()
    try:
        agent = CVCollectorAgent(CollectorStore(out / "inbox"), ack=False,
                                 cfg={"simhash_max_bits": -1, "dhash_max_bits": -1} if args.no_near_dup else None)
        t0 = time.perf_counter()
        report = agent.collect([JobBoardSource(web_url, ten=SOURCE_LABEL[WEB], client=HttpClient(delay=args.delay)),
                                ApiSource(api_url + "api/ung-tuyen", ten=SOURCE_LABEL[API],
                                          client=HttpClient(delay=args.delay, respect_robots=False)),
                                DatasetSource(out / "bo_du_lieu.zip", ten=SOURCE_LABEL[DATASET])])
        total_s = time.perf_counter() - t0
    finally:
        web_srv.shutdown()
        api_srv.shutdown()

    rows = agent.store.all()
    notes: list[str] = []
    ext = extract_rate([c for c in rows if c.trang_thai == NEW], notes) if args.extract else {}

    # so với nhãn
    pred = {NEW: GOC, DUP: TRUNG, NEAR_DUP: GAN_TRUNG, INVALID: HONG, FETCH_ERR: LOI_TAI}
    confusion: dict[tuple[str, str], int] = {}
    for c in rows:
        truth = labels.get(c.ten_tep, (None, "?", ""))[1]
        confusion[(truth, pred[c.trang_thai])] = confusion.get((truth, pred[c.trang_thai]), 0) + 1
    base = lambda c: labels.get(c.ten_tep, ("", "", c.ten_tep))[2]  # noqa: E731
    base_of_key = {c.key: base(c) for c in rows if c.trang_thai == NEW}
    tp = sum(1 for c in rows if c.trang_thai in (DUP, NEAR_DUP) and base_of_key.get(c.trung_voi) == base(c))
    fp_ = sum(1 for c in rows if c.trang_thai in (DUP, NEAR_DUP)) - tp
    accepted: dict[str, int] = {}
    for c in rows:
        if c.trang_thai == NEW:
            accepted[base(c)] = accepted.get(base(c), 0) + 1
    fn = sum(n - 1 for n in accepted.values() if n > 1)

    def prf(tp: int, fp_: int, fn: int) -> dict[str, float]:
        pr = tp / (tp + fp_) if tp + fp_ else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        return {"tp": tp, "fp": fp_, "fn": fn, "precision": round(pr, 4), "recall": round(rc, 4),
                "f1": round(2 * pr * rc / (pr + rc), 4) if pr + rc else 0.0}

    bad_tp = confusion.get((HONG, HONG), 0)
    bad = prf(bad_tp, sum(v for (t, p), v in confusion.items() if p == HONG) - bad_tp,
              sum(v for (t, p), v in confusion.items() if t == HONG) - bad_tp)

    table = []
    for s in [*report.nguon, report.total()]:
        n_ext = ext.get(next((k for k, v in SOURCE_LABEL.items() if v == s.ten), ""), None)
        if s.ten == "Tổng" and ext:
            n_ext = (sum(a for a, _ in ext.values()), sum(b for _, b in ext.values()))
        table.append({"Nguồn": s.ten, "Số hồ sơ thu": s.thu, "Thành công (%)": round(100 * s.ty_le_thanh_cong, 1),
                      "Trùng bị loại (%)": round(100 * s.ty_le_trung, 1),
                      "Hợp lệ (%)": round(100 * s.ty_le_hop_le, 1),
                      "Xử lý được (%)": round(100 * n_ext[1] / n_ext[0], 1) if n_ext and n_ext[0] else None,
                      "Thời gian / hồ sơ (s)": round(s.giay_moi_ho_so, 3)})
    result = {"bang_3_8": table, "loai_trung": prf(tp, fp_, fn), "kiem_tra_dinh_dang": bad,
              "ma_tran_nham_lan": [{"nhan": t, "du_doan": p, "so_tep": v} for (t, p), v in sorted(confusion.items())],
              "tong_thoi_gian_s": round(total_s, 2), "tham_so": vars(args), "loi_extractor": notes}
    (out / "k3_collector.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "k3_collector.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)

    print(f"\nKết quả thu thập hồ sơ theo nguồn ({len(labels)} tệp có nhãn)")
    print(" | ".join(table[0]))
    for r in table:
        print(" | ".join("—" if v is None else str(v) for v in r.values()))
    print(f"\nLoại trùng so với nhãn: {result['loai_trung']}")
    print(f"Kiểm tra định dạng so với nhãn: {result['kiem_tra_dinh_dang']}")
    print("Ma trận nhầm lẫn (nhãn → dự đoán; bản sao đến trước bản gốc thì hai nhãn đổi chỗ, vẫn tính là đúng):")
    for (t, p), v in sorted(confusion.items()):
        print(f"  {t:13} → {p:13} {v}")
    print(f"\nĐã lưu {out / 'k3_collector.csv'} và {out / 'k3_collector.json'}")


if __name__ == "__main__":
    main()
