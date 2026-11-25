"""Giao diện cho nhà tuyển dụng: thu thập hồ sơ, đánh giá hồ sơ, bảng xếp hạng (bấm một CV để xem chi tiết),
cài đặt email.

    streamlit run scripts/recruiter_app.py
"""
from __future__ import annotations

import io
import os
import smtplib
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # thay cho PYTHONPATH=src (xem README)

from cvagents.analysis import CVJDAnalysisAgent  # noqa: E402
from cvagents.analysis.embedder import SentenceTransformerEmbedder, model_for_lang  # noqa: E402
from cvagents.analysis.jd_parser import parse_jd  # noqa: E402
from cvagents.collector import (  # noqa: E402
    DUP, FAILED, INVALID, NEAR_DUP, NEW, SOURCE_LABEL, STATUS_LABEL as CSTATUS, CollectedCV, CollectorStore,
    CVCollectorAgent, parse_times, setup_logging,
)
from cvagents.collector.changes import diff_lines  # noqa: E402
from cvagents.collector.scheduler import next_slot, previous_slot  # noqa: E402
from cvagents.collector.sources import (  # noqa: E402
    ApiSource, DatasetSource, FolderSource, GoogleDriveSource, ImapSource, JobBoardSource, SearchSource, UrlListSource,
)
from cvagents.collector.sources.net import HttpClient  # noqa: E402
from cvagents.collector.validate import validate  # noqa: E402
from cvagents.common.config import load_config  # noqa: E402
from cvagents.common.text import detect_lang  # noqa: E402
from cvagents.extractor import CVExtractorAgent  # noqa: E402
from cvagents.extractor.layout import order_document  # noqa: E402
from cvagents.extractor.reader import IMAGE_SUFFIXES  # noqa: E402
from cvagents.extractor.visualize import LABEL_COLORS, OTHER_COLOR, draw_blocks  # noqa: E402
from cvagents.mailer import INVITE, EmailAgent, SentLog, SmtpSettings  # noqa: E402
from cvagents.screening import (  # noqa: E402
    EMAIL_LABEL, ERROR, FAIL, PASS, REVIEW, STATUS_LABEL, CVResult, content_key, rank_key, screening_status,
    send_invites,
)

CFG = load_config()
ACFG = CFG["analysis"]
CCFG = CFG["collector"]
LANG = {"vi": "Tiếng Việt", "en": "Tiếng Anh"}
LANG_LC = {"vi": "tiếng Việt", "en": "tiếng Anh"}
BAC_HOC = {1: "Trung cấp", 2: "Cao đẳng", 3: "Đại học", 4: "Thạc sĩ", 5: "Tiến sĩ"}
THANH_PHAN = {"toan_van": ("Toàn văn", "w_fulltext"), "bat_buoc": ("Kỹ năng bắt buộc", "w_must"), "uu_tien": ("Kỹ năng ưu tiên", "w_nice"),
              "ngu_nghia": ("Ngữ nghĩa", "w_semantic"), "kinh_nghiem": ("Kinh nghiệm", "w_experience"),
              "hoc_van": ("Học vấn", "w_education")}
LOAI_YC = {"bat_buoc": "Bắt buộc", "uu_tien": "Ưu tiên", "khac": "Khác"}
STATUS_ICON = {PASS: "🟢", REVIEW: "🟡", FAIL: "🔴", ERROR: "⚪"}
STATUS_COLOR = {PASS: "green", REVIEW: "orange", FAIL: "red", ERROR: "gray"}
STATUS_MI = {PASS: ":material/check_circle:", REVIEW: ":material/pending:", FAIL: ":material/cancel:",
             ERROR: ":material/error:"}

CSS = """
<style>
.block-container {padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1360px;}
h1 {font-size: 1.85rem !important; font-weight: 700 !important; letter-spacing: -0.015em; padding-bottom: 0 !important;}
.page-sub {color: #1f2937; font-size: 0.95rem; margin: 0.1rem 0 1.4rem 0;}
.step {font-weight: 650; font-size: 1.05rem; margin-bottom: 0.4rem;}
.step b {display: inline-flex; align-items: center; justify-content: center; width: 1.55rem; height: 1.55rem;
         border-radius: 999px; background: #1e3a8a; color: #fff; font-size: 0.85rem; margin-right: 0.5rem;}
.muted {color: #1f2937; font-size: 0.85rem;}
.legend span {display: inline-block; margin: 0 6px 4px 0; padding: 1px 9px; border-radius: 999px; color: #fff;
              font-size: 0.75rem;}
[data-testid="stMetricValue"] {font-size: 1.55rem;}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * {color: #1f2937 !important; opacity: 1 !important;}
[data-testid="stWidgetLabel"] p, [data-testid="stMetricLabel"] * {color: #000 !important; font-weight: 600;}
[data-testid="stBadge"] {filter: saturate(1.6) brightness(0.7); font-weight: 600;}
[data-testid="stFileUploaderDropzoneInstructions"] * {color: #1f2937 !important;}
</style>
"""

st.set_page_config(page_title="Sàng lọc hồ sơ ứng viên", page_icon=":material/badge:", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
ss = st.session_state
_TPL = EmailAgent.default_templates()[INVITE]
for k, v in {"cong_ty": CFG["email"]["cong_ty"], "nguoi_gui": CFG["email"]["nguoi_gui"],
             "smtp_user": os.environ.get("SMTP_USER", ""), "smtp_pass": os.environ.get("SMTP_PASSWORD", ""),
             "smtp_host": os.environ.get("SMTP_HOST", CFG["email"]["smtp_host"]),
             "smtp_port": int(os.environ.get("SMTP_PORT", CFG["email"]["smtp_port"])),
             "tpl_subject": _TPL["subject"], "tpl_body": _TPL["body"], "jd_paste": "", "results": [], "flash": [],
             "nav": 0, "src_folder": "", "src_imap_user": "", "src_imap_pass": "", "src_imap_host": CCFG["imap_host"],
             "src_imap_folder": CCFG["imap_folder"], "src_web": "", "src_api": "", "src_api_token": "", "src_dataset": "",
             "src_drive": "", "src_drive_key": "", "col_ack": bool(CCFG["gui_xac_nhan"]), "col_auto": False,
             "col_every": max(1, int(CCFG["chu_ky_giay"]) // 60), "col_last": None,
             "col_mode": "Theo chu kỳ", "col_times": "08:00, 20:00", "src_urls": "", "src_search": "",
             "src_search_api": CCFG["tim_kiem_api"], "src_search_url": CCFG["tim_kiem_url"], "src_search_key": "",
             "src_search_cx": "", "th_delay": float(CCFG["crawl_delay"]), "th_pages": int(CCFG["crawl_max_pages"]),
             "th_depth": int(CCFG["crawl_max_depth"]), "th_files": int(CCFG["crawl_max_tep"]),
             "th_retries": int(CCFG["crawl_retries"]), "th_errors": int(CCFG["crawl_max_loi_lien_tiep"]),
             "th_change": float(CCFG["nguong_thay_doi"]) * 100,
             "tau_s": float(ACFG["pass_threshold"]), "smtp_ok": None, "smtp_msg": None}.items():
    ss.setdefault(k, v)


# ------------------------------------------------------------------ tiện ích
def kept(widget, label: str, name: str, **kw):
    """Widget giữ giá trị khi chuyển trang (Streamlit xoá state của widget không được hiển thị)."""
    ss[name] = widget(label, value=ss[name], key=f"_w_{name}", **kw)
    return ss[name]


def current_smtp() -> SmtpSettings | None:
    if ss.smtp_user and ss.smtp_pass:
        return SmtpSettings(ss.smtp_host, int(ss.smtp_port), ss.smtp_user, ss.smtp_pass)
    return SmtpSettings.from_env()


def mailer() -> EmailAgent:
    return EmailAgent(smtp=current_smtp(), cong_ty=ss.cong_ty, nguoi_gui=ss.nguoi_gui,
                      templates={INVITE: {"subject": ss.tpl_subject, "body": ss.tpl_body}})


def invite(results: list[CVResult]) -> None:
    """Gửi thư mời qua Email_Agent; lỗi kết nối được ghi lại để hiển thị, không làm dừng giao diện."""
    try:
        send_invites(results, mailer(), ss.vi_tri, ss.jd_key, SentLog())
    except smtplib.SMTPAuthenticationError:
        ss.flash.append(("error", "Sai tài khoản hoặc mật khẩu SMTP (Gmail cần App Password). Chưa thư nào được gửi."))
    except (smtplib.SMTPException, OSError) as e:
        ss.flash.append(("error", f"Không kết nối được máy chủ SMTP: {e}. Chưa thư nào được gửi."))


def _tmp_copy(name: str, data: bytes) -> Path:
    p = Path(tempfile.mkdtemp(prefix="cvagents_")) / ("upload" + Path(name).suffix.lower())
    p.write_bytes(data)
    return p


@st.cache_resource(show_spinner="Đang nạp mô hình nhúng…")
def analysis_agent(model: str) -> CVJDAnalysisAgent:
    return CVJDAnalysisAgent(embedder=SentenceTransformerEmbedder(model_name=model))


def missing_weights(model: str) -> bool:
    """Thư mục mô hình cục bộ nhưng không có tệp trọng số (thường do tải từ Drive chưa xong)."""
    d = Path(model)
    return d.is_dir() and not any(d.glob("*.safetensors")) and not any(d.glob("*.bin"))


def load_agent(model: str) -> tuple[CVJDAnalysisAgent, str]:
    """Nạp mô hình theo ngôn ngữ; nếu lỗi thì tạm dùng mô hình tiếng Việt và báo rõ trên giao diện."""
    try:
        if missing_weights(model):
            raise OSError("thiếu tệp model.safetensors")
        return analysis_agent(model), model
    except (OSError, ValueError) as e:
        fallback = ACFG["embed_model_vi"]
        if model == fallback:
            raise
        ss.flash.append(("warning", f"Không nạp được mô hình {Path(model).name} ({e}); đợt này tạm dùng "
                                    f"{Path(fallback).name}."))
        return analysis_agent(fallback), fallback


@st.cache_data(show_spinner="Đang đọc tệp JD…")
def jd_text_from_file(name: str, data: bytes) -> str:
    if name.lower().endswith((".txt", ".md")):
        return data.decode("utf-8-sig", errors="replace")
    raw, _ = CVExtractorAgent().read(_tmp_copy(name, data))
    return "\n".join(b.text if b.table is None else "\n".join(" | ".join(r) for r in b.table) for b in raw.blocks)


@st.cache_resource(show_spinner=False, max_entries=300)
def extract_cv(name: str, data: bytes):
    """(tài liệu thô, hồ sơ) – giữ cả tài liệu thô để vẽ khối nhận diện trong phần chi tiết."""
    agent = CVExtractorAgent()
    raw, ocr = agent.read(_tmp_copy(name, data))
    return raw, agent.process(raw, ocr)


def ordered_blocks(raw) -> list:
    """Khối theo đúng thứ tự đọc mà Extractor dùng (nhánh OCR có sắp lại hai cột, công thức 2.7)."""
    return order_document(raw.blocks, raw.page_width) if raw.used_ocr else raw.blocks


def name_of(r: CVResult) -> str:
    return (r.report and r.report.ten_ung_vien) or r.file


def status_text(r: CVResult) -> str:
    return STATUS_LABEL[r.status] + (" (đã xem xét)" if r.reviewed else "")


def email_text(r: CVResult) -> str:
    return EMAIL_LABEL.get(r.email, r.email.replace("error:", "Lỗi:")).capitalize() if r.email else "—"


def export_frames(items: list[tuple[int, CVResult]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(bảng xếp hạng kèm điểm thành phần, bảng đối chiếu từng yêu cầu) cho tệp Excel."""
    main, detail = [], []
    for rank, r in items:
        rep, prof = r.report, r.profile
        ms = rep.da_dap_ung + rep.chua_dap_ung if rep else []
        row = {"Hạng": rank if rep else None, "Ứng viên": name_of(r), "Điểm tổng": rep.diem_tong if rep else None,
               "Trạng thái": status_text(r), "Thư mời": email_text(r)}
        for k, (label, _) in THANH_PHAN.items():  # để trống nếu JD không có yêu cầu thành phần đó
            row[f"Điểm {label.lower()}"] = rep.diem_thanh_phan.get(k) if rep else None
        row.update({
            "Yêu cầu đạt": f"{sum(m.dat for m in ms)}/{len(ms)}" if ms else "",
            "Yêu cầu chưa đáp ứng": ", ".join(m.yeu_cau for m in ms if not m.dat),
            "Kinh nghiệm (tháng)": prof.tong_thang_kinh_nghiem if prof else None,
            "Email": (prof and prof.thong_tin_ca_nhan.email) or "",
            "Ngôn ngữ CV": LANG.get(r.lang, ""), "Tệp": r.file,
            "Cảnh báo trích xuất": ", ".join(prof.canh_bao) if prof else r.error,
            "Ghi chú đánh giá": "; ".join(rep.ghi_chu) if rep else "",
        })
        main.append(row)
        detail += [{"Hạng": rank, "Ứng viên": name_of(r), "Tệp": r.file, "Yêu cầu": m.yeu_cau,
                    "Loại": LOAI_YC.get(m.loai, m.loai), "Đạt": "Có" if m.dat else "Không", "Cosine": m.cosine,
                    "Bằng chứng trong CV": m.bang_chung or ""} for m in ms]
    main_df = pd.DataFrame(main)
    main_df["Hạng"] = main_df["Hạng"].astype("Int64")
    return main_df, pd.DataFrame(detail, columns=["Hạng", "Ứng viên", "Tệp", "Yêu cầu", "Loại", "Đạt", "Cosine",
                                                  "Bằng chứng trong CV"])


def ranking_xlsx(items: list[tuple[int, CVResult]]) -> bytes:
    """Excel hai sheet: Xếp hạng (điểm thành phần, yêu cầu chưa đạt) và Chi tiết đánh giá (từng yêu cầu)."""
    from openpyxl.styles import Font, PatternFill

    fills = {v: PatternFill("solid", fgColor=c) for v, c in (
        (STATUS_LABEL[PASS], "DCFCE7"), ("Có", "DCFCE7"), (STATUS_LABEL[REVIEW], "FEF3C7"),
        (STATUS_LABEL[FAIL], "FEE2E2"), ("Không", "FEE2E2"))}
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in zip(("Xếp hạng", "Chi tiết đánh giá"), export_frames(items)):
            df.to_excel(w, index=False, sheet_name=name)
            ws = w.sheets[name]
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="1E3A8A")
            ws.freeze_panes = "C2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                head = str(col[0].value)
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 3, 60)
                for c in col[1:]:
                    if head.startswith("Điểm") or head == "Cosine":
                        c.number_format = "0.00"
                    elif head in ("Trạng thái", "Đạt"):
                        f = fills.get(str(c.value).replace(" (đã xem xét)", ""))
                        if f:
                            c.fill = f
    return buf.getvalue()


def page_header(title: str, sub: str) -> None:
    st.title(title)
    st.markdown(f"<p class='page-sub'>{sub}</p>", unsafe_allow_html=True)


def badges(items: list[str], color: str, icon: str | None = None) -> None:
    if not items:
        st.markdown("<span class='muted'>Không có</span>", unsafe_allow_html=True)
        return
    with st.container(horizontal=True, gap="small"):
        for it in items:
            st.badge(it, color=color, icon=icon)


def show_flash() -> None:
    for kind, msg in ss.flash:
        getattr(st, kind)(msg)


# ------------------------------------------------------------------ trang 1: đánh giá
def page_evaluate() -> None:
    page_header("Đánh giá hồ sơ", "Tải mô tả công việc và các CV. Hệ thống trích xuất thông tin, chấm điểm, xếp hạng "
                                  "và tự động gửi thư mời cho ứng viên đạt.")
    left, right = st.columns([1.15, 1], gap="large")
    jd, jd_text, vi_tri, lang = None, "", "", ""
    with left, st.container(border=True):
        st.markdown("<div class='step'><b>1</b>Mô tả công việc</div>", unsafe_allow_html=True)
        source = st.segmented_control("Nguồn JD", ["Tải tệp", "Dán văn bản"], default="Tải tệp",
                                      label_visibility="collapsed") or "Tải tệp"
        if source == "Tải tệp":
            f = st.file_uploader("Tệp JD (.txt, .md, .pdf, .docx)", type=["txt", "md", "pdf", "docx"])
            if f:
                jd_text = jd_text_from_file(f.name, f.getvalue())
        else:
            jd_text = kept(st.text_area, "Nội dung JD", "jd_paste", height=240,
                           placeholder="Backend Developer\nYêu cầu:\n- Tối thiểu 2 năm kinh nghiệm …")
        if jd_text.strip():
            jd = parse_jd(jd_text)
            lang = detect_lang(jd_text)
            vi_tri = st.text_input("Tên vị trí (dùng trong thư mời)", jd.tieu_de)
            with st.container(horizontal=True, gap="small"):
                st.badge(f"JD {LANG[lang]}", icon=":material/translate:", color="violet")
                st.badge("Mô hình nhúng chọn theo ngôn ngữ từng CV", icon=":material/hub:", color="gray")
            if missing_weights(ACFG["embed_model"]):
                st.warning(f"Thư mục {ACFG['embed_model']} chưa có tệp trọng số (model.safetensors); CV tiếng Anh sẽ "
                           f"tạm dùng {Path(ACFG['embed_model_vi']).name}.")
            st.markdown("**Kỹ năng bắt buộc**")
            badges(jd.ky_nang_bat_buoc, "blue")
            st.markdown("**Kỹ năng ưu tiên**")
            badges(jd.ky_nang_uu_tien, "gray")
            m = jd.so_thang_kinh_nghiem_toi_thieu
            c1, c2 = st.columns(2)
            c1.metric("Kinh nghiệm tối thiểu", f"{m / 12:.1f} năm" if m else "—", border=True)
            c2.metric("Bậc học tối thiểu", BAC_HOC.get(jd.bac_hoc_toi_thieu, "—"), border=True)
            if jd.yeu_cau_khac:
                with st.expander(f"Yêu cầu khác ({len(jd.yeu_cau_khac)}), đối chiếu bằng ngữ nghĩa"):
                    st.markdown("\n".join(f"- {r}" for r in jd.yeu_cau_khac))
            if not (jd.ky_nang_bat_buoc or jd.ky_nang_uu_tien or jd.yeu_cau_khac):
                st.warning("Không nhận diện được yêu cầu nào trong JD; điểm đánh giá sẽ không có ý nghĩa.")

    with right, st.container(border=True):
        st.markdown("<div class='step'><b>2</b>Hồ sơ ứng viên</div>", unsafe_allow_html=True)
        pending = collector_store().all(NEW)
        cv_src = st.segmented_control(
            "Nguồn CV", ["Tải tệp", "Hộp thu thập"], default="Tải tệp", label_visibility="collapsed",
            format_func=lambda x: x if x == "Tải tệp" else f"Hộp thu thập ({len(pending)} chờ)") or "Tải tệp"
        if cv_src == "Tải tệp":
            files = st.file_uploader("CV (PDF, DOCX, ảnh) — chọn được nhiều tệp",
                                     type=["pdf", "docx", "png", "jpg", "jpeg"], accept_multiple_files=True)
        else:
            files = pick_collected(pending)
        t, lo = ss.tau_s, max(0.0, ss.tau_s - ACFG["review_margin"])
        smtp = current_smtp()
        st.markdown(
            f"<div class='muted'>🟢 <b>Đạt</b>: điểm ≥ {t:.2f}, tự động gửi thư mời<br>"
            f"🟡 <b>Cần xem xét lại</b>: {lo:.2f} – dưới {t:.2f}, chờ nhà tuyển dụng duyệt<br>"
            f"🔴 <b>Không đạt</b>: dưới {lo:.2f}<br><br>"
            + (f"Thư gửi từ <b>{smtp.user}</b>." if smtp else
               f"Chưa cấu hình SMTP: thư mời được lưu nháp vào <code>{CFG['email']['outbox']}</code>.")
            + "</div>", unsafe_allow_html=True)
        st.write("")
        if st.button("Đánh giá và xếp hạng", type="primary", icon=":material/play_arrow:",
                     disabled=not (files and jd), width="stretch"):
            run_evaluation(files, jd_text, jd, vi_tri, lang)
            st.switch_page(PAGES["rank"])
        if not jd:
            st.caption("Nhập JD ở bước 1 để bắt đầu.")


def run_evaluation(files, jd_text: str, jd, vi_tri: str, lang: str) -> None:
    ss.flash = []
    results, seen = [], set()
    bar = st.progress(0.0)
    for i, f in enumerate(files):
        bar.progress(i / len(files), text=f"Đang xử lý {f.name} ({i + 1}/{len(files)})")
        data = f.getvalue()
        r = CVResult(key=content_key(data), file=f.name, data=data)
        if r.key in seen:  # cùng một tệp chọn hai lần trong lô
            ss.flash.append(("info", f"Bỏ qua {f.name}: trùng nội dung với một tệp khác trong lô."))
            continue
        seen.add(r.key)
        v = validate(f.name, data)
        if not v.ok:  # kiểm tra định dạng của CV_Collector trước khi gọi Extractor
            r.error = v.reason
            results.append(r)
            continue
        try:
            r.raw, r.profile = extract_cv(f.name, data)
            r.lang = detect_lang("\n".join(b.text for b in r.raw.blocks))
            agent, r.model = load_agent(model_for_lang(r.lang))
            r.report = agent.analyze(r.profile, jd)
            r.status = screening_status(r.report, ss.tau_s)
        except Exception as e:  # một CV lỗi không làm dừng cả lô
            r.error = f"{type(e).__name__}: {e}"
        results.append(r)
        if getattr(f, "collected", None):  # hồ sơ từ hộp thu thập: ghi kết quả chuyển giao vào sổ cái
            collector().mark(f.collected, r.profile, r.error)
    bar.empty()
    results.sort(key=rank_key, reverse=True)
    ss.update(results=results, jd_text=jd_text, jd_key=content_key(jd_text), vi_tri=vi_tri or "ứng tuyển",
              jd_lang=lang, nav=ss.nav + 1)
    invite(results)


# ------------------------------------------------------------------ trang 2: xếp hạng + chi tiết
def page_rank() -> None:
    results: list[CVResult] = ss.results
    if not results:
        page_header("Xếp hạng ứng viên", "Chưa có đợt đánh giá nào.")
        st.page_link(PAGES["eval"], label="Đến trang Đánh giá hồ sơ", icon=":material/arrow_forward:")
        return
    page_header("Xếp hạng ứng viên", f"Vị trí <b>{ss.vi_tri}</b> · JD {LANG_LC[ss.jd_lang]} · "
                                     "mô hình nhúng chọn theo ngôn ngữ từng CV")
    show_flash()
    with st.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
        todo =[r for r in results if r.status == PASS and not r.email]
        if todo and st.button(f"Gửi thư mời cho {len(todo)} CV mới đạt", icon=":material/send:"):
            invite(todo)
            st.rerun()
    counts = {s: sum(r.status == s for r in results) for s in STATUS_LABEL}
    cols = st.columns(5)
    cols[0].metric("Tổng hồ sơ", len(results), border=True)
    for c, s in zip(cols[1:], (PASS, REVIEW, FAIL, ERROR)):
        c.metric(f"{STATUS_ICON[s]} {STATUS_LABEL[s]}", counts[s], border=True)

    show = st.pills("Lọc theo trạng thái", list(STATUS_LABEL), selection_mode="multi",
                       default=[s for s in STATUS_LABEL if counts[s]],
                       format_func=lambda s: f"{STATUS_LABEL[s]} ({counts[s]})")
    rows, keys = [], []
    for rank, r in enumerate(results, 1):
        if r.status not in show:
            continue
        keys.append(r.key)
        rows.append({"Hạng": str(rank) if r.report else "—", "Ứng viên": name_of(r),
                     "Điểm": r.report.diem_tong if r.report else None,
                     "Trạng thái": f"{STATUS_ICON[r.status]} {status_text(r)}", "Thư mời": email_text(r),
                     "Email": (r.profile and r.profile.thong_tin_ca_nhan.email) or "—",
                     "Ngôn ngữ CV": LANG.get(r.lang, "—"), "Tệp": r.file})
    df = pd.DataFrame(rows, columns=["Hạng", "Ứng viên", "Điểm", "Trạng thái", "Thư mời", "Email", "Ngôn ngữ CV", "Tệp"])
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key=f"rank_{ss.nav}", column_config={
                          "Hạng": st.column_config.TextColumn(width="small"),
                          "Ứng viên": st.column_config.TextColumn(width="medium"),
                          "Điểm": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f",
                                                                  width="medium")})
    foot_l, foot_r = st.columns([4, 1], vertical_alignment="center")
    foot_l.caption("Bấm vào một dòng để xem chi tiết hồ sơ.")
    items = [(rank, r) for rank, r in enumerate(results, 1) if r.status in show]
    foot_r.download_button("Tải bảng xếp hạng (Excel)", ranking_xlsx(items), "xep_hang.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:", width="stretch")
    if ev.selection.rows:
        show_detail(keys[ev.selection.rows[0]])


def _close_detail() -> None:
    ss.nav += 1  # bảng mới (bỏ chọn dòng) để hộp thoại không tự mở lại


@st.dialog("Chi tiết hồ sơ", width="large", on_dismiss=_close_detail)
def show_detail(key: str) -> None:
    results: list[CVResult] = ss.results
    rank, r = next((i, x) for i, x in enumerate(results, 1) if x.key == key)
    st.markdown(f"### {name_of(r)}")
    with st.container(horizontal=True, gap="small"):
        st.badge(status_text(r), color=STATUS_COLOR[r.status], icon=STATUS_MI[r.status])
        st.badge(r.file, color="gray", icon=":material/description:")
        if r.lang:
            st.badge(f"CV {LANG_LC[r.lang]}", color="violet", icon=":material/translate:")
        if r.profile:
            st.badge("Đọc bằng OCR" if r.profile.nhanh_doc == "ocr" else "Đọc lớp văn bản", color="blue",
                     icon=":material/document_scanner:")
    if r.status == ERROR:
        st.error(f"Không xử lý được tệp: {r.error}")
        return
    rep, prof = r.report, r.profile
    c = st.columns(4)
    c[0].metric("Hạng", f"{rank}/{len(results)}", border=True)
    c[1].metric("Điểm tổng", f"{rep.diem_tong:.2f}", f"{rep.diem_tong - ss.tau_s:+.2f} so với ngưỡng",
                border=True)
    c[2].metric("Kinh nghiệm", f"{prof.tong_thang_kinh_nghiem} tháng", border=True)
    c[3].metric("Thư mời", email_text(r), border=True)

    if r.status == REVIEW and not r.reviewed:
        with st.container(border=True):
            st.markdown(f"**Cần nhà tuyển dụng quyết định.** Điểm {rep.diem_tong:.2f} gần ngưỡng đạt "
                        f"{ss.tau_s:.2f}; xem chi tiết đánh giá bên dưới.")
            b1, b2, _ = st.columns([1.3, 1, 2])
            if b1.button("Duyệt đạt và gửi thư mời", type="primary", icon=":material/send:", width="stretch"):
                r.status, r.reviewed = PASS, True
                invite([r])
                _close_detail()
                st.rerun()
            if b2.button("Không đạt", icon=":material/close:", width="stretch"):
                r.status, r.reviewed = FAIL, True
                _close_detail()
                st.rerun()

    t_eval, t_img, t_prof = st.tabs([":material/analytics: Đánh giá", ":material/image: Ảnh CV và nhận diện",
                                     ":material/person: Hồ sơ trích xuất"])
    with t_eval:
        detail_evaluation(r)
    with t_img:
        detail_images(r)
    with t_prof:
        detail_profile(r)


def detail_evaluation(r: CVResult) -> None:
    rep = r.report
    st.caption(f"Mô hình nhúng {Path(r.model).name} (CV {LANG_LC[r.lang]}, JD {LANG_LC[ss.jd_lang]}) · ngưỡng khớp yêu cầu "
               f"τkh = {ACFG['match_threshold']} · ngưỡng đạt τS = {ss.tau_s:.2f}")
    if r.lang and r.lang != ss.jd_lang:
        st.info(f"CV viết bằng {LANG_LC[r.lang]}, JD bằng {LANG_LC[ss.jd_lang]}: điểm dựa trên khả năng "
                "đa ngôn ngữ của mô hình, nên xem kỹ bằng chứng.")
    if rep.diem_thanh_phan:
        wsum = sum(ACFG[THANH_PHAN[k][1]] for k in rep.diem_thanh_phan)
        # mọi thành phần hiện có đều trọng số 0 thì điểm tổng là trung bình cộng (như scoring.total_score)
        wt = {k: ACFG[THANH_PHAN[k][1]] / wsum if wsum else 1 / len(rep.diem_thanh_phan) for k in rep.diem_thanh_phan}
        st.markdown("**Điểm thành phần**")
        st.dataframe(pd.DataFrame([{
            "Thành phần": THANH_PHAN[k][0], "Điểm": v, "Trọng số": wt[k], "Đóng góp": wt[k] * v,
        } for k, v in rep.diem_thanh_phan.items()]), hide_index=True, width="stretch", column_config={
            "Điểm": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
            "Trọng số": st.column_config.NumberColumn(format="%.2f"),
            "Đóng góp": st.column_config.NumberColumn(format="%.3f")})
        st.caption("Trọng số được chuẩn hoá lại trên các thành phần mà JD có yêu cầu; điểm tổng = tổng cột Đóng góp.")
    st.markdown("**Đối chiếu từng yêu cầu**")
    st.dataframe(pd.DataFrame([{
        "Đạt": "✅" if m.dat else "❌", "Yêu cầu": m.yeu_cau, "Loại": LOAI_YC.get(m.loai, m.loai),
        "Cosine": m.cosine, "Bằng chứng gần nhất trong CV": m.bang_chung or "—",
    } for m in rep.da_dap_ung + rep.chua_dap_ung]), hide_index=True, width="stretch", column_config={
        "Đạt": st.column_config.TextColumn(width="small"),
        "Cosine": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f")})
    for note in rep.ghi_chu:
        st.warning(note)


def detail_images(r: CVResult) -> None:
    raw, prof = r.raw, r.profile
    blocks = list(enumerate(ordered_blocks(raw), 1))
    if raw.page_images:
        pages = sorted(raw.page_images)
        top_l, top_r = st.columns([1, 3], vertical_alignment="center")
        show_layout = top_l.toggle("Hiện khối bố cục", True)
        page = (top_r.selectbox("Trang", pages, format_func=lambda p: f"Trang {p}/{len(pages)}",
                                label_visibility="collapsed") if len(pages) > 1 else pages[0])
        left, right = st.columns(2, gap="medium")
        with left, st.container(border=True):
            st.markdown("**Ảnh gốc**")
            is_image = Path(r.file).suffix.lower() in IMAGE_SUFFIXES
            st.image(r.data if is_image else raw.page_images[page], width="stretch")
        with right, st.container(border=True):
            st.markdown("**Kết quả nhận diện**")
            st.image(draw_blocks(raw.page_images[page], raw.page_sizes[page],
                                 [(i, b) for i, b in blocks if b.page == page] if show_layout else []),
                     width="stretch")
        legend = "".join(f"<span style='background:rgb{LABEL_COLORS.get(lb, OTHER_COLOR)}'>{lb}</span>"
                         for lb in dict.fromkeys(b.label for _, b in blocks))
        st.markdown(f"<div class='legend'>{legend}</div>", unsafe_allow_html=True)
        st.caption((f"Đọc bằng OCR toàn trang, độ tin cậy κ = {prof.do_tin_cay}. " if raw.used_ocr else
                    "Đọc trực tiếp lớp văn bản (không cần OCR theo công thức 2.5), κ = 1. ")
                   + "Số trên mỗi khối là thứ tự đọc dùng để định tuyến nội dung vào các mục.")
    else:
        st.info("Tệp DOCX không có ảnh trang: Docling đọc trực tiếp cấu trúc tài liệu nên không có bố cục trang. "
                "Các khối và nhãn được liệt kê bên dưới.")
    with st.expander(f"Các khối theo thứ tự đọc ({len(blocks)})", expanded=not raw.page_images):
        st.dataframe(pd.DataFrame([{"#": i, "Trang": b.page, "Nhãn": b.label,
                                    "Nội dung": b.text if b.table is None else
                                    " | ".join(" ; ".join(x) for x in b.table)} for i, b in blocks]),
                     hide_index=True, width="stretch")


def detail_profile(r: CVResult) -> None:
    p = r.profile
    c = p.thong_tin_ca_nhan
    a, b = st.columns(2, gap="medium")
    with a, st.container(border=True):
        st.markdown("**Liên hệ**")
        st.markdown(f":material/person: {c.ho_ten or '—'}  \n:material/mail: {c.email or '—'}  \n"
                    f":material/call: {c.so_dien_thoai or '—'}  \n:material/code: {c.github or '—'}  \n"
                    f":material/work: {c.linkedin or '—'}")
    with b, st.container(border=True):
        st.markdown("**Chất lượng trích xuất**")
        st.markdown(f"Độ tin cậy κ: **{p.do_tin_cay}**  \nMức đầy đủ φ: **{p.muc_day_du}**  \n"
                    f"Cảnh báo: {', '.join(p.canh_bao) or 'không có'}")
    if p.muc_tieu:
        st.markdown(f"**Mục tiêu nghề nghiệp**  \n{p.muc_tieu}")
    st.markdown("**Kỹ năng chuẩn hoá**")
    badges(p.ky_nang.chuan_hoa, "blue")
    if p.hoc_van:
        st.markdown("**Học vấn**")
        st.dataframe(pd.DataFrame([{"Trường": e.truong, "Chuyên ngành": e.chuyen_nganh, "Thời gian": e.thoi_gian,
                                    "Bậc học": BAC_HOC.get(e.bac_hoc), "GPA (thang 4)": e.gpa4} for e in p.hoc_van]),
                     hide_index=True, width="stretch")
    for title, items in (("Kinh nghiệm", p.kinh_nghiem), ("Dự án", p.du_an)):
        if items:
            st.markdown(f"**{title}**")
            for e in items:
                head = " · ".join(x for x in (getattr(e, "chuc_danh", None) or getattr(e, "ten", None),
                                               getattr(e, "cong_ty", None)) if x) or "—"
                with st.container(border=True):
                    st.markdown(f"**{head}**" + "".join(f"\n- {m}" for m in e.mo_ta))
    if p.chung_chi:
        st.markdown("**Chứng chỉ:** " + "; ".join(p.chung_chi))
    with st.expander("JSON đầy đủ (đầu ra của CV_Extractor_Agent)"):
        st.json(p.model_dump())


# ------------------------------------------------------------------ trang thu thập hồ sơ (CV_Collector_Agent)
class CollectedFile:
    """Hồ sơ trong hộp thu thập, có cùng giao diện với tệp tải lên (name, getvalue) để dùng chung run_evaluation."""

    def __init__(self, cv: CollectedCV):
        self.collected, self.name = cv, cv.ten_tep

    def getvalue(self) -> bytes:
        return Path(self.collected.duong_dan).read_bytes()


@st.cache_resource
def collector_store() -> CollectorStore:
    setup_logging()
    return CollectorStore()


def collector() -> CVCollectorAgent:
    return CVCollectorAgent(collector_store(), configured_sources(), mailer=mailer(), sent_log=SentLog(),
                            ack=ss.col_ack)


def configured_sources() -> list:
    out = []
    for line in ss.src_folder.splitlines():
        if line.strip():
            out.append(FolderSource(line.strip().strip('"')))
    user, pw = ss.src_imap_user, ss.src_imap_pass
    if user and pw:
        out.append(ImapSource(user, pw, host=ss.src_imap_host, folder=ss.src_imap_folder))
    elif ImapSource.from_env():
        out.append(ImapSource.from_env(folder=ss.src_imap_folder))
    client = lambda: HttpClient(delay=ss.th_delay, retries=int(ss.th_retries))  # noqa: E731
    limits = dict(max_pages=int(ss.th_pages), max_files=int(ss.th_files), max_errors=int(ss.th_errors),
                  threshold=ss.th_change / 100)
    out += [JobBoardSource(u.strip(), client=client(), max_depth=int(ss.th_depth), **limits)
            for u in ss.src_web.splitlines() if u.strip()]
    urls = [u.strip() for u in ss.src_urls.splitlines() if u.strip()]
    if urls:
        out.append(UrlListSource(urls, client=client(), **limits))
    queries = [q.strip() for q in ss.src_search.splitlines() if q.strip()]
    if queries:
        out.append(SearchSource(queries, provider=ss.src_search_api, api_url=ss.src_search_url or None,
                                api_key=ss.src_search_key or None, cse_id=ss.src_search_cx or None, client=client(),
                                **limits))
    out += [ApiSource(u.strip(), token=ss.src_api_token or None) for u in ss.src_api.splitlines() if u.strip()]
    out += [DatasetSource(p.strip().strip('"')) for p in ss.src_dataset.splitlines() if p.strip()]
    if ss.src_drive:
        out.append(GoogleDriveSource(ss.src_drive.strip(), credentials=ss.src_drive_key or None))
    return out


def run_collect() -> None:
    agent = collector()
    if not agent.sources:
        ss.flash = [("warning", "Chưa cấu hình nguồn nào.")]
        return
    with st.spinner("Đang thu thập hồ sơ…"):
        r = agent.collect()
    ss.col_last = datetime.now().strftime("%H:%M:%S")
    t = r.total()
    ss.flash = [("success", f"Lần chạy #{r.id}: {t.thu} tệp, {t.hop_le} hồ sơ mới, {t.trung + t.gan_trung} trùng, "
                            f"{t.khong_hop_le} không hợp lệ, {t.loi_tai} lỗi tải.")]
    ss.flash += [("error", f"Nguồn {s.ten}: {s.loi}") for s in r.nguon if s.loi]


def pick_collected(pending: list[CollectedCV]) -> list[CollectedFile]:
    if not pending:
        st.info("Hộp thu thập chưa có hồ sơ mới.")
        st.page_link(PAGES["collect"], label="Đến trang Thu thập hồ sơ", icon=":material/arrow_forward:")
        return []
    jobs = sorted({c.vi_tri or "(chưa rõ vị trí)" for c in pending})
    chosen = st.multiselect("Vị trí ứng tuyển", jobs, default=jobs)
    picked = [c for c in pending if (c.vi_tri or "(chưa rõ vị trí)") in chosen]
    st.caption(f"{len(picked)} hồ sơ sẽ được đánh giá; sau khi đánh giá, hồ sơ chuyển sang trạng thái Đã đánh giá.")
    return [CollectedFile(c) for c in picked]


def page_collect() -> None:
    store = collector_store()
    rows = store.all()
    page_header("Thu thập hồ sơ", "CV_Collector_Agent nhận CV từ thư mục, hộp thư, trang tuyển dụng, danh sách URL, "
                                  "máy tìm kiếm, API, bộ dữ liệu và Google Drive; kiểm tra định dạng, loại trùng rồi "
                                  "chuyển cho bước đánh giá. Trang đã crawl được lưu lại để phát hiện thay đổi.")
    show_flash()
    count = lambda *ts: sum(r.trang_thai in ts for r in rows)  # noqa: E731
    c = st.columns(5)
    c[0].metric("Tổng tệp đã nhận", len(rows), border=True)
    c[1].metric("🟦 Chờ đánh giá", count(NEW), border=True)
    c[2].metric("✅ Đã đánh giá", count("da_xu_ly", FAILED), border=True)
    c[3].metric("♊ Trùng / gần trùng", count(DUP, NEAR_DUP), border=True)
    c[4].metric("⛔ Không hợp lệ / lỗi tải", count(INVALID, "loi_tai"), border=True)

    left, right = st.columns([1, 1.6], gap="large")
    with left, st.container(border=True):
        st.markdown("<div class='step'><b>1</b>Nguồn hồ sơ</div>", unsafe_allow_html=True)
        with st.expander(":material/folder: Thư mục", expanded=True):
            kept(st.text_area, "Đường dẫn (mỗi dòng một thư mục)", "src_folder", height=68,
                 placeholder="D:/Tuyen dung/CV moi")
            st.caption("Thư mục con được hiểu là vị trí ứng tuyển. Chỉ tệp mới hoặc đã sửa được đọc lại.")
        with st.expander(":material/mail: Hộp thư (IMAP / Gmail)"):
            kept(st.text_input, "Tài khoản", "src_imap_user", placeholder="tuyendung@gmail.com")
            kept(st.text_input, "Mật khẩu ứng dụng", "src_imap_pass", type="password")
            a, b = st.columns(2)
            with a:
                kept(st.text_input, "Máy chủ IMAP", "src_imap_host")
            with b:
                kept(st.text_input, "Thư mục / nhãn", "src_imap_folder")
            st.caption("Đọc tệp đính kèm (kể cả .zip), vị trí lấy từ tiêu đề thư. Hộp thư mở ở chế độ chỉ đọc.")
        with st.expander(":material/travel_explore: Trang tuyển dụng"):
            kept(st.text_area, "URL bắt đầu (mỗi dòng một trang)", "src_web", height=68,
                 placeholder="http://127.0.0.1:8765/")
            st.caption("Đi theo liên kết trong cùng tên miền, tuân thủ robots.txt và các ngưỡng crawl bên dưới.")
        with st.expander(":material/link: Danh sách URL theo dõi"):
            kept(st.text_area, "URL tệp CV hoặc trang chứa CV (mỗi dòng một URL)", "src_urls", height=68,
                 placeholder="https://ten.github.io/cv.pdf")
            st.caption("Mỗi lần thu thập kiểm tra lại từng URL: tệp đổi nội dung thì nhận lại; trang thay đổi thì lưu "
                       "phiên bản mới và kiểm tra lại các CV trên trang.")
        with st.expander(":material/search: Tìm CV trên Internet"):
            kept(st.text_area, "Truy vấn (mỗi dòng một truy vấn)", "src_search", height=68,
                 placeholder='"curriculum vitae" python developer filetype:pdf')
            a, b = st.columns([1, 2])
            with a:
                ss.src_search_api = st.selectbox("API tìm kiếm", ["searxng", "brave", "google"],
                                                 index=["searxng", "brave", "google"].index(ss.src_search_api))
            with b:
                if ss.src_search_api == "searxng":
                    kept(st.text_input, "Địa chỉ SearXNG", "src_search_url")
                else:
                    kept(st.text_input, "Khoá API (SEARCH_API_KEY)", "src_search_key", type="password")
            if ss.src_search_api == "google":
                kept(st.text_input, "Mã công cụ tìm kiếm (GOOGLE_CSE_ID)", "src_search_cx")
            st.caption("Dùng API của máy tìm kiếm, không cào trang kết quả của Google/Bing. CV tìm được là dữ liệu cá "
                       "nhân: chỉ dùng cho mục đích được phép. Thử offline: SearXNG = <trang giả lập>/tim-kiem.")
        with st.expander(":material/api: API"):
            kept(st.text_area, "URL API (mỗi dòng một API)", "src_api", height=68,
                 placeholder="http://127.0.0.1:8765/api/ung-tuyen")
            kept(st.text_input, "Token (nếu có)", "src_api_token", type="password")
        with st.expander(":material/dataset: Bộ dữ liệu"):
            kept(st.text_area, "Thư mục hoặc tệp .zip", "src_dataset", height=68)
        with st.expander(":material/add_to_drive: Google Drive"):
            kept(st.text_input, "ID thư mục Drive", "src_drive")
            kept(st.text_input, "Tệp khoá service account (.json)", "src_drive_key")
        with st.expander(":material/tune: Ngưỡng crawl (trang tuyển dụng, URL, tìm kiếm)"):
            a, b = st.columns(2)
            with a:
                kept(st.number_input, "Giãn cách giữa hai yêu cầu (giây)", "th_delay", min_value=0.0, step=0.5)
                kept(st.number_input, "Số trang tối đa / lần chạy", "th_pages", min_value=1, step=10)
                kept(st.number_input, "Số tệp CV tối đa / lần chạy", "th_files", min_value=1, step=10)
                kept(st.number_input, "Ngưỡng thay đổi trang (%)", "th_change", min_value=0.0, max_value=100.0,
                     step=0.5, help="Tỷ lệ dòng chữ khác so với phiên bản đã lưu để coi là trang đã thay đổi.")
            with b:
                kept(st.number_input, "Độ sâu liên kết tối đa", "th_depth", min_value=0, step=1,
                     help="0: chỉ trang bắt đầu. Danh sách URL và tìm kiếm luôn dùng 0.")
                kept(st.number_input, "Số lần thử lại (429, 5xx, hết giờ)", "th_retries", min_value=0, step=1)
                kept(st.number_input, "Dừng nguồn sau số lỗi liên tiếp", "th_errors", min_value=1, step=1)
            st.caption("Giá trị mặc định ở mục [collector] của config.toml; thay đổi ở đây chỉ áp dụng trong phiên.")
        kept(st.toggle, "Gửi thư xác nhận đã nhận hồ sơ", "col_ack")
        a, b = st.columns([1.2, 1], vertical_alignment="bottom")
        with a:
            kept(st.toggle, "Tự động thu thập", "col_auto")
        with b:
            ss.col_mode = st.segmented_control("Lịch", ["Theo chu kỳ", "Giờ cố định"], default=ss.col_mode,
                                               key="_w_col_mode") or ss.col_mode
        if ss.col_mode == "Theo chu kỳ":
            kept(st.number_input, "Chu kỳ (phút)", "col_every", min_value=1, step=1)
        else:
            kept(st.text_input, "Giờ chạy mỗi ngày", "col_times", placeholder="08:00, 20:00")
        if st.button("Thu thập ngay", type="primary", icon=":material/download:", width="stretch"):
            run_collect()
            st.rerun()
        if ss.col_auto:
            auto_collect()

    with right:
        collect_tables(store, rows)
        page_history(store)


@st.fragment(run_every=60)
def auto_collect() -> None:
    """Chạy lại mỗi phút trong khi trang mở; thu thập khi đủ chu kỳ hoặc khi tới giờ đã đặt."""
    last = ss.get("_col_auto_at")
    now = datetime.now()
    if ss.col_mode == "Giờ cố định":
        try:
            times = parse_times(ss.col_times)
        except ValueError as e:
            st.error(f"Giờ chạy không hợp lệ ({e}). Ví dụ: 08:00, 20:00")
            return
        if last is None:
            ss._col_auto_at = now  # vừa bật: chờ tới mốc giờ kế tiếp
        elif previous_slot(times, now) > last:
            ss._col_auto_at = now
            run_collect()
            st.rerun()
        st.caption(f"Tự động thu thập lúc {', '.join(times)} mỗi ngày khi trang đang mở. Lần kế tiếp: "
                   f"{next_slot(times, now):%H:%M %d/%m}. Lần gần nhất: {ss.col_last or '—'}. Chạy nền không cần mở "
                   f"trang: scripts/collect.py --at {','.join(times)}.")
        return
    if last is None or (now - last).total_seconds() >= ss.col_every * 60:
        ss._col_auto_at = now
        if last is not None:
            run_collect()
            st.rerun()
    st.caption(f"Tự động thu thập mỗi {ss.col_every} phút khi trang đang mở. Lần gần nhất: {ss.col_last or '—'}. "
               "Chạy nền không cần mở trang: scripts/collect.py --watch.")


def collect_tables(store: CollectorStore, rows: list[CollectedCV]) -> None:
    runs = store.runs(20)
    if runs:
        with st.container(border=True):
            st.markdown("<div class='step'><b>2</b>Lần thu thập gần nhất</div>", unsafe_allow_html=True)
            r = runs[0]
            st.caption(f"Lần #{r.id}: {r.bat_dau} → {r.ket_thuc}")
            st.dataframe(pd.DataFrame([{
                "Nguồn": s.ten, "Tệp": s.thu, "Mới": s.hop_le, "Trùng": s.trung + s.gan_trung,
                "Không hợp lệ": s.khong_hop_le, "Lỗi tải": s.loi_tai, "Tải thành công": s.ty_le_thanh_cong,
                "Giây / hồ sơ": s.giay_moi_ho_so, "Trang": s.trang, "Trang mới": s.trang_moi,
                "Trang thay đổi": s.trang_thay_doi, "Lỗi nguồn": s.loi or "",
            } for s in [*r.nguon, r.total()]]), hide_index=True, width="stretch", column_config={
                "Tải thành công": st.column_config.NumberColumn(format="percent"),
                "Giây / hồ sơ": st.column_config.NumberColumn(format="%.3f")})
    with st.container(border=True):
        st.markdown("<div class='step'><b>3</b>Sổ cái hồ sơ</div>", unsafe_allow_html=True)
        if not rows:
            st.info("Chưa có hồ sơ nào. Cấu hình nguồn bên trái rồi bấm Thu thập ngay.")
            return
        present = list(dict.fromkeys(r.trang_thai for r in rows))
        a, b = st.columns(2)
        show = a.multiselect("Trạng thái", present, default=present, format_func=CSTATUS.get)
        srcs = list(dict.fromkeys(r.nguon for r in rows))
        show_src = b.multiselect("Nguồn", srcs, default=srcs, format_func=lambda x: SOURCE_LABEL.get(x, x))
        df = pd.DataFrame([{
            "Nhận lúc": r.thoi_diem_nhan.replace("T", " "), "Tệp": r.ten_tep, "Trạng thái": CSTATUS[r.trang_thai],
            "Nguồn": SOURCE_LABEL.get(r.nguon, r.nguon), "Vị trí": r.vi_tri or "", "Người gửi":
            " ".join(x for x in (r.ten_nguoi_gui, f"<{r.nguoi_gui}>" if r.nguoi_gui else "") if x),
            "Định dạng": r.dinh_dang, "KB": round(r.kich_thuoc / 1024, 1), "Ghi chú": r.ly_do,
            "Tham chiếu": r.tham_chieu,
        } for r in reversed(rows) if r.trang_thai in show and r.nguon in show_src])
        st.dataframe(df, hide_index=True, width="stretch", height=min(460, 38 + 35 * max(len(df), 1)))
        st.download_button("Tải sổ cái (CSV)", df.to_csv(index=False).encode("utf-8-sig"), "so_cai_ho_so.csv",
                           "text/csv", icon=":material/download:")


def page_history(store: CollectorStore) -> None:
    """Các trang web đã crawl, phiên bản đã lưu và khác biệt giữa hai phiên bản liền nhau."""
    pages = store.pages_seen()
    if not pages:
        return
    with st.container(border=True):
        st.markdown("<div class='step'><b>4</b>Trang đã crawl và thay đổi</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{
            "URL": p["url"], "Kiểm tra lần cuối": p["lan_cuoi"].replace("T", " "), "Số lần kiểm tra": p["so_lan"],
            "Phiên bản đã lưu": p["so_phien_ban"], "Thay đổi lần cuối": p["ty_le_lan_cuoi"],
            "Lưu gần nhất": (p["thay_doi_cuoi"] or "").replace("T", " "), "Nguồn": p["nguon"],
        } for p in pages]), hide_index=True, width="stretch", height=min(320, 38 + 35 * len(pages)), column_config={
            "URL": st.column_config.LinkColumn(), "Thay đổi lần cuối": st.column_config.NumberColumn(format="percent")})
        st.caption(f"Trang được lưu thành phiên bản mới khi phần chữ thay đổi từ {ss.th_change:g}% trở lên so với "
                   "phiên bản đã lưu gần nhất.")
        a, b = st.columns([2, 1])
        url = a.selectbox("Trang", [p["url"] for p in pages])
        versions = {v.phien_ban: v for v in store.page_versions(url)}
        n = b.selectbox("Phiên bản", sorted(versions, reverse=True), format_func=lambda k: (
            f"#{k} · {versions[k].thoi_diem.replace('T', ' ')}"))
        v, prev = versions[n], versions.get(n - 1)
        if prev:
            st.caption(f"Khác biệt so với phiên bản #{prev.phien_ban} (thay đổi {v.ty_le_thay_doi:.1%}; "
                       "dòng − bị bỏ, dòng + mới thêm):")
            st.code(diff_lines(prev.text(), v.text(), f"#{prev.phien_ban}", f"#{v.phien_ban}"), language="diff")
        else:
            st.caption("Phiên bản đầu tiên. Phần chữ đã lưu:")
            st.code(v.text()[:5000], language=None)
        st.download_button("Tải HTML phiên bản này", v.html(), f"trang_v{v.phien_ban}.html", "text/html",
                           icon=":material/download:")


# ------------------------------------------------------------------ trang 3: cài đặt email
def smtp_login() -> None:
    """Nút Đăng nhập: đăng nhập thử SMTP với tài khoản đang nhập, ghi kết quả để hiển thị."""
    s = SmtpSettings(ss.smtp_host, int(ss.smtp_port), ss.smtp_user, ss.smtp_pass)
    try:
        s.check()
        ss.smtp_ok = (s.host, s.port, s.user, s.password)
        ss.smtp_msg = ("success", f"Đăng nhập thành công: {s.user} ({s.host}:{s.port}).")
    except smtplib.SMTPAuthenticationError as e:
        ss.smtp_ok = None
        ss.smtp_msg = ("error", f"Sai tài khoản hoặc mật khẩu: {e.smtp_error.decode(errors='replace')}. Gmail cần App "
                                "Password; Microsoft 365 cần SMTP AUTH được bật cho tài khoản.")
    except (smtplib.SMTPException, OSError) as e:
        ss.smtp_ok = None
        ss.smtp_msg = ("error", f"Không kết nối được {s.host}:{s.port}: {e}")


def smtp_logout() -> None:
    ss.smtp_pass, ss.smtp_ok, ss.smtp_msg = "", None, None
    ss.pop("_w_smtp_pass", None)


def page_email() -> None:
    head = st.container()  # dòng trạng thái điền sau các ô nhập để khớp tài khoản vừa gõ
    left, right = st.columns([1, 1.3], gap="large")
    with left, st.container(border=True):
        st.markdown("<div class='step'><b>1</b>Tài khoản gửi</div>", unsafe_allow_html=True)
        kept(st.text_input, "Tên công ty", "cong_ty")
        kept(st.text_input, "Người gửi", "nguoi_gui")
        kept(st.text_input, "Tài khoản SMTP", "smtp_user", placeholder="tuyendung@congty.vn")
        kept(st.text_input, "Mật khẩu ứng dụng", "smtp_pass", type="password")
        h, p = st.columns([2, 1])
        with h:
            kept(st.text_input, "Máy chủ SMTP", "smtp_host")
        with p:
            kept(st.number_input, "Cổng", "smtp_port", step=1)
        st.caption("Gmail: smtp.gmail.com, cổng 587, dùng App Password. Microsoft 365 (Outlook, email trường): "
                   "smtp.office365.com, cổng 587. Mật khẩu chỉ giữ trong phiên làm việc, không ghi ra tệp; có thể đặt "
                   "sẵn bằng biến môi trường SMTP_USER, SMTP_PASSWORD.")
        a, b = st.columns(2)
        with a:
            if st.button("Đăng nhập", icon=":material/login:", type="primary", width="stretch",
                         disabled=not (ss.smtp_user and ss.smtp_pass)):
                with st.spinner("Đang đăng nhập…"):
                    smtp_login()
                st.rerun()
        with b:
            if st.button("Đăng xuất", icon=":material/logout:", width="stretch", disabled=not ss.smtp_pass):
                smtp_logout()
                st.rerun()
        if ss.smtp_msg:
            getattr(st, ss.smtp_msg[0])(ss.smtp_msg[1])
    smtp = current_smtp()
    verified = smtp is not None and ss.smtp_ok == (smtp.host, smtp.port, smtp.user, smtp.password)
    with head:
        page_header("Cài đặt email", f"Đã đăng nhập: thư mời được gửi từ <b>{smtp.user}</b>." if verified else
                    f"Tài khoản <b>{smtp.user}</b> chưa đăng nhập thử: bấm Đăng nhập để kiểm tra." if smtp else
                    f"Chưa có tài khoản SMTP: thư mời được lưu nháp (.eml) vào <code>{CFG['email']['outbox']}</code>.")
    with right, st.container(border=True):
        st.markdown("<div class='step'><b>2</b>Mẫu thư mời phỏng vấn</div>", unsafe_allow_html=True)
        kept(st.text_input, "Tiêu đề", "tpl_subject")
        kept(st.text_area, "Nội dung", "tpl_body", height=300)
        st.caption("Dùng được {ten}, {vi_tri}, {cong_ty}, {nguoi_gui}.")
        if st.button("Khôi phục mẫu mặc định", icon=":material/restart_alt:"):
            ss.tpl_subject, ss.tpl_body = _TPL["subject"], _TPL["body"]
            for k in ("_w_tpl_subject", "_w_tpl_body"):
                ss.pop(k, None)
            st.rerun()


PAGES = {
    "collect": st.Page(page_collect, title="Thu thập hồ sơ", icon=":material/inbox:", url_path="thu-thap"),
    "eval": st.Page(page_evaluate, title="Đánh giá hồ sơ", icon=":material/upload_file:", default=True),
    "rank": st.Page(page_rank, title="Xếp hạng ứng viên", icon=":material/leaderboard:", url_path="xep-hang"),
    "email": st.Page(page_email, title="Cài đặt email", icon=":material/mail:", url_path="email"),
}
st.navigation(list(PAGES.values()), position="top").run()
