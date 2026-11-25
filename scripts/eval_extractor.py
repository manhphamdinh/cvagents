"""Nhóm thử nghiệm B – CV_Extractor: từ baseline thêm dần từng thành phần, trên cùng một tập CV có nhãn.

    python scripts/eval_extractor.py --cv-dir data/eval/b_cv --gt-dir data/eval/b_nhan --out results/B_sach
    python scripts/eval_extractor.py --cv-dir data/eval/b_nhieu --gt-dir data/eval/b_nhan --out results/B_nhieu
    python scripts/eval_extractor.py --cv-dir data/eval/b_cv --gt-dir data/eval/b_nhan --make-template   # tạo nhãn nháp

Cấu hình (mỗi cấu hình bật thêm đúng một thành phần; tắt bằng cách ghi đè tham số khi chạy, không sửa mã nguồn):
    B0  Docling đọc lớp văn bản; không kiểm tra chất lượng / chuyển OCR, không sắp thứ tự hai cột,
        tiêu đề mục và kỹ năng chỉ khớp chính xác                                  (baseline)
    B1  + kiểm tra chất lượng lớp văn bản và chuyển OCR có điều kiện (bước 1–2, công thức 2.2–2.6)
    B2  + sắp thứ tự đọc hai cột (công thức 2.7)
    B3  + so khớp mờ rapidfuzz cho tiêu đề mục và kỹ năng (công thức 2.8) = CV_Extractor đầy đủ
    Ảnh PNG/JPG luôn đi qua OCR ở mọi cấu hình vì không có lớp văn bản.

Nhãn: mỗi CV một tệp <tên CV>.json trong --gt-dir (xem --make-template và hướng dẫn thử nghiệm):
    {"ho_ten": "...", "email": "...", "so_dien_thoai": "...",
     "hoc_van": [{"truong": "...", "bac_hoc": 3}], "kinh_nghiem": [{"cong_ty": "...", "chuc_danh": "..."}],
     "ky_nang": ["Python", "SQL"], "tong_thang_kinh_nghiem": 30, "can_ocr": false}
Thư mục CV nhiễu do scripts/make_noisy.py tạo có manifest.json (tệp nhiễu → CV gốc, mức nhiễu): nhãn lấy theo CV gốc và
kết quả được tách theo mức nhiễu; có --ref-dir (thư mục CV gốc) thì tính thêm CER của văn bản OCR so với lớp văn bản gốc.

Độ đo: Precision / Recall / F1 (vi mô) theo bốn nhóm trường (liên hệ, học vấn, kinh nghiệm, kỹ năng) và trung bình macro;
sai số tuyệt đối trung bình của tổng số tháng kinh nghiệm; tỷ lệ chọn đúng nhánh đọc; CER; tương quan giữa κ và F1.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfuzz import fuzz  # noqa: E402
from rapidfuzz.distance import Levenshtein  # noqa: E402

from cvagents.common.config import load_config  # noqa: E402
from cvagents.common.text import norm_key  # noqa: E402

CV_EXT = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}
GROUPS = {"lien_he": "Thông tin liên hệ", "hoc_van": "Học vấn", "kinh_nghiem": "Kinh nghiệm làm việc", "ky_nang": "Kỹ năng"}
CONFIGS = ["B0", "B1", "B2", "B3"]
NAMES = {"B0": "Docling, khớp chính xác (baseline)", "B1": "+ kiểm tra chất lượng, chuyển OCR",
         "B2": "+ thứ tự đọc hai cột", "B3": "+ so khớp mờ rapidfuzz (đầy đủ)"}


# ---------------------------------------------------------------- bật / tắt thành phần
_ORIG: dict[str, object] = {}


def apply_config(cfg: str) -> None:
    from cvagents.extractor import sections
    ex = load_config()["extractor"]  # dict được lru_cache giữ chung cho toàn tiến trình → ghi đè có hiệu lực ngay
    if not _ORIG:
        _ORIG.update({k: ex[k] for k in ("tau_rho", "tau_la", "tau_td", "two_col_gap", "min_alias_fuzzy")})
        _ORIG["match_heading"] = sections.match_heading
    ex.update({k: _ORIG[k] for k in ("tau_rho", "tau_la", "tau_td", "two_col_gap", "min_alias_fuzzy")})
    sections.match_heading = _ORIG["match_heading"]
    level = CONFIGS.index(cfg)
    if level < 1:  # không bao giờ chuyển OCR với PDF/DOCX
        ex.update(tau_rho=0, tau_la=1.0, tau_td=0.0)
    if level < 2:  # không bao giờ coi là hai cột
        ex["two_col_gap"] = 1e9
    if level < 3:  # chỉ khớp chính xác
        ex["min_alias_fuzzy"] = 10 ** 9
        orig = _ORIG["match_heading"]
        sections.match_heading = lambda text, fuzzy=False: orig(text, fuzzy=False)


def install_read_cache() -> None:
    """Docling chậm: mỗi (tệp, có ép OCR) chỉ đọc một lần cho mọi cấu hình."""
    from cvagents.extractor import agent

    orig = agent.read_document

    @lru_cache(maxsize=None)
    def cached(path: str, force_ocr: bool = False):
        return orig(Path(path), force_ocr=force_ocr)

    agent.read_document = lambda path, force_ocr=False: cached(str(path), force_ocr)


# ---------------------------------------------------------------- so khớp với nhãn
def _digits(s: str | None) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-9:]  # bỏ mã quốc gia / số 0 đầu


def _same(a: str | None, b: str | None, thr: int = 85) -> bool:
    return bool(a and b) and fuzz.token_set_ratio(norm_key(a), norm_key(b)) >= thr


def _match_lists(pred: list[str], gold: list[str], thr: int = 85) -> tuple[int, int, int]:
    """Ghép một – một tham lam theo độ giống; trả (tp, fp, fn)."""
    used, tp = set(), 0
    for p in pred:
        best = max(((fuzz.token_set_ratio(norm_key(p), norm_key(g)), k) for k, g in enumerate(gold) if k not in used),
                   default=(0, -1))
        if best[0] >= thr:
            used.add(best[1])
            tp += 1
    return tp, len(pred) - tp, len(gold) - tp


def compare(profile, gt: dict) -> dict[str, tuple[int, int, int]]:
    c = profile.thong_tin_ca_nhan
    lh = [0, 0, 0]
    for pv, gv, eq in ((c.ho_ten, gt.get("ho_ten"), lambda a, b: _same(a, b, 90)),
                       (c.email, gt.get("email"), lambda a, b: a.strip().lower() == b.strip().lower()),
                       (c.so_dien_thoai, gt.get("so_dien_thoai"), lambda a, b: _digits(a) == _digits(b))):
        if pv and gv and eq(pv, gv):
            lh[0] += 1
        else:
            lh[1] += bool(pv)
            lh[2] += bool(gv)
    hv = _match_lists([e.truong for e in profile.hoc_van if e.truong], [e["truong"] for e in gt.get("hoc_van", []) if e.get("truong")])
    kn = _match_lists([e.cong_ty or e.chuc_danh for e in profile.kinh_nghiem if e.cong_ty or e.chuc_danh],
                      [e.get("cong_ty") or e.get("chuc_danh") for e in gt.get("kinh_nghiem", []) if e.get("cong_ty") or e.get("chuc_danh")])
    pk, gk = {s.lower() for s in profile.ky_nang.chuan_hoa}, {s.lower() for s in gt.get("ky_nang", [])}
    return {"lien_he": tuple(lh), "hoc_van": hv, "kinh_nghiem": kn,
            "ky_nang": (len(pk & gk), len(pk - gk), len(gk - pk))}


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def summarize(items: list[dict]) -> dict:
    out = {}
    for g in GROUPS:
        tp, fp, fn = (sum(it["dem"][g][k] for it in items) for k in range(3))
        out[g] = dict(zip(("precision", "recall", "f1"), prf(tp, fp, fn)))
    for k in ("precision", "recall", "f1"):
        out.setdefault("macro", {})[k] = sum(out[g][k] for g in GROUPS) / len(GROUPS)
    errs = [it["sai_so_thang"] for it in items if it["sai_so_thang"] is not None]
    out["mae_thang_kinh_nghiem"] = sum(errs) / len(errs) if errs else None
    out["chon_dung_nhanh"] = sum(it["dung_nhanh"] for it in items) / len(items) if items else None
    cers = [it["cer"] for it in items if it["cer"] is not None]
    out["cer"] = sum(cers) / len(cers) if cers else None
    out["so_cv"] = len(items)
    ks = [(it["kappa"], it["f1"]) for it in items]
    if len(ks) > 2:
        import statistics
        try:
            out["tuong_quan_kappa_f1"] = statistics.correlation([a for a, _ in ks], [b for _, b in ks])
        except statistics.StatisticsError:  # κ không đổi (ví dụ mọi CV đều đi nhánh văn bản)
            out["tuong_quan_kappa_f1"] = None
    return out


def text_of(raw) -> str:
    return re.sub(r"\s+", " ", " ".join(b.text if b.table is None else " ".join(c for r in b.table for c in r)
                                       for b in raw.blocks)).strip()


# ---------------------------------------------------------------- chạy
def main() -> None:
    ap = argparse.ArgumentParser(description="Nhóm thử nghiệm B – CV_Extractor (B0–B3)")
    ap.add_argument("--cv-dir", required=True)
    ap.add_argument("--gt-dir", required=True)
    ap.add_argument("--ref-dir", help="thư mục CV gốc (lớp văn bản) để tính CER cho CV nhiễu")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--make-template", action="store_true", help="ghi kết quả B3 thành nhãn nháp <tên>.json để sửa tay")
    ap.add_argument("--timing", action="store_true", help="đo thêm thời gian B3 không dùng bộ đệm")
    ap.add_argument("--out", default="results/B")
    args = ap.parse_args()

    from cvagents.extractor.agent import CVExtractorAgent
    cv_dir, gt_dir = Path(args.cv_dir), Path(args.gt_dir)
    files = sorted(p for p in cv_dir.iterdir() if p.suffix.lower() in CV_EXT)
    manifest = json.loads((cv_dir / "manifest.json").read_text(encoding="utf-8")) if (cv_dir / "manifest.json").exists() else {}
    agent = CVExtractorAgent()

    if args.make_template:
        apply_config("B3")
        gt_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            dst = gt_dir / f"{f.stem}.json"
            if dst.exists():
                continue
            p = agent.extract(f)
            c = p.thong_tin_ca_nhan
            dst.write_text(json.dumps({
                "ho_ten": c.ho_ten, "email": c.email, "so_dien_thoai": c.so_dien_thoai,
                "hoc_van": [{"truong": e.truong, "bac_hoc": e.bac_hoc} for e in p.hoc_van],
                "kinh_nghiem": [{"cong_ty": e.cong_ty, "chuc_danh": e.chuc_danh} for e in p.kinh_nghiem],
                "ky_nang": p.ky_nang.chuan_hoa, "tong_thang_kinh_nghiem": p.tong_thang_kinh_nghiem,
                "can_ocr": f.suffix.lower() in {".png", ".jpg", ".jpeg"}}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"nhãn nháp: {dst}")
        print("Mở từng tệp .json, sửa cho đúng với CV gốc rồi mới chạy đánh giá.")
        return

    install_read_cache()
    ref_text: dict[str, str] = {}
    if args.ref_dir:
        apply_config("B0")
        for f in sorted(Path(args.ref_dir).iterdir()):
            if f.suffix.lower() in {".pdf", ".docx"}:
                ref_text[f.name] = text_of(agent.read(f)[0])

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results, rows = {}, []
    for cfg in [c.strip() for c in args.configs.split(",") if c.strip()]:
        apply_config(cfg)
        items = []
        for f in files:
            meta = manifest.get(f.name, {})
            gt_path = gt_dir / f"{Path(meta.get('nguon', f.name)).stem}.json"
            if not gt_path.exists():
                print(f"  bỏ qua {f.name}: thiếu nhãn {gt_path.name}")
                continue
            gt = json.loads(gt_path.read_text(encoding="utf-8"))
            try:
                raw, ocr = agent.read(f)
                p = agent.process(raw, ocr)
            except Exception as e:  # noqa: BLE001 – một CV lỗi tính là không trích xuất được gì
                print(f"  {cfg} lỗi {f.name}: {e}")
                from cvagents.common.schemas import CVProfile
                raw, p = None, CVProfile()
            dem = compare(p, gt)
            f1s = [prf(*dem[g])[2] for g in GROUPS]
            need_ocr = meta.get("can_ocr", gt.get("can_ocr", f.suffix.lower() in {".png", ".jpg", ".jpeg"} or bool(meta)))
            ref = ref_text.get(meta.get("nguon", ""))
            hyp = text_of(raw) if raw else ""
            items.append({
                "tep": f.name, "muc": meta.get("muc", "goc"), "dem": dem, "f1": sum(f1s) / len(f1s),
                "kappa": p.do_tin_cay, "dung_nhanh": bool(raw) and raw.used_ocr == need_ocr,
                "sai_so_thang": (abs(p.tong_thang_kinh_nghiem - gt["tong_thang_kinh_nghiem"])
                                 if gt.get("tong_thang_kinh_nghiem") is not None else None),
                "cer": Levenshtein.distance(ref, hyp) / max(len(ref), 1) if ref else None,
            })
        levels = sorted({it["muc"] for it in items})
        results[cfg] = {"tat_ca": summarize(items), **({m: summarize([it for it in items if it["muc"] == m]) for m in levels}
                                                        if len(levels) > 1 else {})}
        for m, s in results[cfg].items():
            rows.append({"cau_hinh": cfg, "mo_ta": NAMES[cfg], "muc_nhieu": m, "so_cv": s["so_cv"],
                         **{f"f1_{g}": s[g]["f1"] for g in GROUPS},
                         "p_macro": s["macro"]["precision"], "r_macro": s["macro"]["recall"], "f1_macro": s["macro"]["f1"],
                         "mae_thang": s["mae_thang_kinh_nghiem"], "chon_dung_nhanh": s["chon_dung_nhanh"], "cer": s["cer"],
                         "tuong_quan_kappa_f1": s.get("tuong_quan_kappa_f1")})
        s = results[cfg]["tat_ca"]
        print(f"{cfg} {NAMES[cfg]:42} F1 macro {s['macro']['f1']:.4f}  " +
              "  ".join(f"{GROUPS[g]} {s[g]['f1']:.3f}" for g in GROUPS))
        (out / f"chi_tiet_{cfg}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.timing:
        from cvagents.extractor import agent as ag
        import importlib
        importlib.reload(ag)
        apply_config("B3")
        a = ag.CVExtractorAgent()
        t0 = time.perf_counter()
        for f in files:
            a.extract(f)
        results["giay_moi_cv_B3"] = (time.perf_counter() - t0) / max(len(files), 1)
        print(f"Thời gian B3: {results['giay_moi_cv_B3']:.2f} giây / CV")

    import csv
    with (out / "ket_qua.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows([{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in rows])
    (out / "ket_qua.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã lưu {out / 'ket_qua.csv'}")


if __name__ == "__main__":
    main()
