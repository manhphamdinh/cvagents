"""Sinh CV ở nhiều định dạng (cùng nội dung) và dựng thư mục trang tuyển dụng giả lập.

    python make_cvs.py <thu_muc_anh_goc> <thu_muc_ra>

Ra:
  <ra>/cv_mau/<CV>/<CV>.{txt,pdf,docx,png,jpg}, <CV>_scan.pdf   (mọi định dạng của một CV)
  <ra>/trang_gia_lap/<tin>/...                                   (mỗi ứng viên một tệp, định dạng khác nhau)
"""
from __future__ import annotations

import csv
import io
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor
from PIL import Image, ImageFilter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

sys.path.insert(0, str(Path(__file__).parent))
from cv_noi_dung import ANH, CV  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/"
pdfmetrics.registerFont(TTFont("DV", FONT + "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", FONT + "DejaVuSans-Bold.ttf"))
ACCENT = "#1F4E79"


def parse(text: str):
    """→ list (loại, nội dung): name | head | bullet | line."""
    out = []
    for ln in text.strip().splitlines():
        s = ln.rstrip()
        if not s.strip():
            continue
        if s.startswith("! "):
            out.append(("name", s[2:].strip()))
        elif s.startswith("# "):
            out.append(("head", s[2:].strip()))
        elif s.startswith("- "):
            out.append(("bullet", s[2:].strip()))
        else:
            out.append(("line", s.strip()))
    return out


def plain(text: str) -> str:
    lines = []
    for k, v in parse(text):
        lines.append({"bullet": "• " + v, "head": "\n" + v.upper()}.get(k, v))
    return "\n".join(lines).strip() + "\n"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Mẫu trình bày khác nhau cho từng CV (ứng viên thật dùng mẫu khác nhau; nội dung không đổi).
#   (màu nhấn, kiểu: plain | band (dải đầu trang) | side (cột trái) | center | frame)
MAU = [("#1F4E79", "plain"), ("#7A1F3D", "band"), ("#2E6B3A", "side"), ("#444444", "center"), ("#B35C00", "frame")]


def make_pdf(text: str, path: Path, mau: int = 0) -> None:
    from reportlab.lib.enums import TA_CENTER

    accent, kieu = MAU[mau % len(MAU)]
    W, H = A4
    left, top = 18 * mm, 16 * mm
    if kieu == "side":
        left = 62 * mm
    if kieu == "band":
        top = 9 * mm
    fs = 10.5 if kieu in ("center", "frame") else 9.5
    st = {
        "name": ParagraphStyle("n", fontName="DVB", fontSize=20 if kieu != "plain" else 18, leading=25,
                               textColor=HexColor("#FFFFFF" if kieu == "band" else accent), spaceAfter=2,
                               alignment=TA_CENTER if kieu == "center" else 0),
        "head": ParagraphStyle("h", fontName="DVB", fontSize=fs + 2, leading=fs + 6, textColor=HexColor(accent),
                               spaceBefore=9, spaceAfter=1),
        "bullet": ParagraphStyle("b", fontName="DV", fontSize=fs, leading=fs * 1.38, leftIndent=12, bulletIndent=2,
                                 bulletFontName="DV"),
        "line": ParagraphStyle("l", fontName="DV", fontSize=fs, leading=fs * 1.38),
    }

    def deco(c, _doc):
        c.saveState()
        if kieu == "band":
            c.setFillColor(HexColor(accent)); c.rect(0, H - 34 * mm, W, 34 * mm, stroke=0, fill=1)
        elif kieu == "side":
            c.setFillColor(HexColor("#E3EDE5")); c.rect(0, 0, 54 * mm, H, stroke=0, fill=1)
            c.setFillColor(HexColor(accent)); c.rect(0, H - 60 * mm, 54 * mm, 60 * mm, stroke=0, fill=1)
        elif kieu == "center":
            c.setFillColor(HexColor("#DDDDDD")); c.rect(0, 0, W, 60 * mm, stroke=0, fill=1)
            c.setFillColor(HexColor(accent)); c.rect(W / 2 - 3 * mm, 60 * mm, 6 * mm, 30 * mm, stroke=0, fill=1)
        elif kieu == "frame":
            c.setStrokeColor(HexColor(accent)); c.setLineWidth(5)
            c.rect(8 * mm, 8 * mm, W - 16 * mm, H - 16 * mm, stroke=1, fill=0)
            c.setFillColor(HexColor(accent)); c.rect(W - 30 * mm, 8 * mm, 22 * mm, H - 16 * mm, stroke=0, fill=1)
        c.restoreState()

    flow, first = [], True
    for k, v in parse(text):
        if k == "bullet":
            flow.append(Paragraph(esc(v), st[k], bulletText="•"))
        elif k == "name" and kieu == "band":
            flow.append(Spacer(1, 3 * mm)); flow.append(Paragraph(esc(v), st[k])); flow.append(Spacer(1, 14 * mm))
        else:
            flow.append(Paragraph(esc(v), st[k]))
        if k == "head" and kieu != "center":
            flow.append(HRFlowable(width="100%", thickness=0.6, color=HexColor(accent), spaceAfter=3))
    right = 36 * mm if kieu == "frame" else 18 * mm
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=left, rightMargin=right,
                            topMargin=top, bottomMargin=16 * mm, title=path.stem, author="")
    doc.build(flow, onFirstPage=deco, onLaterPages=deco)


def make_docx(text: str, path: Path) -> None:
    d = Document()
    stl = d.styles["Normal"]
    stl.font.name = "Calibri"
    stl.font.size = Pt(10.5)
    for k, v in parse(text):
        if k == "name":
            r = d.add_paragraph().add_run(v)
            r.bold, r.font.size, r.font.color.rgb = True, Pt(20), RGBColor(0x1F, 0x4E, 0x79)
        elif k == "head":
            h = d.add_heading(v, level=2)
            for r in h.runs:
                r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
        elif k == "bullet":
            d.add_paragraph(v, style="List Bullet")
        else:
            d.add_paragraph(v)
    d.core_properties.author = ""
    d.save(str(path))


def pdf_to_images(pdf: Path, dpi: int = 150) -> list[Image.Image]:
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), f"{td}/p"], check=True)
        return [Image.open(p).convert("RGB") for p in sorted(Path(td).glob("p-*.png"))]


def stack(pages: list[Image.Image]) -> Image.Image:
    w = max(p.width for p in pages)
    out = Image.new("RGB", (w, sum(p.height for p in pages)), "white")
    y = 0
    for p in pages:
        out.paste(p, (0, y))
        y += p.height
    return out


def scan_look(img: Image.Image, seed: int) -> Image.Image:
    """Giả ảnh chụp/quét: xoay nhẹ, mờ nhẹ, nén JPEG."""
    rnd = random.Random(seed)
    im = img.rotate(rnd.uniform(-0.8, 0.8), expand=True, fillcolor="white", resample=Image.BICUBIC)
    im = im.filter(ImageFilter.GaussianBlur(0.4))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def split_pages(img: Image.Image) -> list[Image.Image]:
    """Ảnh dài hơn khổ A4 thì cắt thành nhiều trang."""
    a4 = img.width * 297 / 210
    if img.height <= a4 * 1.1:
        return [img]
    n = -(-img.height // int(a4 * 1.1))
    h = -(-img.height // n)
    return [img.crop((0, i * h, img.width, min(img.height, (i + 1) * h))) for i in range(n)]


def save_scan_pdf(pages: list[Image.Image], path: Path) -> None:
    pages[0].save(str(path), "PDF", resolution=150, save_all=True, append_images=pages[1:])


def build_one(key: str, src_dir: Path, out: Path, seed: int) -> Path:
    d = out / "cv_mau" / key
    d.mkdir(parents=True, exist_ok=True)
    text = CV[key]
    (d / f"{key}.txt").write_text(plain(text), encoding="utf-8")
    make_pdf(text, d / f"{key}.pdf", mau=0 if key in ANH else seed - 5)
    make_docx(text, d / f"{key}.docx")
    if key in ANH:  # có ảnh gốc: giữ nguyên ảnh làm bản jpg/png/scan
        img = Image.open(src_dir / ANH[key]).convert("RGB")
    else:  # CV tổng hợp: ảnh = PDF kết xuất + hiệu ứng quét
        img = scan_look(stack(pdf_to_images(d / f"{key}.pdf")), seed)
    img.save(d / f"{key}.jpg", "JPEG", quality=90)
    img.save(d / f"{key}.png", "PNG", optimize=True)
    save_scan_pdf(split_pages(img), d / f"{key}_scan.pdf")
    return d


# Trang giả lập: mỗi ứng viên một tệp, định dạng xoay vòng (cùng nội dung với bản gốc).
TIN = {
    "Backend Developer (.NET)": [
        ("CV06_Nguyen_Hoang_Nam", ".docx", "Nguyễn Hoàng Nam", "nguyenhoangnam.dev@example.com"),
        ("CV02_Kimono_Rental_Dev", "_scan.pdf", "", ""),
        ("CV03_Vo_Van_Sang", ".pdf", "Võ Văn Sang", ""),
        ("CV07_Le_Thi_Thu_Trang", ".png", "Lê Thị Thu Trang", "lethuthutrang@example.com"),
        ("CV09_Do_Quang_Huy", ".jpg", "Đỗ Quang Huy", "doquanghuy.qa@example.com"),
    ],
    "Fullstack Developer": [
        ("CV01_Tran_Xuan_Duc", ".jpg", "Trần Xuân Đức", "tranxuanduc.work@gmail.com"),
        ("CV04_FrontEnd_Developer", ".png", "", ""),
        ("CV05_Fullstack_Laravel", ".docx", "", ""),
        ("CV10_Bui_Ngoc_Anh", "_scan.pdf", "Bùi Ngọc Anh", "buingocanh.mobile@example.com"),
        ("CV08_Pham_Minh_Khoa", ".pdf", "Phạm Minh Khoa", "phamminhkhoa.data@example.com"),
    ],
}


def build_site(out: Path) -> None:
    site = out / "trang_gia_lap"
    if site.exists():
        shutil.rmtree(site)
    for tin, ds in TIN.items():
        t = site / tin
        t.mkdir(parents=True)
        rows = []
        for key, suf, ten, email in ds:
            name = key + (".pdf" if suf == "_scan.pdf" else suf)
            shutil.copy2(out / "cv_mau" / key / (key + suf), t / name)
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", CV[key])
            rows.append({"ten_tep": name, "ho_ten": ten, "email": m.group(0) if m else "", "vi_tri": tin})
        with open(t / "ung_vien.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ten_tep", "ho_ten", "email", "vi_tri"])
            w.writeheader()
            w.writerows(rows)


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    for i, key in enumerate(CV):
        build_one(key, src, out, seed=i)
        print("ok", key)
    build_site(out)


if __name__ == "__main__":
    main()
