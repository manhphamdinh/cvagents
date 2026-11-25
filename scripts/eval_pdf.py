"""Thử nghiệm bổ sung – đối sánh khi CV đi vào hệ thống dưới dạng tệp PDF (đầu vào thật của hệ thống).

    python scripts/eval_pdf.py --lang vi --data-dir cvjd_vi --cap kho --out results/pdf_vi_kho --n-cv 200 \
        --ket-qua-chinh results/A_vi_kho/ket_qua.json

Bộ dữ liệu chỉ có CV dạng văn bản, nên lấy n CV đầu của TẬP KIỂM TRA (cùng cách chia với eval_matching.py) và dựng
mỗi CV thành tệp PDF như một CV thật: tiêu đề mục in đậm trên dòng riêng, kỹ năng và kinh nghiệm thành gạch đầu dòng.
Mọi cặp (CV, JD) của n CV này trong tập kiểm tra được chấm bằng KB4 (hệ thống đầy đủ) theo ba dạng đầu vào:
    van_ban   CV văn bản đưa thẳng vào bước 3–8 (giống KB4 trong eval_matching.py)          – mốc so sánh
    pdf       CV PDF có lớp văn bản: CV_Extractor đọc qua Docling (bước 1–2) rồi bước 3–8
    pdf_quet  CV PDF được kết xuất thành ảnh và làm nhiễu mức "vua" (scripts/make_noisy.py), không còn lớp văn bản:
              CV_Extractor phải tự phát hiện và chuyển sang OCR
JD giữ dạng văn bản (trong hệ thống, JD được nhập dưới dạng văn bản). Nhãn của cặp không đổi, nên đo được bước đọc
tệp làm thay đổi kết quả đối sánh bao nhiêu.

Độ đo cho mỗi dạng: Spearman, Accuracy, P, R, F1, MCC, AP, AUC (ngưỡng = ngưỡng KB4 của lần chạy chính nếu có
--ket-qua-chinh, nếu không thì chọn trên chính tập này và ghi rõ); độ lệch điểm trung bình |ΔS| so với văn bản; tỉ lệ
cặp đổi quyết định (đạt ↔ không đạt) so với văn bản; độ trùng kỹ năng trích xuất (Jaccard) so với văn bản; số đơn vị
bằng chứng trung bình; tỉ lệ CV phải dùng OCR; thời gian đọc + trích xuất trung bình mỗi CV.
Kết quả: <out>/ket_qua_pdf.csv, ket_qua_pdf.json, diem_tung_cap.csv; tệp PDF ở <out>/pdf/ và <out>/pdf_quet/.

Cần: pip install docling reportlab pypdfium2 pillow (và các gói của eval_matching.py)
Chạy thử không cần Docling/mô hình: --embedder hashing --reader pdfplumber --n-cv 20
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
import eval_matching as em  # noqa: E402

FORMS = ["van_ban", "pdf", "pdf_quet"]
FONTS = [  # phông Unicode có đủ dấu tiếng Việt: Colab/Linux, Windows, macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf", "/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
]


# ---------------------------------------------------------------- dựng PDF
def _fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for f in FONTS:
        if Path(f).exists():
            bold = next((b for b in (f.replace("Sans.ttf", "Sans-Bold.ttf"), f.replace("Regular", "Bold"),
                                     f.replace("arial.ttf", "arialbd.ttf")) if b != f and Path(b).exists()), f)
            pdfmetrics.registerFont(TTFont("CV", f))
            pdfmetrics.registerFont(TTFont("CV-B", bold))
            return "CV", "CV-B"
    raise SystemExit("Không tìm thấy phông Unicode (DejaVuSans/Liberation/Arial); cài: apt-get install fonts-dejavu")


def make_pdf(lines: list[str], name: str, path: Path, fonts: tuple[str, str]) -> None:
    """Một CV một cột: họ tên, vị trí, rồi các mục (tiêu đề đậm) với gạch đầu dòng – bố cục CV phổ biến."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape
    reg, bold = fonts
    st = {
        "ten": ParagraphStyle("ten", fontName=bold, fontSize=16, leading=20, spaceAfter=2),
        "vt": ParagraphStyle("vt", fontName=reg, fontSize=11, leading=14, spaceAfter=8),
        "muc": ParagraphStyle("muc", fontName=bold, fontSize=12, leading=15, spaceBefore=8, spaceAfter=3),
        "dong": ParagraphStyle("dong", fontName=reg, fontSize=10, leading=13, leftIndent=12),
    }
    heads = {"Mục tiêu", "Kỹ năng", "Học vấn", "Kinh nghiệm", "Chứng chỉ"}
    story = [Paragraph(escape(name), st["ten"])]
    for i, ln in enumerate(lines):
        if ln in heads:
            story.append(Paragraph(escape(ln.upper()), st["muc"]))
        elif ln.startswith("• "):
            story.append(Paragraph("• " + escape(ln[2:]), st["dong"]))
        elif i == 0:
            story.append(Paragraph(escape(ln), st["vt"]))
        else:
            story.append(Paragraph(escape(ln), st["dong"]))
    story.append(Spacer(1, 6))
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                      bottomMargin=1.8 * cm, title=name).build(story)


def make_scan(pdf: Path, out: Path, seed: int) -> None:
    import make_noisy as mn
    rng = np.random.default_rng(seed)
    pages = [mn.degrade(im, mn.LEVELS["vua"], rng).convert("RGB") for im in mn.render(pdf, 200)]
    pages[0].save(out, "PDF", resolution=200, save_all=True, append_images=pages[1:])


# ---------------------------------------------------------------- đọc PDF
def extract(path: Path, reader: str):
    """→ (CVProfile, có dùng OCR, giây)."""
    from cvagents.extractor.agent import CVExtractorAgent
    t0 = time.perf_counter()
    agent = CVExtractorAgent()
    if reader == "docling":
        raw, ocr = agent.read(path)
        prof = agent.process(raw, ocr)
        used_ocr = bool(raw.used_ocr)
    else:  # chỉ để chạy thử khi không có Docling: lấy dòng văn bản bằng pdfplumber, PDF quét → rỗng
        import pdfplumber
        from cvagents.extractor.models import Block, RawDocument
        with pdfplumber.open(path) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        prof = agent.process(RawDocument(blocks=[Block(text=l) for l in text.splitlines() if l.strip()]), None)
        used_ocr = False
    return prof, used_ocr, time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser(description="Thử nghiệm bổ sung: KB4 với CV đầu vào dạng PDF")
    ap.add_argument("--lang", choices=["en", "vi"], required=True)
    ap.add_argument("--data-dir")
    ap.add_argument("--ft-model")
    ap.add_argument("--n-cv", type=int, default=200, help="số CV đầu của tập kiểm tra được dựng thành PDF")
    ap.add_argument("--forms", default=",".join(FORMS))
    ap.add_argument("--ket-qua-chinh", help="ket_qua.json của eval_matching.py để dùng lại ngưỡng KB4")
    ap.add_argument("--max-seq-length", type=int, default=256)
    ap.add_argument("--embedder", choices=["st", "hashing"], default="st")
    ap.add_argument("--reader", choices=["docling", "pdfplumber"], default="docling")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", choices=["goc", "kho"], default="goc", help="cách dựng cặp, như eval_matching.py --cap")
    args = ap.parse_args()
    em.DATA_DIR = Path(args.data_dir) if args.data_dir else None
    em.PAIRING = args.cap
    root = HERE.parent
    args.ft_model = args.ft_model or str(root / "models" / ("vn-embedding-cvjd-vi" if args.lang == "vi" else "vn-embedding-cvjd"))
    out = Path(args.out)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    (out / "pdf_quet").mkdir(parents=True, exist_ok=True)
    forms = [f.strip() for f in args.forms.split(",") if f.strip()]

    train, val, test = em.load_splits()
    test = test.head(em.TEST_PAIRS)
    cvs = test.candidate_id.drop_duplicates().head(args.n_cv).tolist()
    pairs = test[test.candidate_id.isin(cvs)].reset_index(drop=True)
    print(f"{len(cvs)} CV, {len(pairs)} cặp (dương: {(pairs.final_score > 0).sum()})")

    thr, thr_src, w = None, "chọn trên chính tập này (F1 lớn nhất)", None
    if args.ket_qua_chinh and Path(args.ket_qua_chinh).exists():
        kb4 = [r for r in json.loads(Path(args.ket_qua_chinh).read_text(encoding="utf-8"))["ket_qua"]
               if r["cau_hinh"] == "KB4" and r.get("phien_ban") == em.KB4_VERSION]
        if kb4:
            thr, thr_src = float(kb4[0]["nguong"]), f"ngưỡng và trọng số KB4 của lần chạy chính ({args.ket_qua_chinh})"
            w = kb4[0].get("trong_so")
    print("Ngưỡng:", thr_src)

    scorer = em.Scorer(SimpleNamespace(embedder=args.embedder, max_seq_length=args.max_seq_length), train)
    from cvagents.analysis.agent import CVJDAnalysisAgent
    agent = CVJDAnalysisAgent(embedder=scorer.embedder(args.ft_model))
    from cvagents.analysis.matching import evidence_units
    from cvagents.analysis.scoring import total_score
    print("Trọng số:", w or "theo config.toml")

    text_of = dict(zip(pairs.candidate_id, pairs.resume_text))
    fonts = _fonts() if {"pdf", "pdf_quet"} & set(forms) else None
    profiles: dict[str, dict[str, object]] = {f: {} for f in forms}
    info: dict[str, dict[str, list]] = {f: {"ocr": [], "giay": []} for f in forms}
    for k, cid in enumerate(cvs):
        txt = str(text_of[cid])
        lines = em.cv_lines(txt)
        for f in forms:
            if f == "van_ban":
                t0 = time.perf_counter()
                p, ocr = scorer.profile(txt).model_copy(update={"van_ban_goc": txt}), False
                dt = time.perf_counter() - t0
            else:
                pdf = out / "pdf" / f"{cid}.pdf"
                if not pdf.exists():
                    make_pdf(lines, f"Ứng viên {cid}" if args.lang == "vi" else f"Candidate {cid}", pdf, fonts)
                path = pdf
                if f == "pdf_quet":
                    path = out / "pdf_quet" / f"{cid}.pdf"
                    if not path.exists():
                        make_scan(pdf, path, seed=k)
                try:
                    p, ocr, dt = extract(path, args.reader)
                except Exception as e:  # một tệp lỗi không làm dừng cả lượt chạy
                    print(f"  lỗi {f} {cid}: {e}")
                    p, ocr, dt = None, False, 0.0
            profiles[f][cid] = p
            info[f]["ocr"].append(ocr)
            info[f]["giay"].append(dt)
        if (k + 1) % 20 == 0:
            print(f"  đã xử lý {k + 1}/{len(cvs)} CV")

    def skills(p) -> set[str]:
        return {s.lower() for s in (p.ky_nang.chuan_hoa + p.ky_nang.goc)} if p else set()

    per_pair = pairs[["candidate_id", "job_id", "final_score"]].copy()
    for f in forms:
        s = []
        for cid, jd in zip(pairs.candidate_id, pairs.job_text):
            p = profiles[f][cid]
            j = scorer.jd(str(jd)).model_copy(update={"van_ban_goc": str(jd)})
            s.append(total_score(agent.analyze(p, j).diem_thanh_phan, w) if p is not None else 0.0)
        per_pair[f] = s

    y = (pairs.final_score.to_numpy() > 0).astype(int)
    if thr is None:
        thr = em.best_threshold(per_pair[forms[0]].to_numpy(), y)
    rows = []
    for f in forms:
        s = per_pair[f].to_numpy()
        m = em.metrics(s, pairs, thr)
        m.update({"dang_dau_vao": f, "so_cv": len(cvs), "so_cap": len(pairs),
                  "ty_le_ocr": float(np.mean(info[f]["ocr"])), "giay_moi_cv": float(np.mean(info[f]["giay"])),
                  "so_bang_chung_tb": float(np.mean([len(evidence_units(p)) if p else 0 for p in profiles[f].values()])),
                  "ty_le_loi": float(np.mean([p is None for p in profiles[f].values()]))})
        if "van_ban" in forms and f != "van_ban":
            base = per_pair["van_ban"].to_numpy()
            m["lech_diem_tb"] = float(np.mean(np.abs(s - base)))
            m["ty_le_doi_quyet_dinh"] = float(np.mean((s >= thr) != (base >= thr)))
            m["jaccard_ky_nang"] = float(np.mean([
                (len(a & b) / len(a | b)) if (a := skills(profiles["van_ban"][c])) | (b := skills(profiles[f][c])) else 1.0
                for c in cvs]))
        rows.append(m)

    cols = ["dang_dau_vao", "so_cv", "so_cap", "spearman", "accuracy", "precision", "recall", "f1", "mcc", "ap", "roc_auc",
            "nguong", "lech_diem_tb", "ty_le_doi_quyet_dinh", "jaccard_ky_nang", "so_bang_chung_tb", "ty_le_ocr",
            "ty_le_loi", "giay_moi_cv"]
    table = pd.DataFrame(rows).reindex(columns=cols)
    table.to_csv(out / "ket_qua_pdf.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    per_pair.to_csv(out / "diem_tung_cap.csv", index=False)
    (out / "ket_qua_pdf.json").write_text(json.dumps({"lang": args.lang, "ft_model": args.ft_model, "nguong_tu": thr_src,
                                                      "reader": args.reader, "ket_qua": rows}, ensure_ascii=False,
                                                     indent=2), encoding="utf-8")
    print("\n" + table.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(f"\nĐã lưu {out / 'ket_qua_pdf.csv'}")


if __name__ == "__main__":
    main()
