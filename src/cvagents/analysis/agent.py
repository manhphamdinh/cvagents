"""CV/JD_Analysis_Agent: (CVProfile, JD) → AnalysisReport có giải thích."""
from __future__ import annotations

from cvagents.analysis import matching, scoring
from cvagents.analysis.embedder import Embedder
from cvagents.analysis.jd_parser import parse_jd
from cvagents.common.config import load_config
from cvagents.common.schemas import AnalysisReport, CVProfile, JobDescription


class CVJDAnalysisAgent:
    def __init__(self, embedder: Embedder | None = None):
        if embedder is None:
            from cvagents.analysis.embedder import SentenceTransformerEmbedder

            embedder = SentenceTransformerEmbedder()
        self.embedder = embedder
        self.cfg = load_config()["analysis"]

    def analyze(self, cv: CVProfile, jd: JobDescription | str) -> AnalysisReport:
        if isinstance(jd, str):
            jd = parse_jd(jd)
        ms = matching.match_requirements(cv, jd, self.embedder, self.cfg["match_threshold"])
        parts = scoring.component_scores(cv, jd, ms, self.embedder)
        total = scoring.total_score(parts)

        notes = [f"Hồ sơ cảnh báo trích xuất: {w}" for w in cv.canh_bao]
        if jd.so_thang_kinh_nghiem_toi_thieu and "kinh_nghiem" in parts and parts["kinh_nghiem"] < 1:
            notes.append(
                f"Kinh nghiệm {cv.tong_thang_kinh_nghiem} tháng, yêu cầu {jd.so_thang_kinh_nghiem_toi_thieu} tháng."
            )
        if not parts:
            notes.append("JD không có yêu cầu nào được nhận diện; điểm không có ý nghĩa.")
        enough, complete = scoring.must_check(parts)
        missing = [m.yeu_cau for m in ms if m.loai == "bat_buoc" and not m.dat]
        if missing:
            notes.append(f"Thiếu kỹ năng bắt buộc: {', '.join(missing)}"
                         + ("." if enough else f" (đáp ứng dưới {self.cfg['must_min']:.0%}: loại)."))
        return AnalysisReport(
            ten_ung_vien=cv.thong_tin_ca_nhan.ho_ten,
            diem_tong=round(total, 4),
            diem_thanh_phan={k: round(v, 4) for k, v in parts.items()},
            phu_hop=bool(parts) and complete and total >= self.cfg["pass_threshold"],
            da_dap_ung=[m for m in ms if m.dat],
            chua_dap_ung=[m for m in ms if not m.dat],
            ghi_chu=notes,
        )

    def rank(self, cvs: list[CVProfile], jd: JobDescription | str) -> list[AnalysisReport]:
        jd = parse_jd(jd) if isinstance(jd, str) else jd
        return sorted((self.analyze(c, jd) for c in cvs), key=lambda r: r.diem_tong, reverse=True)
