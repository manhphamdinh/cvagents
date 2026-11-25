"""Sinh 6 CV demo mới (CV11–CV16) ở mọi định dạng + thư mục trang tuyển dụng giả lập + thư mục tải lên + 2 JD.

    python build_demo.py <thu_muc_ra> <jd_dotnet.txt> <jd_python.txt>
"""
import csv, re, shutil, sys
from pathlib import Path
import make_cvs as m
from cv_demo_noi_dung import CV

MAU = {"CV11_Le_Hoang_Phuc": 0, "CV12_Nguyen_Thu_Ha": 2, "CV13_Hoang_Gia_Bao": 1,
       "CV14_Trinh_Quoc_Viet": 4, "CV15_Phan_Ngoc_Mai": 1, "CV16_Dinh_Thao_Vy": 2}
NET, PY = "Backend Developer (.NET)", "Backend Developer (Python - FastAPI)"
# (CV, định dạng gửi lên trang, tin tuyển dụng, họ tên trên form)
DOT1 = [("CV11_Le_Hoang_Phuc", ".pdf", NET, "Lê Hoàng Phúc"),
        ("CV13_Hoang_Gia_Bao", ".jpg", NET, "Hoàng Gia Bảo"),
        ("CV12_Nguyen_Thu_Ha", ".docx", PY, "Nguyễn Thu Hà")]
DOT2 = [("CV15_Phan_Ngoc_Mai", ".pdf", NET, "Phan Ngọc Mai"),
        ("CV14_Trinh_Quoc_Viet", ".png", PY, "Trịnh Quốc Việt"),
        ("CV16_Dinh_Thao_Vy", "_scan.pdf", PY, "Đinh Thảo Vy")]

def build(key, out, seed):
    d = out / "tat_ca_dinh_dang" / key
    d.mkdir(parents=True, exist_ok=True)
    t = CV[key]
    (d / f"{key}.txt").write_text(m.plain(t), encoding="utf-8")
    m.make_pdf(t, d / f"{key}.pdf", mau=MAU[key])
    m.make_docx(t, d / f"{key}.docx")
    img = m.scan_look(m.stack(m.pdf_to_images(d / f"{key}.pdf")), seed)
    img.save(d / f"{key}.jpg", "JPEG", quality=90)
    img.save(d / f"{key}.png", "PNG", optimize=True)
    m.save_scan_pdf(m.split_pages(img), d / f"{key}_scan.pdf")

def email(key):
    return re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", CV[key]).group(0)

def main():
    out, jd_net, jd_py = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    if out.exists(): shutil.rmtree(out)
    for i, k in enumerate(CV): build(k, out, seed=100 + i)
    site = out / "trang_gia_lap"
    for tin in (NET, PY): (site / tin).mkdir(parents=True)  # cả hai tin đều có thư mục (tin Python có sẵn 1 CV)
    rows = {}
    for key, suf, tin, ten in DOT1:
        name = key + (".pdf" if suf == "_scan.pdf" else suf)
        shutil.copy2(out / "tat_ca_dinh_dang" / key / (key + suf), site / tin / name)
        rows.setdefault(tin, []).append({"ten_tep": name, "ho_ten": ten, "email": email(key), "vi_tri": tin})
    for tin, rs in rows.items():
        with open(site / tin / "ung_vien.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ten_tep", "ho_ten", "email", "vi_tri"]); w.writeheader(); w.writerows(rs)
    up = out / "tai_len_dot_2"; up.mkdir()
    form = []
    for key, suf, tin, ten in DOT2:
        name = key + (".pdf" if suf == "_scan.pdf" else suf)
        shutil.copy2(out / "tat_ca_dinh_dang" / key / (key + suf), up / name)
        form.append({"ten_tep": name, "ho_ten": ten, "email": email(key), "vi_tri": tin})
    with open(up / "thong_tin_nop_form.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["ten_tep", "ho_ten", "email", "vi_tri"]); w.writeheader(); w.writerows(form)
    j = out / "jd"; j.mkdir()
    shutil.copy2(jd_net, j / "jd_backend_dotnet.txt")
    shutil.copy2(jd_py, j / "jd_backend_python_fastapi.txt")
    src = out / "_ma_tao"; src.mkdir()
    for f in ("make_cvs.py", "cv_noi_dung.py", "cv_demo_noi_dung.py", "build_demo.py"):
        shutil.copy2(Path(__file__).parent / f, src / f)

if __name__ == "__main__":
    main()
