from datetime import date

import pytest

from cvagents.extractor import entities as ent
from cvagents.extractor import quality
from cvagents.extractor.agent import CVExtractorAgent
from cvagents.extractor.layout import reading_order
from cvagents.extractor.models import Block, RawDocument
from cvagents.extractor.normalize import normalize_text

TODAY = date(2026, 6, 1)


def _m(y, q):
    return 12 * y + q - 1  # công thức (2.9)


# ---- Bước 1: chất lượng lớp văn bản
def test_quality_flags_tcvn3_garbage():
    r = quality.assess("Kü n¨ng chuyªn m«n µ Ö ¨ " * 40, 1)
    assert r.r_la > 0.05 and r.need_ocr


def test_quality_flags_glyph_font():
    assert quality.odd_char_ratio("GLYPH<123> GLYPH<45> ab") > 0.5


def test_quality_accepts_clean_vietnamese():
    text = ("Kinh nghiệm phát triển ứng dụng phía máy chủ bằng Python và Java. " * 12)
    r = quality.assess(text, 1)
    assert not r.need_ocr, r


def test_density_triggers_ocr_for_scanned_pdf():
    assert quality.assess("Phạm Đình Mạnh", 2).need_ocr


# ---- Bước 2: thứ tự đọc hai cột
def _b(text, x1, y1, x2=None):
    return Block(text, x1, y1, x2 or x1 + 100, y1 + 10)


def test_two_column_reading_order():
    blocks = [_b("L1", 20, 10), _b("R1", 320, 12), _b("L2", 20, 40), _b("R2", 320, 42),
              _b("L3", 20, 70), _b("R3", 320, 72)]
    assert [b.text for b in reading_order(blocks, 595)] == ["L1", "L2", "L3", "R1", "R2", "R3"]


def test_single_column_untouched():
    blocks = [_b(f"x{i}", 20, i * 20) for i in range(8)]
    assert reading_order(blocks, 595) == blocks


# ---- Bước 3
def test_normalize_nfc_and_bullets():
    decomposed = "Kỹ năng"  # ỹ ở dạng tổ hợp
    out = normalize_text("• " + decomposed + "  gạch—nối")
    assert out == "Kỹ năng gạch-nối"
    assert normalize_text("tiền-\nxử lý") == "tiềnxử lý"


# ---- Bước 6
def test_phone_and_email():
    assert ent.find_phone("SĐT: 0987 654 321") == "+84987654321"
    assert ent.find_phone("+84 912.345.678") == "+84912345678"
    assert ent.find_phone("12345") is None
    assert ent.find_email("Email: manhpd@st.neu.edu.vn") == "manhpd@st.neu.edu.vn"


def test_parse_range_variants():
    s, e, _ = ent.parse_range("06/2022 - nay", TODAY)
    assert (s, e) == (_m(2022, 6), _m(2026, 6))
    s, e, _ = ent.parse_range("2023 - Present", TODAY)
    assert s == _m(2023, 1)
    s, e, _ = ent.parse_range("Jan. 2024 to Mar 2024", TODAY)
    assert (s, e) == (_m(2024, 1), _m(2024, 3))
    assert ent.parse_range("2020 - 2021", TODAY)[:2] == (_m(2020, 1), _m(2021, 12))
    assert ent.parse_range("không có thời gian") is None


def test_total_months_merges_overlap():
    a = (_m(2022, 1), _m(2022, 12))   # 12 tháng
    b = (_m(2022, 7), _m(2023, 6))    # chồng lấn 6 tháng
    assert ent.total_months([a, b]) == 18
    assert ent.total_months([]) == 0


def test_skills_gazetteer_and_split():
    phrases = ent.split_skill_line("Python (DuckDB, Polars), ReactJS | CI/CD")
    assert set(phrases) >= {"Python", "DuckDB", "Polars", "ReactJS", "CI/CD"}
    assert set(ent.canonical_skills(phrases)) == {"Python", "DuckDB", "Polars", "React", "CI/CD"}


def test_short_aliases_require_exact_match():
    assert ent.canonical_skills(["Sq"]) == []          # gần SQL nhưng quá ngắn
    assert ent.canonical_skills(["SQL"]) == ["SQL"]
    assert ent.canonical_skills(["Pythn"]) == ["Python"]  # lỗi OCR một ký tự, bí danh dài


def test_degree_and_gpa():
    assert ent.degree_level("Cử nhân Công nghệ thông tin") == 3
    assert ent.degree_level("Thạc sĩ KHMT") == 4
    assert ent.gpa_to_4("GPA 3.3/4.0") == 3.3
    assert ent.gpa_to_4("Điểm TB: 8.2/10") == 3.28
    assert ent.gpa_to_4("GPA: 8.5") == 3.4  # không ghi thang, > 4 → thang 10


# ---- Toàn pipeline (bước 3–8) trên văn bản dựng sẵn, không cần Docling
def _doc(*items):
    return RawDocument(blocks=[Block(t, label=l) for t, l in items])


def test_process_worked_example_from_thesis():
    """Ví dụ cuối mục 2.3.4: κ ≈ 0,966, φ = 0,5, cảnh báo THIEU_TRUONG."""
    raw = RawDocument(used_ocr=True, blocks=[
        Block("Phạm Đình Mạnh", 120, 80, 450, 120, label="title"),
        Block("Email: manhpd@st.neu.edu.vn", 120, 140, 500, 175),
        Block("KỸ NĂNG", 120, 190, 260, 215, label="section_header"),
        Block("Python, Java, SQL", 120, 225, 400, 260),
    ])
    ocr = [Block("Phạm Đình Mạnh", conf=0.98), Block("Email: manhpd@st.neu.edu.vn", conf=0.97),
           Block("KỸ NĂNG", conf=0.96), Block("Python, Java, SQL", conf=0.95)]
    p = CVExtractorAgent(TODAY).process(raw, ocr)
    assert p.thong_tin_ca_nhan.ho_ten == "Phạm Đình Mạnh"
    assert p.thong_tin_ca_nhan.email == "manhpd@st.neu.edu.vn"
    assert p.ky_nang.goc == ["Python, Java, SQL"]
    assert p.ky_nang.chuan_hoa == ["Python", "Java", "SQL"]
    assert p.do_tin_cay == pytest.approx(0.966, abs=1e-3)
    assert p.muc_day_du == 0.5
    assert p.canh_bao == ["THIEU_TRUONG"]
    assert p.tong_thang_kinh_nghiem == 0


def test_process_full_cv():
    raw = _doc(
        ("Nguyễn Văn An", "title"),
        ("Email: an@example.com | 0912345678", "text"),
        ("HỌC VẤN", "section_header"),
        ("Đại học Kinh tế Quốc dân", "text"),
        ("2020 - 2024", "text"),
        ("Cử nhân Công nghệ thông tin, GPA 3.3/4.0", "text"),
        ("KINH NGHIỆM LÀM VIỆC", "section_header"),
        ("Công ty TNHH ABC", "text"),
        ("Backend Developer, 06/2022 - nay", "text"),
        ("Xây dựng REST API bằng FastAPI", "list_item"),
        ("Tối ưu truy vấn PostgreSQL", "list_item"),
        ("KỸ NĂNG", "section_header"),
        ("Backend: Python, FastAPI, PostgreSQL, Docker", "text"),
    )
    p = CVExtractorAgent(TODAY).process(raw)
    assert p.thong_tin_ca_nhan.so_dien_thoai == "+84912345678"
    assert p.hoc_van[0].truong == "Đại học Kinh tế Quốc dân"
    assert p.hoc_van[0].bac_hoc == 3 and p.hoc_van[0].gpa4 == 3.3
    assert len(p.kinh_nghiem) == 1
    exp = p.kinh_nghiem[0]
    assert exp.cong_ty == "Công ty TNHH ABC" and len(exp.mo_ta) == 2
    assert p.tong_thang_kinh_nghiem == (_m(2026, 6)) - (_m(2022, 6)) + 1
    assert set(p.ky_nang.chuan_hoa) == {"Python", "FastAPI", "PostgreSQL", "Docker"}
    assert p.muc_day_du == 1.0 and p.canh_bao == []
    assert p.do_tin_cay == 1.0


def test_unaccented_heading_and_table_routing():
    raw = RawDocument(blocks=[
        Block("Tran Thi B", label="title"),
        Block("KY NANG", label="text"),
        Block("", label="table", table=[["Nhóm kỹ năng", "Danh sách"], ["Ngôn ngữ", "Python, Java"]]),
    ])
    p = CVExtractorAgent(TODAY).process(raw)
    assert set(p.ky_nang.chuan_hoa) == {"Python", "Java"}
