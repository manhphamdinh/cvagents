"""Form nộp hồ sơ cho ứng viên: một nguồn của CV_Collector_Agent (hồ sơ đi thẳng vào hộp thu thập).

    streamlit run scripts/candidate_portal.py --server.port 8502

Hồ sơ được kiểm tra định dạng và loại trùng ngay khi nộp; ứng viên nhận kết quả trên trang và (nếu bật gui_xac_nhan
hoặc đặt biến môi trường SMTP) thư xác nhận đã nhận hồ sơ. Danh sách vị trí lấy từ tham số ?vi_tri=A,B trên URL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # thay cho PYTHONPATH=src (xem README)

from cvagents.collector import DUP, NEAR_DUP, NEW, CollectorStore, CVCollectorAgent, Incoming, setup_logging  # noqa: E402
from cvagents.collector.models import FORM  # noqa: E402
from cvagents.common.config import load_config  # noqa: E402
from cvagents.mailer import EmailAgent, SentLog, SmtpSettings  # noqa: E402

CFG = load_config()


@st.cache_resource
def store() -> CollectorStore:
    setup_logging()
    return CollectorStore()


st.set_page_config(page_title="Nộp hồ sơ ứng tuyển", page_icon=":material/work:", layout="centered")
st.title("Nộp hồ sơ ứng tuyển")
st.caption(CFG["email"]["cong_ty"])

jobs = [j.strip() for j in st.query_params.get("vi_tri", "").split(",") if j.strip()]
with st.form("apply", clear_on_submit=True):
    ten = st.text_input("Họ và tên *")
    email = st.text_input("Email *")
    sdt = st.text_input("Số điện thoại")
    vi_tri = st.selectbox("Vị trí ứng tuyển *", jobs) if jobs else st.text_input("Vị trí ứng tuyển *")
    cv = st.file_uploader("CV (PDF, DOCX hoặc ảnh, tối đa "
                          f"{CFG['collector']['kich_thuoc_max_mb']} MB) *", type=["pdf", "docx", "png", "jpg", "jpeg"])
    agree = st.checkbox("Tôi đồng ý để công ty lưu trữ và xử lý hồ sơ cho mục đích tuyển dụng, trong tối đa "
                        f"{CFG['collector']['luu_toi_da_ngay']} ngày.")
    sent = st.form_submit_button("Nộp hồ sơ", type="primary")

if sent:
    if not (ten.strip() and "@" in email and vi_tri and cv):
        st.error("Vui lòng điền đủ các mục có dấu * và chọn tệp CV.")
    elif not agree:
        st.error("Cần đồng ý cho phép xử lý dữ liệu cá nhân để nộp hồ sơ.")
    else:
        agent = CVCollectorAgent(store(), mailer=EmailAgent(smtp=SmtpSettings.from_env()), sent_log=SentLog())
        res = agent.ingest(Incoming(cv.name, cv.getvalue(), FORM, tham_chieu=f"form:{email.strip().lower()}",
                                    nguoi_gui=email.strip().lower(), ten_nguoi_gui=ten.strip(),
                                    tieu_de=f"Form ứng tuyển · SĐT {sdt.strip()}" if sdt.strip() else "Form ứng tuyển",
                                    vi_tri=vi_tri), ten_nguon="Form ứng tuyển")[0]
        if res.trang_thai == NEW:
            st.success(f"Đã nhận hồ sơ của {ten.strip()} cho vị trí {vi_tri}. Kết quả sẽ được gửi tới {email.strip()}.")
        elif res.trang_thai in (DUP, NEAR_DUP):
            st.info("Hồ sơ này đã được nộp trước đó, bạn không cần nộp lại.")
        else:
            st.error(f"Không nhận được tệp: {res.ly_do}.")
