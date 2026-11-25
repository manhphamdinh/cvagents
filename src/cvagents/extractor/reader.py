"""Bước 1–2: đọc tài liệu qua Docling (adapter). Mọi import nặng nằm trong hàm để phần còn lại test được độc lập."""
from __future__ import annotations

from pathlib import Path

from cvagents.common.config import load_config
from cvagents.extractor.models import Block, RawDocument

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
OCR_CAPABLE = IMAGE_SUFFIXES | {".pdf"}
DEFAULT_PAGE_WIDTH = 595.0
PAGE_IMAGE_SCALE = 1.5


def _ocr_options(force_full_page: bool):
    engine = load_config()["extractor"]["ocr_engine"]
    if engine == "easyocr":
        from docling.datamodel.pipeline_options import EasyOcrOptions

        return EasyOcrOptions(lang=["vi", "en"], force_full_page_ocr=force_full_page)
    if engine == "tesseract":
        from docling.datamodel.pipeline_options import TesseractCliOcrOptions

        return TesseractCliOcrOptions(lang=["vie", "eng"], force_full_page_ocr=force_full_page)
    from docling.datamodel.pipeline_options import RapidOcrOptions
    from rapidocr import LangRec, ModelType, OCRVersion

    return RapidOcrOptions(
        force_full_page_ocr=force_full_page,
        text_score=load_config()["extractor"]["tau_ocr"],  # công thức (2.6)
        rapidocr_params={  # bộ nhận dạng chữ Latin
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": LangRec.LATIN,
            "Rec.model_type": ModelType.MOBILE,
        },
    )


def _converter(do_ocr: bool, force_full_page: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = do_ocr
    opts.do_table_structure = True
    opts.generate_page_images = True  # để hiển thị ảnh trang và các khối nhận diện trên giao diện
    opts.images_scale = PAGE_IMAGE_SCALE
    if do_ocr:
        opts.ocr_options = _ocr_options(force_full_page)
    pdf_kw = {"backend": _pdfium_backend()} if _needs_pdfium() else {}
    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opts, **pdf_kw),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
    })


def _needs_pdfium() -> bool:
    """docling-parse (bộ đọc PDF mặc định) không mở được tệp tài nguyên của nó khi thư mục cài đặt có chữ có dấu,
    ví dụ D:/Neu/Khoá luận/.venv trên Windows; khi đó mọi PDF đều lỗi "unexpected error while opening"."""
    import docling_parse

    return not Path(docling_parse.__file__).as_posix().isascii()


def _pdfium_backend():
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

    return PyPdfiumDocumentBackend


def read_document(path: str | Path, force_ocr: bool = False) -> tuple[RawDocument, list[Block]]:
    """Trả về (tài liệu thô, các vùng OCR O′). Danh sách vùng OCR rỗng nếu đi nhánh lớp văn bản."""
    path = Path(path)
    ocr = force_ocr or path.suffix.lower() in IMAGE_SUFFIXES
    result = _converter(do_ocr=ocr, force_full_page=ocr).convert(str(path))
    return _to_raw(result, ocr), (_ocr_blocks(result) if ocr else [])


def _to_raw(result, used_ocr: bool) -> RawDocument:
    from docling_core.types.doc import DocItemLabel, TableItem

    doc = result.document
    raw = RawDocument(used_ocr=used_ocr, n_pages=max(len(doc.pages), 1))
    sizes = {n: p.size for n, p in doc.pages.items()}
    if sizes:
        raw.page_width = float(next(iter(sizes.values())).width)
    for n, p in doc.pages.items():
        raw.page_sizes[n] = (float(p.size.width), float(p.size.height))
        if p.image is not None and p.image.pil_image is not None:
            raw.page_images[n] = p.image.pil_image
    for item, _ in doc.iterate_items():
        label = getattr(item.label, "value", str(item.label))
        if label in (DocItemLabel.PAGE_HEADER.value, DocItemLabel.PAGE_FOOTER.value):
            continue
        b = Block(text="", label=label)
        if item.prov:
            pv = item.prov[0]
            bb = pv.bbox.to_top_left_origin(float(sizes[pv.page_no].height)) if pv.page_no in sizes else pv.bbox
            b.x1, b.y1, b.x2, b.y2, b.page = bb.l, bb.t, bb.r, bb.b, pv.page_no
        if isinstance(item, TableItem):
            grid = item.data.grid
            b.table = [[c.text for c in row] for row in grid]
            b.label = "table"
        else:
            b.text = getattr(item, "text", "") or ""
            if not b.text.strip():
                continue
        raw.blocks.append(b)
    return raw


def _ocr_blocks(result) -> list[Block]:
    """Các ô OCR (t_k, p_k, b_k) của mọi trang, dùng cho κ (2.12)."""
    out = []
    for page in result.pages:
        for el in page.assembled.elements:
            for cell in el.cluster.cells:
                if cell.from_ocr and cell.text.strip():
                    r = cell.rect.to_bounding_box()
                    out.append(Block(cell.text, r.l, r.t, r.r, r.b, conf=float(cell.confidence), page=page.page_no))
    return out
