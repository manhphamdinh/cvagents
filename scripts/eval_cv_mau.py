"""Kiểm tra Analysis trên CV thật có nhãn tay: data/cv_mau/<CV>/<CV>.docx × JD trong data/raw, nhãn ở data/cv_mau/nhan.csv.

    python scripts/eval_cv_mau.py              # mô hình nhúng theo config.toml
    python scripts/eval_cv_mau.py --hashing    # thử nhanh không cần mô hình

Nhãn: dat (nên mời), xem_xet (chấp nhận Đạt hoặc Cần xem xét lại), khong (nên loại). Đọc bản .docx để không phụ thuộc OCR.
Báo cáo: trạng thái hệ thống của từng cặp, số cặp đúng, số "đạt nhầm" (nhãn khong mà hệ thống Đạt) và "loại nhầm"
(nhãn dat mà hệ thống Không đạt). Tập này nhỏ, dùng để kiểm chứng trên CV thật chứ không để học ngưỡng.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cvagents.analysis import CVJDAnalysisAgent  # noqa: E402
from cvagents.analysis.embedder import HashingEmbedder, SentenceTransformerEmbedder, model_for_lang  # noqa: E402
from cvagents.analysis.jd_parser import parse_jd  # noqa: E402
from cvagents.common.text import detect_lang  # noqa: E402
from cvagents.extractor import CVExtractorAgent  # noqa: E402
from cvagents.screening import FAIL, PASS, REVIEW, STATUS_LABEL, screening_status  # noqa: E402

OK = {"dat": {PASS}, "xem_xet": {PASS, REVIEW}, "khong": {FAIL, REVIEW}}  # trạng thái chấp nhận được với mỗi nhãn


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cv-dir", default=str(ROOT / "data/cv_mau"))
    ap.add_argument("--jd-dir", default=str(ROOT / "data/raw"))
    ap.add_argument("--hashing", action="store_true")
    args = ap.parse_args()
    cv_dir, jd_dir = Path(args.cv_dir), Path(args.jd_dir)
    labels = list(csv.DictReader((cv_dir / "nhan.csv").open(encoding="utf-8-sig")))

    ex, profiles, agents = CVExtractorAgent(), {}, {}
    rows, right, false_pass, missed = [], 0, 0, 0
    for jd_name in dict.fromkeys(r["jd"] for r in labels):
        jd_text = (jd_dir / jd_name).read_text(encoding="utf-8")
        jd = parse_jd(jd_text)
        model = "hashing" if args.hashing else model_for_lang(detect_lang(jd_text))
        if model not in agents:
            agents[model] = CVJDAnalysisAgent(HashingEmbedder() if args.hashing else
                                              SentenceTransformerEmbedder(model_name=model))
        print(f"\n== {jd_name}: bắt buộc {jd.ky_nang_bat_buoc}, ưu tiên {jd.ky_nang_uu_tien}")
        res = []
        for r in (r for r in labels if r["jd"] == jd_name):
            if r["cv"] not in profiles:
                raw, ocr = ex.read(cv_dir / r["cv"] / f"{r['cv']}.docx")
                profiles[r["cv"]] = ex.process(raw, ocr)
            rep = agents[model].analyze(profiles[r["cv"]], jd)
            st = screening_status(rep)
            must = [m for m in rep.da_dap_ung + rep.chua_dap_ung if m.loai == "bat_buoc"]
            res.append((rep.diem_tong, r, st, sum(m.dat for m in must), len(must)))
        for score, r, st, k, n in sorted(res, key=lambda x: -x[0]):
            good = st in OK[r["nhan"]]
            right += good
            false_pass += r["nhan"] == "khong" and st == PASS
            missed += r["nhan"] == "dat" and st == FAIL
            print(f"  {'✓' if good else '✗'} {score:.3f} {STATUS_LABEL[st]:16} nhãn {r['nhan']:8} bắt buộc {k}/{n}  "
                  f"{r['cv']}")
            rows.append(good)
    print(f"\nĐúng {right}/{len(rows)}   đạt nhầm {false_pass}   loại nhầm {missed}")


if __name__ == "__main__":
    main()
