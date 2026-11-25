"""Kiểm thử tích hợp toàn hệ thống: CV_Collector → CV_Extractor → CV/JD_Analysis → Email_Agent trên một đợt tuyển dụng
mô phỏng, có đáp án cho từng bước.

    python scripts/eval_e2e.py --lang vi --data-dir /content/drive/MyDrive/khoaluan/cvjd_vi --out results/e2e_vi

Kịch bản
  1. Chọn --n-jd JD trong tập kiểm tra (không trùng dữ liệu tinh chỉnh), mỗi JD lấy tối đa --cv-moi-jd CV có nhãn
     (cân bằng phù hợp / không phù hợp, mỗi CV chỉ thuộc một JD). Mỗi CV được gán họ tên, email, SĐT tổng hợp rồi dựng
     thành PDF có lớp văn bản, PDF quét (ảnh nhiễu, cần OCR) hoặc DOCX và thả vào inbox/<JD>/ như nhà tuyển dụng.
  2. Thêm các tệp "nhiễu" có đáp án: bản sao byte (trùng), CV xuất lại (gần trùng), tệp hỏng / sai định dạng / .doc cũ /
     rỗng (không hợp lệ), PDF đặt đuôi .docx (hợp lệ, đổi đuôi), một tệp .zip chứa 2 CV.
  3. Chạy cả chuỗi đúng như ứng dụng: CVCollectorAgent(FolderSource) có gửi thư xác nhận → screen_files (Extractor +
     Analysis theo config.toml, collector.mark sau mỗi CV) → send_invites. Email_Agent dùng máy chủ SMTP giả lập: thư
     được ghi thành .eml và coi như đã gửi, để kiểm tra cả nhật ký chống gửi trùng.
  4. Chạy lại: quét lại thư mục (không được nhận lại tệp cũ), nộp lại toàn bộ tệp hợp lệ qua một nguồn khác (phải bị
     loại vì trùng), đánh giá lại và gửi thư lần hai (không được gửi trùng).

Kết quả trong --out: ket_qua_e2e.json (đầy đủ), bang_*.csv (các bảng của mục kiểm thử tích hợp), tung_cv.csv, outbox/.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import eval_matching as em  # noqa: E402
import eval_pdf as ep  # noqa: E402

HO = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi", "Đỗ", "Ngô", "Dương", "Lý"]
DEM = ["Văn", "Thị", "Minh", "Đức", "Thu", "Quang", "Ngọc", "Gia", "Hải", "Thanh", "Bảo", "Tuấn"]
TEN = ["An", "Bình", "Châu", "Dũng", "Hà", "Huy", "Hương", "Khánh", "Long", "Mai", "Ngọc", "Phong", "Quân", "Trang"]
FORM_MIX = [("pdf", 0.6), ("pdf_quet", 0.2), ("docx", 0.2)]
EXPECT = {"moi": "Hợp lệ", "trung": "Trùng byte", "gan_trung": "Gần trùng", "khong_hop_le": "Không hợp lệ"}


def ascii_of(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def person(rnd: random.Random, cid: str, with_email: bool) -> dict:
    name = f"{rnd.choice(HO)} {rnd.choice(DEM)} {rnd.choice(TEN)}"
    email = f"{ascii_of(name).replace(' ', '.')}.{cid}@example.com".lower() if with_email else ""
    return {"ho_ten": name, "email": email, "sdt": f"09{rnd.randrange(10**7, 10**8)}"}


def contact_lines(p: dict) -> list[str]:
    return [f"Email: {p['email']} | Điện thoại: {p['sdt']}" if p["email"] else f"Điện thoại: {p['sdt']}"]


def make_docx(name: str, lines: list[str]) -> bytes:
    from cvagents.collector.synthetic import make_docx as mk
    return mk([escape(name)] + [escape(ln) for ln in lines])


def safe(s: str, n: int = 40) -> str:
    return re.sub(r"[^\w\- ]", "", ascii_of(s)).strip()[:n] or "JD"


# ---------------------------------------------------------------- đọc tệp khi chạy thử không có Docling
class PlumberExtractor:
    """Thay CVExtractorAgent.read bằng pdfplumber / đọc XML của DOCX (PDF quét → rỗng). Chỉ để chạy thử nhanh."""

    def __init__(self):
        from cvagents.extractor.agent import CVExtractorAgent
        self.agent = CVExtractorAgent()

    def read(self, path):
        from cvagents.extractor.models import Block, RawDocument
        data = Path(path).read_bytes()
        if data[:4] == b"PK\x03\x04":
            import zipfile, io
            xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
            text = "\n".join(re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<w:p>.*?</w:p>", xml))
        else:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        return RawDocument(blocks=[Block(text=l) for l in text.splitlines() if l.strip()]), None

    def process(self, raw, ocr):
        return self.agent.process(raw, ocr)


class Timed:
    """Bọc một tác tử, cộng dồn thời gian của từng phương thức được gọi."""

    def __init__(self, obj, clock: Counter, label: str):
        self._obj, self._clock, self._label = obj, clock, label

    def __getattr__(self, name):
        f = getattr(self._obj, name)
        if not callable(f):
            return f

        def wrap(*a, **k):
            t0 = time.perf_counter()
            try:
                return f(*a, **k)
            finally:
                self._clock[self._label] += time.perf_counter() - t0
        return wrap


def sim_smtp_mailer(outbox: Path):
    """Email_Agent với máy chủ SMTP giả lập: soạn thư thật, ghi .eml, trả trạng thái 'sent'."""
    from cvagents.mailer import EmailAgent
    from cvagents.mailer.agent import SendResult

    class SimSMTP(EmailAgent):
        def send(self, drafts, dry_run=False):
            return [SendResult(d.to, "sent", self._save(d).detail) for d in drafts]
    return SimSMTP(smtp=None, outbox=outbox, cong_ty="Công ty Thử nghiệm", nguoi_gui="Phòng Nhân sự")


# ---------------------------------------------------------------- dựng đợt tuyển dụng
def build_batch(args, test: pd.DataFrame, inbox: Path, fonts) -> tuple[list[dict], dict[str, dict]]:
    """→ (manifest các tệp thả vào inbox, thông tin JD). Mỗi phần tử manifest: tệp, đáp án collector, cid, nhãn …"""
    from cvagents.analysis.jd_parser import parse_jd
    rnd = random.Random(em.SEED)
    test = test.assign(y=(test.final_score > 0).astype(int))
    stats = test.groupby("job_id").y.agg(["sum", "count"])
    stats = stats[(stats["sum"] >= 3) & (stats["count"] - stats["sum"] >= 3)]
    stats = stats.assign(bal=np.minimum(stats["sum"], stats["count"] - stats["sum"])).sort_values(
        ["bal", "count"], ascending=False)
    used, jds, man = set(), {}, []
    for job_id in stats.index:
        if len(jds) == args.n_jd:
            break
        df = test[(test.job_id == job_id) & ~test.candidate_id.isin(used)].drop_duplicates("candidate_id")
        half = args.cv_moi_jd // 2
        pos, neg = df[df.y == 1], df[df.y == 0]
        k_pos = min(len(pos), max(half, args.cv_moi_jd - len(neg)))
        pick = pd.concat([pos.head(k_pos), neg.head(args.cv_moi_jd - k_pos)])
        if len(pick) < 6:
            continue
        jd_raw = str(pick.job_text.iloc[0])
        jd = parse_jd(em.jd_text(jd_raw))
        folder = f"JD{len(jds) + 1:02d} {safe(jd.tieu_de or str(job_id))}"
        jds[folder] = {"job_id": job_id, "raw": jd_raw, "tieu_de": jd.tieu_de, "so_cv": len(pick),
                       "so_phu_hop": int(pick.y.sum())}
        (inbox / folder).mkdir(parents=True, exist_ok=True)
        for _, r in pick.iterrows():
            used.add(r.candidate_id)
            cid = str(r.candidate_id)
            form = rnd.choices([f for f, _ in FORM_MIX], [w for _, w in FORM_MIX])[0]
            p = person(rnd, cid, with_email=rnd.random() >= args.ty_le_khong_email)
            lines = contact_lines(p) + em.cv_lines(str(r.resume_text))
            stem = f"CV_{ascii_of(p['ho_ten']).replace(' ', '_')}_{cid}"
            path = inbox / folder / f"{stem}{'.docx' if form == 'docx' else '.pdf'}"
            if form == "docx":
                path.write_bytes(make_docx(p["ho_ten"], lines))
            else:
                ep.make_pdf(lines, p["ho_ten"], path, fonts)
                if form == "pdf_quet":
                    tmp = path.with_suffix(".tmp.pdf")
                    path.rename(tmp)
                    ep.make_scan(tmp, path, seed=len(man))
                    tmp.unlink()
            man.append({"tep": str(path), "jd": folder, "cid": cid, "nhan": int(r.y), "dang": form,
                        "mong_doi": "moi", "goc": None, **p, "_lines": lines})
    if len(jds) < args.n_jd:
        print(f"Chỉ tìm được {len(jds)} JD đủ CV cân bằng")

    # -- tệp nhiễu có đáp án
    cvs = [m for m in man if m["mong_doi"] == "moi"]
    pdfs = [m for m in cvs if m["dang"] == "pdf"]
    rnd.shuffle(pdfs)
    n = max(1, round(args.ty_le_trung * len(cvs)))
    for m in pdfs[:n]:  # nộp hai lần cùng một tệp (bản sao byte, tên khác)
        dst = Path(m["tep"]).with_name(Path(m["tep"]).stem + "_nop_lan_2.pdf")
        shutil.copy(m["tep"], dst)
        man.append({**m, "tep": str(dst), "mong_doi": "trung", "goc": m["tep"]})
    for m in pdfs[n:2 * n]:  # cùng nội dung, xuất lại thành tệp khác (byte khác, văn bản giống)
        dst = Path(m["tep"]).with_name(Path(m["tep"]).stem + "_ban_moi.pdf")
        ep.make_pdf(m["_lines"], m["ho_ten"], dst, fonts)
        man.append({**m, "tep": str(dst), "mong_doi": "gan_trung", "goc": m["tep"]})
    first = sorted(jds)[0]
    bad = {
        "CV_hong.pdf": (lambda b: b[:len(b) // 2])(Path(pdfs[0]["tep"]).read_bytes()),                 # PDF tải dở (thiếu %%EOF)
        "CV_ngau_nhien.pdf": bytes(rnd.randrange(256) for _ in range(5000)),      # không phải PDF
        "CV_cu.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 6000,         # Word 97–2003
        "CV_rong.docx": b"",                                                     # tệp rỗng
        "Thu_xin_viec.txt": ("Kính gửi phòng nhân sự, " * 100).encode("utf-8"),  # định dạng không hỗ trợ
    }
    for name, data in bad.items():
        p = inbox / first / name
        p.write_bytes(data)
        man.append({"tep": str(p), "jd": first, "cid": None, "nhan": None, "dang": "nhieu", "mong_doi": "khong_hop_le",
                    "goc": None, "ho_ten": "", "email": "", "sdt": ""})
    # PDF đặt nhầm đuôi .docx: hợp lệ, collector đổi lại đuôi
    if len(pdfs) > 2 * n:
        m = pdfs[2 * n]
        dst = Path(m["tep"]).with_suffix(".docx")
        Path(m["tep"]).rename(dst)
        m["tep"], m["doi_duoi"] = str(dst), True
    # một tệp .zip chứa 2 CV của JD cuối: giải nén một cấp, mỗi CV là một hồ sơ hợp lệ
    last = sorted(jds)[-1]
    inz = [m for m in man if m["jd"] == last and m["mong_doi"] == "moi" and m["dang"] == "pdf" and not m.get("doi_duoi")
           and not any(o["goc"] == m["tep"] for o in man)][:2]
    if len(inz) == 2:
        from cvagents.collector.synthetic import make_zip
        zp = inbox / last / "Ho_so_gui_kem.zip"
        zp.write_bytes(make_zip({Path(m["tep"]).name: Path(m["tep"]).read_bytes() for m in inz}))
        for m in inz:
            Path(m["tep"]).unlink()
            m["tep"] = f"{zp}!{Path(m['tep']).name}"
    for m in man:
        m.pop("_lines", None)
    return man, jds


# ---------------------------------------------------------------- chạy và đối chiếu
def main() -> None:
    ap = argparse.ArgumentParser(description="Kiểm thử tích hợp bốn tác tử trên một đợt tuyển dụng mô phỏng")
    ap.add_argument("--lang", choices=["en", "vi"], required=True)
    ap.add_argument("--data-dir")
    ap.add_argument("--ft-model")
    ap.add_argument("--n-jd", type=int, default=5)
    ap.add_argument("--cv-moi-jd", type=int, default=24)
    ap.add_argument("--ty-le-trung", type=float, default=0.05, help="tỉ lệ CV được nộp trùng (và gần trùng)")
    ap.add_argument("--ty-le-khong-email", type=float, default=0.1, help="tỉ lệ CV không ghi email")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--max-seq-length", type=int, default=256)
    ap.add_argument("--embedder", choices=["st", "hashing"], default="st")
    ap.add_argument("--reader", choices=["docling", "pdfplumber"], default="docling")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    em.DATA_DIR = Path(args.data_dir) if args.data_dir else None
    logging.getLogger("cvagents").setLevel(logging.ERROR)  # nhật ký từng tệp của collector quá dài cho Colab
    args.ft_model = args.ft_model or str(HERE.parent / "models" / ("vn-embedding-cvjd-vi" if args.lang == "vi"
                                                                   else "vn-embedding-cvjd"))
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)  # mỗi lần chạy là một đợt mới: kho, nhật ký thư và outbox phải rỗng
    inbox, store_dir, outbox = out / "inbox", out / "kho", out / "outbox"
    inbox.mkdir(parents=True)

    from cvagents.analysis.agent import CVJDAnalysisAgent
    from cvagents.analysis.jd_parser import parse_jd
    from cvagents.collector.agent import CVCollectorAgent
    from cvagents.collector.models import DONE, DUP, FAILED, INVALID, NEAR_DUP, NEW
    from cvagents.collector.sources.folder import FolderSource
    from cvagents.collector.store import CollectorStore
    from cvagents.common.config import load_config
    from cvagents.extractor.agent import CVExtractorAgent
    from cvagents.mailer import ACK, INVITE, SentLog
    from cvagents.screening import PASS, REVIEW, STATUS_LABEL, screen_files, send_invites

    em.TEST_PAIRS = 0  # toàn bộ CV của phần kiểm tra để chọn được JD có đủ CV
    train, _, test = em.load_splits()
    man, jds = build_batch(args, test, inbox, ep._fonts())
    print(f"Đợt tuyển dụng: {len(jds)} JD, {sum(m['mong_doi'] == 'moi' for m in man)} CV, {len(man)} tệp vào inbox")

    clock: Counter = Counter()
    cfg = load_config()["analysis"]
    scorer = em.Scorer(SimpleNamespace(embedder=args.embedder, max_seq_length=args.max_seq_length), train)
    analysis = Timed(CVJDAnalysisAgent(embedder=scorer.embedder(args.ft_model)), clock, "analysis")
    extractor = Timed(CVExtractorAgent() if args.reader == "docling" else PlumberExtractor(), clock, "extractor")
    mailer = sim_smtp_mailer(outbox)
    sent_log = SentLog(out / "da_gui.json")
    store = CollectorStore(store_dir)
    collector = CVCollectorAgent(store, [FolderSource(inbox, ten="Thư mục inbox", settle=0)], mailer=mailer,
                                 sent_log=sent_log, ack=True)

    # -- 1. CV_Collector
    t0 = time.perf_counter()
    rep1 = collector.collect()
    clock["collector"] = time.perf_counter() - t0
    by_ref = {cv.tham_chieu: cv for cv in store.all()}
    for m in man:
        cv = by_ref.get(m["tep"])
        m["thuc_te"] = cv.trang_thai if cv else "khong_thay"
        m["ly_do"] = cv.ly_do if cv else ""
        m["key"] = cv.key if cv else ""
        m["ten_luu"] = cv.ten_tep if cv else ""
    code = {NEW: "moi", DUP: "trung", NEAR_DUP: "gan_trung", INVALID: "khong_hop_le"}
    rows = []
    for exp in EXPECT:
        g = [m for m in man if m["mong_doi"] == exp]
        hit = [m for m in g if code.get(m["thuc_te"]) == exp]
        rows.append({"loai_tep": EXPECT[exp], "so_tep": len(g), "nhan_dien_dung": len(hit),
                     "ty_le_dung": len(hit) / len(g) if g else float("nan"),
                     "sai_thanh": "; ".join(f"{k} ({v})" for k, v in Counter(m["thuc_te"] for m in g
                                                                             if m not in hit).items())})
    renamed = [m for m in man if m.get("doi_duoi")]
    tab_col = pd.DataFrame(rows)
    acks = len(list(outbox.glob(f"*_{ACK}_*.eml")))
    print("\n[1] CV_Collector\n" + tab_col.to_string(index=False))

    # -- 2. CV_Extractor + CV/JD_Analysis theo từng JD (đúng screen_files của ứng dụng)
    pending = {cv.key: cv for cv in collector.pending()}
    meta = {m["key"]: m for m in man if m["thuc_te"] == NEW}
    results = {}
    for folder, info in jds.items():
        jd = parse_jd(em.jd_text(info["raw"])).model_copy(update={"van_ban_goc": info["raw"]})
        files = [(cv.ten_tep, Path(cv.duong_dan)) for cv in pending.values() if cv.vi_tri == folder]
        res = screen_files(files, jd, extractor, lambda lang: (analysis, Path(args.ft_model).name),
                           on_done=lambda r: collector.mark(pending[r.key], r.profile, r.error))
        results[folder] = (jd, res)
        print(f"  {folder}: {len(res)} CV, Đạt {sum(r.status == PASS for r in res)}")
    left = len(collector.pending())

    # -- 3. Email_Agent
    t0 = time.perf_counter()
    for folder, (jd, res) in results.items():
        send_invites(res, mailer, jd.tieu_de or folder, folder, sent_log)
    clock["email"] = time.perf_counter() - t0

    # -- đối chiếu từng CV
    per_cv = []
    for folder, (jd, res) in results.items():
        for rank, r in enumerate(res, 1):
            m = meta.get(r.key, {})
            prof = r.profile
            c = prof.thong_tin_ca_nhan if prof else None
            digits = lambda s: re.sub(r"\D", "", s or "")[-9:]  # noqa: E731
            per_cv.append({
                "jd": folder, "hang": rank, "tep": r.file, "cid": m.get("cid"), "dang": m.get("dang"),
                "nhan": m.get("nhan"), "diem": round(r.report.diem_tong, 4) if r.report else None,
                "trang_thai": STATUS_LABEL[r.status], "thu": r.email, "loi": r.error,
                "ocr": bool(r.raw and r.raw.used_ocr),
                "ho_ten_dung": bool(c and c.ho_ten == m.get("ho_ten")),
                "email_dung": bool(c and (c.email or "").lower() == m.get("email", "")),
                "sdt_dung": bool(c and digits(c.so_dien_thoai) == digits(m.get("sdt"))),
                "co_email": bool(m.get("email")), "email_mong_doi": m.get("email"),
                "so_ky_nang": len(prof.ky_nang.chuan_hoa) if prof else 0,
                "_status": r.status, "_to": next((d for d in [r.profile.thong_tin_ca_nhan.email] if d), "")
                if prof else ""})
    df = pd.DataFrame(per_cv)

    # bảng Extractor theo dạng tệp
    ex_rows = []
    for form in ["pdf", "pdf_quet", "docx", None]:
        g = df if form is None else df[df.dang == form]
        if g.empty:
            continue
        has_mail = g[g.co_email]
        ex_rows.append({"dang_tep": form or "Tất cả", "so_cv": len(g), "ty_le_loi": (g.loi != "").mean(),
                        "ty_le_ocr": g.ocr.mean(), "ho_ten_dung": g.ho_ten_dung.mean(),
                        "email_dung": has_mail.email_dung.mean() if len(has_mail) else float("nan"),
                        "sdt_dung": g.sdt_dung.mean(), "so_ky_nang_tb": g.so_ky_nang.mean()})
    tab_ex = pd.DataFrame(ex_rows)
    print("\n[2] CV_Extractor\n" + tab_ex.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    # bảng Analysis theo JD
    an_rows = []
    for folder, g in list(df.groupby("jd")) + [("Tất cả", df)]:
        y = g.nhan.fillna(0).astype(int).to_numpy()
        pas = (g._status == PASS).to_numpy()
        rev = (g._status == REVIEW).to_numpy()
        tp = int((pas & (y == 1)).sum())
        prec = tp / pas.sum() if pas.sum() else float("nan")
        rec = tp / y.sum() if y.sum() else float("nan")
        row = {"jd": folder, "so_cv": len(g), "so_phu_hop": int(y.sum()), "dat": int(pas.sum()),
               "can_xem_xet": int(rev.sum()), "khong_dat": int(len(g) - pas.sum() - rev.sum()),
               "precision_dat": prec, "recall_dat": rec,
               "f1_dat": 2 * prec * rec / (prec + rec) if prec and rec and prec + rec else 0.0,
               "recall_dat_hoac_xem_xet": ((pas | rev) & (y == 1)).sum() / y.sum() if y.sum() else float("nan")}
        if folder != "Tất cả":
            from sklearn.metrics import average_precision_score
            top = g.sort_values("hang").head(args.top_k)
            row[f"p_at_{args.top_k}"] = top.nhan.fillna(0).mean()
            row["ap"] = average_precision_score(y, g.diem.fillna(0)) if 0 < y.sum() < len(y) else float("nan")
        an_rows.append(row)
    tab_an = pd.DataFrame(an_rows)
    k = f"p_at_{args.top_k}"
    tab_an.loc[tab_an.jd == "Tất cả", [k, "ap"]] = tab_an.loc[tab_an.jd != "Tất cả", [k, "ap"]].mean().to_numpy()
    print(f"\n[3] CV/JD_Analysis (τS = {cfg['pass_threshold']}, δ = {cfg['review_margin']})\n"
          + tab_an.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    # bảng Email_Agent
    invites = list(outbox.glob(f"*_{INVITE}_*.eml"))
    pas = df[df._status == PASS]
    expect_inv = pas[pas.co_email]
    right_to = int((expect_inv._to.str.lower() == expect_inv.email_mong_doi).sum())
    new_valid = [m for m in man if m["thuc_te"] == NEW and m.get("email") and m["dang"] != "pdf_quet"]

    # -- 4. chạy lại: quét lại, nộp lại, gửi lại
    rep2 = collector.collect()  # cùng thư mục, không có tệp mới
    again = out / "nop_lai"
    for m in man:
        if m["thuc_te"] == NEW and "!" not in m["tep"]:
            dst = again / m["jd"] / Path(m["tep"]).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(m["tep"], dst)
    n_again = sum(1 for p in again.rglob("*") if p.is_file())
    rep3 = collector.collect([FolderSource(again, ten="Nộp lại", settle=0)])
    rejected = sum(s.trung + s.gan_trung for s in rep3.nguon)
    n_eml = len(list(outbox.glob("*.eml")))
    for folder, (jd, res) in results.items():
        for r in res:
            r.email = ""
        send_invites(res, mailer, jd.tieu_de or folder, folder, sent_log)
    resent = len(list(outbox.glob("*.eml"))) - n_eml
    sent_before = sum(r.email == "sent_before" for _, res in results.values() for r in res)

    em_rows = [
        {"kiem_tra": "Thư xác nhận đã nhận hồ sơ (CV hợp lệ có email đọc được từ lớp văn bản)",
         "mong_doi": len(new_valid), "thuc_te": acks},
        {"kiem_tra": "Thư mời phỏng vấn cho CV Đạt có email", "mong_doi": len(expect_inv), "thuc_te": len(invites)},
        {"kiem_tra": "… gửi đúng địa chỉ email của ứng viên", "mong_doi": len(expect_inv), "thuc_te": right_to},
        {"kiem_tra": "CV Đạt không trích được email (cảnh báo, không gửi)", "mong_doi": int((~pas.co_email).sum()),
         "thuc_te": int((pas.thu == "no_email").sum())},
        {"kiem_tra": "Thư gửi cho CV Cần xem xét / Không đạt", "mong_doi": 0,
         "thuc_te": int(df[(df._status != PASS) & df.thu.isin(["sent", "saved"])].shape[0])},
    ]
    re_rows = [
        {"kiem_tra": "Quét lại thư mục inbox: tệp được đọc lại", "mong_doi": 0, "thuc_te": sum(s.thu for s in rep2.nguon)},
        {"kiem_tra": "Nộp lại toàn bộ tệp hợp lệ: số tệp bị loại vì trùng", "mong_doi": n_again, "thuc_te": rejected},
        {"kiem_tra": "Đánh giá lại và gửi thư lần hai: thư gửi thêm", "mong_doi": 0, "thuc_te": resent},
        {"kiem_tra": "… CV được báo 'đã gửi thư mời trước đó'", "mong_doi": len(invites), "thuc_te": sent_before},
        {"kiem_tra": "CV hợp lệ còn chờ xử lý sau đợt (chưa chuyển giao)", "mong_doi": 0, "thuc_te": left},
        {"kiem_tra": "CV lỗi trong Extractor / Analysis", "mong_doi": 0, "thuc_te": int((df.loi != "").sum())},
    ]
    tab_em, tab_re = pd.DataFrame(em_rows), pd.DataFrame(re_rows)
    print("\n[4] Email_Agent\n" + tab_em.to_string(index=False))
    print("\n[5] Chạy lại / tính bền vững\n" + tab_re.to_string(index=False))

    n_cv = len(df)
    tab_t = pd.DataFrame([
        {"buoc": "CV_Collector (thu thập, kiểm tra, loại trùng, thư xác nhận)", "tong_giay": clock["collector"],
         "giay_moi_tep": clock["collector"] / len(man)},
        {"buoc": "CV_Extractor (đọc tệp, OCR, trích xuất)", "tong_giay": clock["extractor"],
         "giay_moi_tep": clock["extractor"] / n_cv},
        {"buoc": "CV/JD_Analysis (đối sánh, chấm điểm)", "tong_giay": clock["analysis"],
         "giay_moi_tep": clock["analysis"] / n_cv},
        {"buoc": "Email_Agent (soạn, gửi thư mời)", "tong_giay": clock["email"],
         "giay_moi_tep": clock["email"] / max(1, len(invites))},
    ])
    tab_t.loc[len(tab_t)] = {"buoc": "Toàn bộ đợt", "tong_giay": tab_t.tong_giay.sum(),
                             "giay_moi_tep": tab_t.tong_giay.sum() / n_cv}
    print("\n[6] Thời gian\n" + tab_t.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    db = Counter(cv.trang_thai for cv in store.all())
    for name, t in [("collector", tab_col), ("extractor", tab_ex), ("analysis", tab_an), ("email", tab_em),
                    ("chay_lai", tab_re), ("thoi_gian", tab_t)]:
        t.to_csv(out / f"bang_{name}.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    df.drop(columns=["_status", "_to"]).to_csv(out / "tung_cv.csv", index=False, encoding="utf-8-sig")
    summary = {
        "lang": args.lang, "ft_model": args.ft_model, "reader": args.reader, "nguong": cfg["pass_threshold"],
        "bien_xem_xet": cfg["review_margin"], "trong_so": {k: v for k, v in cfg.items() if k.startswith("w_")},
        "so_jd": len(jds), "jd": {k: {kk: vv for kk, vv in v.items() if kk != "raw"} for k, v in jds.items()},
        "so_tep_inbox": len(man), "so_cv_hop_le": n_cv, "doi_duoi": [m["ten_luu"] for m in renamed],
        "phan_bo_dang": dict(Counter(m["dang"] for m in man if m["mong_doi"] == "moi")),
        "so_cung_ung_vien": sum(1 for cv in store.all() if cv.trang_thai in (DONE, FAILED) and cv.trung_voi),
        "trang_thai_kho": dict(db), "lan_chay": [rep1.id, rep2.id, rep3.id],
        "bang": {n: t.to_dict(orient="records") for n, t in [("collector", tab_col), ("extractor", tab_ex),
                                                             ("analysis", tab_an), ("email", tab_em),
                                                             ("chay_lai", tab_re), ("thoi_gian", tab_t)]},
        "tep": [{k: v for k, v in m.items()} for m in man],
    }
    (out / "ket_qua_e2e.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                          encoding="utf-8")
    ok = all(r["mong_doi"] == r["thuc_te"] for r in em_rows + re_rows)
    print(f"\nKho sau đợt: {dict(db)}")
    print(f"{'Mọi' if ok else 'KHÔNG phải mọi'} kiểm tra của Email_Agent / chạy lại khớp kỳ vọng."
          f" Đã lưu {out / 'ket_qua_e2e.json'}")


if __name__ == "__main__":
    main()
