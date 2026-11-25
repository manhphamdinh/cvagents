"""Ví dụ minh hoạ: chạy toàn bộ hệ thống trên vài CV PDF thật và một JD, in bảng xếp hạng và bằng chứng từng yêu cầu.

    python scripts/demo_pdf.py --jd data/raw/sample_jd.txt --cv cv1.pdf cv2.pdf cv3.pdf --out results/minh_hoa

Kết quả: <out>/minh_hoa.md (đưa vào khoá luận dưới dạng bảng/hình) và minh_hoa.json. Không có nhãn, không có số liệu
đánh giá: mục đích là cho thấy hệ thống đi được từ tệp PDF đến điểm số kèm giải thích.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jd", required=True, help="tệp .txt chứa JD")
    ap.add_argument("--cv", nargs="+", required=True, help="các tệp CV (PDF/DOCX/ảnh)")
    ap.add_argument("--model", help="mô hình nhúng; mặc định theo config.toml")
    ap.add_argument("--trong-so", help="trong_so_kb4.json của eval_matching.py; bỏ trống = trọng số trong config.toml")
    ap.add_argument("--out", default="results/minh_hoa")
    args = ap.parse_args()
    from cvagents.analysis.agent import CVJDAnalysisAgent
    from cvagents.analysis.embedder import SentenceTransformerEmbedder
    from cvagents.analysis.jd_parser import parse_jd
    from cvagents.extractor.agent import CVExtractorAgent

    jd = parse_jd(Path(args.jd).read_text(encoding="utf-8"))
    agent = CVJDAnalysisAgent(SentenceTransformerEmbedder(args.model) if args.model else None)
    ex = CVExtractorAgent()
    from cvagents.analysis.scoring import total_score
    w = json.loads(Path(args.trong_so).read_text(encoding="utf-8"))["trong_so"] if args.trong_so else None
    thr = agent.cfg["pass_threshold"]
    res = []
    for f in args.cv:
        t0 = time.perf_counter()
        raw, ocr = ex.read(f)
        prof = ex.process(raw, ocr)
        rep = agent.analyze(prof, jd)
        if w:
            rep.diem_tong = round(total_score(rep.diem_thanh_phan, w), 4)
            rep.phu_hop = bool(rep.diem_thanh_phan) and rep.diem_tong >= thr
        res.append({"tep": Path(f).name, "ocr": bool(raw.used_ocr), "giay": round(time.perf_counter() - t0, 2),
                    "ho_ten": prof.thong_tin_ca_nhan.ho_ten, "so_ky_nang": len(prof.ky_nang.chuan_hoa),
                    "thang_kinh_nghiem": prof.tong_thang_kinh_nghiem, "bao_cao": rep.model_dump()})
    res.sort(key=lambda r: r["bao_cao"]["diem_tong"], reverse=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = [f"# Minh hoạ: {jd.tieu_de or Path(args.jd).name}", "",
          f"Yêu cầu bắt buộc: {', '.join(jd.ky_nang_bat_buoc) or '–'}; ưu tiên: {', '.join(jd.ky_nang_uu_tien) or '–'}; "
          f"kinh nghiệm tối thiểu: {jd.so_thang_kinh_nghiem_toi_thieu} tháng", "",
          "| Hạng | Tệp CV | Họ tên | OCR | Điểm tổng | Điểm thành phần | Phù hợp | Thời gian (s) |",
          "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(res, 1):
        b = r["bao_cao"]
        parts = "; ".join(f"{k} {v:.2f}" for k, v in b["diem_thanh_phan"].items())
        md.append(f"| {i} | {r['tep']} | {r['ho_ten'] or '–'} | {'có' if r['ocr'] else 'không'} | {b['diem_tong']:.3f} | "
                  f"{parts} | {'Đạt' if b['phu_hop'] else 'Không'} | {r['giay']} |")
    for r in res:
        b = r["bao_cao"]
        md += ["", f"## {r['tep']} – {b['diem_tong']:.3f}", "", "| Yêu cầu | Loại | Cosine | Bằng chứng | Đạt |", "|---|---|---|---|---|"]
        for m in b["da_dap_ung"] + b["chua_dap_ung"]:
            md.append(f"| {m['yeu_cau']} | {m['loai']} | {m['cosine']:.2f} | {m.get('bang_chung') or '–'} | "
                      f"{'✓' if m['dat'] else '✗'} |")
        md += [f"- {g}" for g in b["ghi_chu"]]
    (out / "minh_hoa.md").write_text("\n".join(md), encoding="utf-8")
    (out / "minh_hoa.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(md[:6 + len(res)]))
    print(f"\nĐã lưu {out / 'minh_hoa.md'}")


if __name__ == "__main__":
    main()
