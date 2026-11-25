import pytest

from cvagents.analysis import CVJDAnalysisAgent
from cvagents.analysis.embedder import HashingEmbedder
from cvagents.analysis.jd_parser import parse_jd
from cvagents.common.schemas import Contact, CVProfile, Experience, Education, Skills

JD_TEXT = """Backend Developer
Yêu cầu:
- Tối thiểu 2 năm kinh nghiệm phát triển backend
- Tốt nghiệp đại học chuyên ngành CNTT
- Thành thạo Python và FastAPI
- Có kinh nghiệm với PostgreSQL
Ưu tiên:
- Biết Docker, Kubernetes
Quyền lợi:
- Lương thưởng hấp dẫn, làm việc với Java
"""


def cv(skills, months=0, level=3):
    return CVProfile(
        thong_tin_ca_nhan=Contact(ho_ten="A"),
        hoc_van=[Education(bac_hoc=level)],
        kinh_nghiem=[Experience(chuc_danh="Backend Developer", mo_ta=["Xây dựng REST API"])],
        ky_nang=Skills(goc=[", ".join(skills)], chuan_hoa=skills),
        tong_thang_kinh_nghiem=months,
    )


def test_parse_jd_splits_must_nice_and_ignores_benefits():
    jd = parse_jd(JD_TEXT)
    assert jd.tieu_de == "Backend Developer" and "Backend Developer" not in jd.yeu_cau_khac
    assert jd.ky_nang_bat_buoc == ["Python", "FastAPI", "PostgreSQL"]
    assert jd.ky_nang_uu_tien == ["Docker", "Kubernetes"]
    assert "Java" not in jd.ky_nang_bat_buoc + jd.ky_nang_uu_tien
    assert jd.so_thang_kinh_nghiem_toi_thieu == 24
    assert jd.bac_hoc_toi_thieu == 3


def test_strong_candidate_passes_weak_fails():
    agent = CVJDAnalysisAgent(embedder=HashingEmbedder())
    strong = agent.analyze(cv(["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"], 36), JD_TEXT)
    weak = agent.analyze(cv(["Excel"], 3, level=2), JD_TEXT)
    assert strong.diem_tong > weak.diem_tong
    assert strong.phu_hop and not weak.phu_hop
    assert {m.yeu_cau for m in weak.chua_dap_ung} >= {"Python", "FastAPI", "PostgreSQL"}
    assert strong.diem_thanh_phan["bat_buoc"] == 1.0


def test_experience_shortfall_is_explained():
    r = CVJDAnalysisAgent(embedder=HashingEmbedder()).analyze(cv(["Python", "FastAPI", "PostgreSQL"], 12), JD_TEXT)
    assert r.diem_thanh_phan["kinh_nghiem"] == 0.5
    assert any("12 tháng" in n for n in r.ghi_chu)


def test_components_absent_from_jd_are_not_scored():
    r = CVJDAnalysisAgent(embedder=HashingEmbedder()).analyze(cv(["Python"]), "Yêu cầu:\n- Thành thạo Python")
    assert set(r.diem_thanh_phan) == {"bat_buoc", "ngu_nghia"}
    assert r.diem_tong == pytest.approx(1.0)


def test_skill_requirements_need_the_skill_not_a_similar_one():
    from cvagents.analysis.jd_parser import scan_skills
    jd = parse_jd("Backend .NET\nYêu cầu:\n- Thành thạo C# và .NET Core\n"
                  "- Có kinh nghiệm với SQL Server hoặc PostgreSQL, viết câu lệnh SQL\n- Sử dụng Git")
    assert jd.ky_nang_bat_buoc == ["C#", ".NET", "SQL Server hoặc PostgreSQL", "SQL", "Git"]
    assert scan_skills("React Native, SQL Server, go to market") == ["React Native", "SQL Server"]  # dài trước; "go" ≠ Go
    agent = CVJDAnalysisAgent(embedder=HashingEmbedder())
    java = agent.analyze(cv(["Java", "PostgreSQL", "Git"]), jd)
    assert {m.yeu_cau for m in java.chua_dap_ung} >= {"C#", ".NET"}          # Java không thay được C#
    assert not java.phu_hop and any("C#" in n for n in java.ghi_chu)
    dotnet = cv(["C#", "Git"])
    dotnet.van_ban_goc = "Dự án: ASP.NET Core Web API, SQL Server"           # kỹ năng nằm trong mô tả dự án
    r = agent.analyze(dotnet, jd)
    assert r.diem_thanh_phan["bat_buoc"] == 1.0                              # SQL Server ⇒ SQL; một trong hai lựa chọn
    assert any(m.yeu_cau == "SQL" and m.bang_chung == "van_ban: SQL Server" for m in r.da_dap_ung)
    fastapi = agent.analyze(cv(["FastAPI", "PostgreSQL"], 36), JD_TEXT)    # FastAPI ⇒ Python
    assert fastapi.diem_thanh_phan["bat_buoc"] == 1.0


def test_rank_orders_by_score():
    agent = CVJDAnalysisAgent(embedder=HashingEmbedder())
    ranked = agent.rank([cv(["Excel"]), cv(["Python", "FastAPI", "PostgreSQL"], 30)], JD_TEXT)
    assert ranked[0].diem_tong >= ranked[1].diem_tong
