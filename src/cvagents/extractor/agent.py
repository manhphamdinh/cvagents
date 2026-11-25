"""CV_Extractor_Agent: tệp CV thô → CVProfile (JSON có cấu trúc), pipeline 8 bước của mục 2.3.4."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from cvagents.common.schemas import Contact, CVProfile, Skills
from cvagents.extractor import confidence, entities as ent, fields, layout, quality
from cvagents.extractor.models import Block, RawDocument
from cvagents.extractor.normalize import normalize_text
from cvagents.extractor.reader import OCR_CAPABLE, read_document
from cvagents.extractor.sections import PERSONAL, route


class CVExtractorAgent:
    def __init__(self, today: date | None = None):
        self.today = today or date.today()

    # -- Bước 1–2 ---------------------------------------------------------
    def read(self, path: str | Path) -> tuple[RawDocument, list[Block]]:
        path = Path(path)
        raw, ocr_blocks = read_document(path)
        if not raw.used_ocr and path.suffix.lower() in OCR_CAPABLE:
            text = "\n".join(b.text for b in raw.blocks)
            if quality.assess(text, raw.n_pages).need_ocr:
                raw, ocr_blocks = read_document(path, force_ocr=True)
        return raw, ocr_blocks

    # -- Bước 3–8 ---------------------------------------------------------
    def extract(self, path: str | Path) -> CVProfile:
        raw, ocr_blocks = self.read(path)
        profile = self.process(raw, ocr_blocks)
        return profile

    def process(self, raw: RawDocument, ocr_blocks: list[Block] | None = None) -> CVProfile:
        blocks = layout.order_document(raw.blocks, raw.page_width) if raw.used_ocr else raw.blocks
        sections = route(blocks)  # bước 3 (trong route), 4, 5
        full_text = "\n".join(
            normalize_text(b.text) if b.table is None else " ".join(c for r in b.table for c in r) for b in blocks
        )

        personal_lines = [ln.text for ln in sections.get(PERSONAL)]
        personal = "\n".join(personal_lines)
        contact = Contact(
            ho_ten=ent.find_name(personal_lines),
            email=ent.find_email(personal) or ent.find_email(full_text),
            so_dien_thoai=ent.find_phone(personal) or ent.find_phone(full_text),
            github=ent.find_link(ent.GITHUB_RE, full_text),
            linkedin=ent.find_link(ent.LINKEDIN_RE, full_text),
        )

        skill_lines = [ln.text for ln in sections.get("ky_nang")]
        phrases = [p for ln in skill_lines for p in ent.split_skill_line(ln)]
        experience = fields.build_experience(sections.get("kinh_nghiem"), self.today)
        intervals = [(e.thang_bat_dau, e.thang_ket_thuc) for e in experience if e.thang_bat_dau is not None]

        profile = CVProfile(
            thong_tin_ca_nhan=contact,
            muc_tieu=sections.text("muc_tieu") or None,
            hoc_van=fields.build_education(sections.get("hoc_van"), self.today),
            kinh_nghiem=experience,
            du_an=fields.build_projects(sections.get("du_an"), self.today),
            ky_nang=Skills(goc=skill_lines, chuan_hoa=ent.canonical_skills(phrases)),
            chung_chi=[ln.text for ln in sections.get("chung_chi")],
            tong_thang_kinh_nghiem=ent.total_months(intervals),
            nhanh_doc="ocr" if raw.used_ocr else "text",
            van_ban_goc=full_text,
        )
        profile.do_tin_cay = round(confidence.extraction_confidence(ocr_blocks if raw.used_ocr else None), 3)
        profile.muc_day_du = confidence.completeness(profile)
        profile.canh_bao = confidence.warnings(profile)
        return profile
