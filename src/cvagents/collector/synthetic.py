"""Sinh tệp CV tổng hợp (PDF, DOCX, ảnh, zip) chỉ bằng thư viện chuẩn và Pillow.

Dùng cho test của CV_Collector và cho scripts/eval_collector.py khi chưa có đủ CV thật (--synthetic). PDF dùng font
Helvetica chuẩn nên văn bản không dấu.
"""
from __future__ import annotations

import io
import random
import zipfile

LOREM = ("python java sql docker kubernetes fastapi django react node golang rust kafka redis postgres mysql "
         "mongodb spark hadoop airflow aws azure gcp linux git ci cd api rest graphql grpc microservice "
         "testing agile scrum team leader mentor product analytics dashboard etl pipeline cloud security").split()


def cv_text(seed: int, n: int = 80, email: str | None = None) -> list[str]:
    rnd = random.Random(seed)
    words = [rnd.choice(LOREM) for _ in range(n)]
    lines = [f"Ung vien so {seed}", f"Email: {email or f'uv{seed}@example.com'}"]
    lines += [" ".join(words[i:i + 10]) for i in range(0, n, 10)]
    return lines


def make_pdf(lines: list[str], pad: int = 0) -> bytes:
    """PDF một trang có lớp văn bản (font Helvetica), đủ để pypdfium2 / pypdf đọc lại chữ."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    stream = "BT /F1 11 Tf 40 800 Td 14 TL " + " ".join(f"({esc(t)}) Tj T*" for t in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n" + b"%" + b"x" * pad + b"\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(lines: list[str], level: int = zipfile.ZIP_DEFLATED, comment: bytes = b"") -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in lines)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", level) as z:
        z.comment = comment
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"/>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.'
                   f'org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
        z.writestr("docProps/padding.txt", "x" * 1200, compress_type=zipfile.ZIP_STORED)  # vượt ngưỡng kích thước tối thiểu 1 KB
    return buf.getvalue()


def make_image(seed: int, size=(600, 800), fmt: str = "PNG", quality: int = 90) -> bytes:
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y = rnd.randrange(size[0] - 100), rnd.randrange(size[1] - 40)
        d.rectangle([x, y, x + rnd.randrange(30, 100), y + rnd.randrange(10, 40)], fill="black")
    buf = io.BytesIO()
    img.save(buf, fmt, **({"quality": quality} if fmt == "JPEG" else {}))
    return buf.getvalue()


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


_HO = ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Dang", "Bui", "Do", "Ngo"]
_TEN = ["Van An", "Thi Binh", "Minh Chau", "Duc Dung", "Thu Ha", "Quang Huy", "Lan Huong", "Gia Khanh", "Hai Long",
        "Thanh Mai", "Bao Ngoc", "Tuan Phong"]
_TRUONG = ["Dai hoc Kinh te Quoc dan", "Dai hoc Bach khoa Ha Noi", "Dai hoc Cong nghe - DHQGHN", "Hoc vien Buu chinh"]
_CTY = ["Cong ty TNHH ABC", "Cong ty CP Cong nghe XYZ", "FPT Software", "Viettel Solutions", "VNG Corporation"]
_VT = ["Backend Developer", "Data Analyst", "Tester", "Frontend Developer"]


def synthetic_cv(seed: int) -> tuple[list[str], dict[str, str]]:
    """(các dòng CV, metadata) của một ứng viên tổng hợp có đủ mục Học vấn / Kinh nghiệm / Kỹ năng."""
    rnd = random.Random(seed)
    name = f"{rnd.choice(_HO)} {rnd.choice(_TEN)}"
    email = f"{name.lower().replace(' ', '.')}.{seed}@example.com"
    phone = f"09{rnd.randrange(10**7, 10**8)}"
    y = rnd.randrange(2016, 2022)
    skills = rnd.sample(LOREM[:24], 6)
    lines = [name, f"Email: {email} | SDT: {phone}", "HOC VAN", rnd.choice(_TRUONG),
             f"Cu nhan Cong nghe thong tin, {y} - {y + 4}, GPA {rnd.uniform(2.5, 3.8):.2f}/4.0",
             "KINH NGHIEM LAM VIEC", f"{rnd.choice(_CTY)} - {rnd.choice(_VT)}, 0{rnd.randrange(1, 9)}/{y + 4} - nay",
             "- " + " ".join(rnd.sample(LOREM, 8)), "- " + " ".join(rnd.sample(LOREM, 8)),
             "KY NANG", ", ".join(skills)]
    return lines, {"ho_ten": name, "email": email, "vi_tri": _VT[seed % len(_VT)]}
