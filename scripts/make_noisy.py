"""Tạo bản quét và bản nhiễu có kiểm soát từ các CV PDF có lớp văn bản (tập K2 của nhóm thử nghiệm B).

    python scripts/make_noisy.py --src data/eval/b_cv --out data/eval/b_nhieu

Mỗi PDF trong --src được kết xuất thành ảnh (200 dpi) rồi suy giảm theo bốn mức, lưu thành PDF quét (không còn lớp văn
bản) để CV_Extractor phải tự phát hiện và chuyển sang OCR:
    sach  chỉ kết xuất ảnh (bản quét tốt)
    nhe   nghiêng 1°, mờ 0,5 px, nhiễu hạt σ = 6, nén JPEG chất lượng 70
    vua   nghiêng 2°, mờ 1,0 px, nhiễu hạt σ = 12, JPEG 40, tương phản 0,8
    nang  nghiêng 4°, mờ 1,6 px, nhiễu hạt σ = 20, JPEG 20, tương phản 0,6
CV dạng DOCX cần xuất sang PDF trước (Word: File → Save As → PDF). Tệp manifest.json ghi tệp nhiễu → CV gốc, mức nhiễu,
để scripts/eval_extractor.py lấy nhãn của CV gốc và tách kết quả theo mức. Hạt giống cố định nên chạy lại cho cùng kết quả.

Cần: pip install pypdfium2 pillow numpy  (pypdfium2 đã được cài kèm docling)
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

LEVELS = {
    "sach": dict(goc=0.0, mo=0.0, nhieu=0, jpeg=None, tuong_phan=1.0),
    "nhe": dict(goc=1.0, mo=0.5, nhieu=6, jpeg=70, tuong_phan=1.0),
    "vua": dict(goc=2.0, mo=1.0, nhieu=12, jpeg=40, tuong_phan=0.8),
    "nang": dict(goc=4.0, mo=1.6, nhieu=20, jpeg=20, tuong_phan=0.6),
}


def degrade(img: Image.Image, p: dict, rng: np.random.Generator) -> Image.Image:
    img = img.convert("L")
    if p["goc"]:
        img = img.rotate(p["goc"] * rng.choice([-1, 1]), resample=Image.BICUBIC, expand=True, fillcolor=255)
    if p["mo"]:
        img = img.filter(ImageFilter.GaussianBlur(p["mo"]))
    if p["tuong_phan"] != 1.0:
        img = ImageEnhance.Contrast(img).enhance(p["tuong_phan"])
    if p["nhieu"]:
        a = np.asarray(img, dtype=np.float32) + rng.normal(0, p["nhieu"], (img.height, img.width))
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    if p["jpeg"]:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=p["jpeg"])
        img = Image.open(io.BytesIO(buf.getvalue())).convert("L")
    return img


def render(pdf: Path, dpi: int) -> list[Image.Image]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    return [doc[i].render(scale=dpi / 72).to_pil() for i in range(len(doc))]


def main() -> None:
    ap = argparse.ArgumentParser(description="Tạo CV quét / nhiễu cho thử nghiệm B")
    ap.add_argument("--src", required=True, help="thư mục CV PDF có lớp văn bản")
    ap.add_argument("--out", required=True)
    ap.add_argument("--levels", default=",".join(LEVELS))
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    manifest = {}
    pdfs = sorted(p for p in Path(args.src).iterdir() if p.suffix.lower() == ".pdf")
    for pdf in pdfs:
        pages = render(pdf, args.dpi)
        for lv in args.levels.split(","):
            imgs = [degrade(pg, LEVELS[lv], rng) for pg in pages]
            name = f"{pdf.stem}__{lv}.pdf"
            imgs[0].save(out / name, "PDF", resolution=args.dpi, save_all=True, append_images=imgs[1:])
            manifest[name] = {"nguon": pdf.name, "muc": lv, "can_ocr": True}
        print(f"{pdf.name}: {len(pages)} trang × {len(args.levels.split(','))} mức")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Đã tạo {len(manifest)} tệp trong {out}")


if __name__ == "__main__":
    main()
