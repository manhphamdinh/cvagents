from cvagents.extractor.models import Block, RawDocument
from cvagents.extractor.agent import CVExtractorAgent


def test_inline_bullets_are_split():
    raw = RawDocument(blocks=[
        Block(text="KỸ NĂNG", label="section_header"),
        Block(text="· Python · SQL · phân tích dữ liệu · học máy", label="text"),
    ])
    p = CVExtractorAgent().process(raw, None)
    got = {s.lower() for s in p.ky_nang.goc + p.ky_nang.chuan_hoa}
    assert {"phân tích dữ liệu", "học máy"} <= got
    assert not any("·" in s for s in p.ky_nang.goc)
