import base64
import email.message
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import pytest

from cvagents.collector import (
    DONE, DUP, FAILED, FETCH_ERR, INVALID, NEAR_DUP, NEW, CollectorStore, CVCollectorAgent, Incoming,
    run_periodically,
)
from cvagents.collector import fingerprint as fp
from cvagents.collector.mocksite import MockJobSite
from cvagents.collector.sources import (
    ApiSource, DatasetSource, FolderSource, GoogleDriveSource, ImapSource, JobBoardSource, SourceState,
    job_from_subject,
)
from cvagents.collector.sources.net import HttpClient
from cvagents.collector.synthetic import cv_text, make_docx, make_image, make_pdf, make_zip
from cvagents.collector.validate import sniff, validate
from cvagents.common.schemas import Contact, CVProfile
from cvagents.mailer import ACK, EmailAgent, SentLog


@pytest.fixture
def agent(tmp_path):
    a = CVCollectorAgent(CollectorStore(tmp_path / "inbox"), ack=False)
    yield a
    a.store.close()


def inc(name, data, **kw):
    return Incoming(name, data, kw.pop("nguon", "tai_len"), **kw)


# ---- kiểm tra định dạng
def test_sniff_by_content_not_extension():
    assert sniff(make_pdf(["a"])) == "pdf"
    assert sniff(make_docx(["a"])) == "docx"
    assert sniff(make_image(1)) == "png"
    assert sniff(make_image(1, fmt="JPEG")) == "jpg"
    assert sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100) == "doc"
    assert sniff(make_zip({"a.txt": b"x"})) == "zip"
    assert sniff(b"hello") is None


def test_validate_rejects_bad_files():
    pdf = make_pdf(cv_text(1))
    assert validate("cv.pdf", pdf).ok
    assert "EOF" in validate("cv.pdf", pdf[:-40]).reason                       # tải chưa hết
    enc = pdf.replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 9 0 R")
    assert "mật khẩu" in validate("cv.pdf", enc).reason
    assert ".doc cũ" in validate("cv.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 2000).reason
    assert "quá nhỏ" in validate("cv.pdf", b"%PDF-1.4 %%EOF").reason
    assert "Ảnh quá nhỏ" in validate("cv.png", make_image(1, size=(200, 120)), {**_cfg(), "kich_thuoc_min_byte": 0}).reason
    assert "hỏng" in validate("cv.png", make_image(1)[:2000]).reason
    assert not validate("cv.txt", "Họ tên: Nguyễn Văn An\n".encode() * 100).ok
    many = make_pdf(cv_text(1)).replace(b"/Type /Page ", b"/Type /Page /Type /Page " * 6)
    assert "trang" in validate("cv.pdf", many, {**_cfg(), "so_trang_max": 3}).reason


def test_validate_renames_wrong_extension(agent):
    v = validate("cv.pdf", make_docx(cv_text(1)))
    assert v.ok and v.kind == "docx" and "đổi thành .docx" in v.reason
    cv = agent.ingest(inc("cv.pdf", make_docx(cv_text(1))))[0]
    assert cv.trang_thai == NEW and cv.ten_tep == "cv.docx" and cv.duong_dan.endswith(".docx")


def _cfg():
    from cvagents.common.config import load_config
    return load_config()["collector"]


def test_zip_bomb_rejected(agent):
    bomb = make_zip({"a.pdf": b"\0" * (2 * 2**20)})
    cv = CVCollectorAgent(agent.store, cfg={"zip_max_mb": 1}, ack=False).ingest(inc("hs.zip", bomb))
    assert len(cv) == 1 and cv[0].trang_thai == INVALID and "zip bomb" in cv[0].ly_do


# ---- loại trùng
def test_job_from_subject():
    assert job_from_subject("[Ứng tuyển] Backend Developer - Nguyễn Văn An") == "Backend Developer"
    assert job_from_subject("Fwd: Ứng tuyển vị trí Data Analyst") == "Data Analyst"
    assert job_from_subject("Application for Java Developer at ABC") == "Java Developer"
    assert job_from_subject("CV ứng tuyển vị trí: Tester | Trần B") == "Tester"
    assert job_from_subject("Hello") is None and job_from_subject(None) is None


def test_simhash_and_dhash_separate_near_from_different():
    a = fp.simhash(" ".join(cv_text(1)))
    assert fp.hamming(a, fp.simhash(" ".join(cv_text(1)) + " ")) == 0
    assert fp.hamming(a, fp.simhash(" ".join(cv_text(2)))) > 10
    assert fp.hamming(fp.dhash(make_image(1)), fp.dhash(make_image(1, fmt="JPEG", quality=50))) <= 4
    assert fp.hamming(fp.dhash(make_image(1)), fp.dhash(make_image(2))) > 10


def test_ingest_three_levels_of_dedupe(agent):
    pdf = make_pdf(cv_text(1))
    first = agent.ingest(inc("an.pdf", pdf))[0]
    assert first.trang_thai == NEW and first.email == "uv1@example.com" and first.loai_van_tay == "text"
    dup = agent.ingest(inc("an (1).pdf", pdf, nguon="email"))[0]
    assert dup.trang_thai == DUP and dup.trung_voi == first.key and dup.duong_dan is None
    near = agent.ingest(inc("an.docx", make_docx(cv_text(1))))[0]          # cùng nội dung, khác định dạng
    assert near.trang_thai == NEAR_DUP and near.trung_voi == first.key
    img = agent.ingest(inc("scan.png", make_image(5)))[0]
    assert agent.ingest(inc("scan.jpg", make_image(5, fmt="JPEG", quality=60)))[0].trang_thai == NEAR_DUP
    # CV khác hẳn nhưng cùng email: vẫn nhận, chỉ đánh dấu
    other = agent.ingest(inc("an_2026.pdf", make_pdf(cv_text(9, email="uv1@example.com"))))[0]
    assert other.trang_thai == NEW and other.trung_voi == first.key and "Cùng ứng viên" in other.ly_do
    assert [c.ten_tep for c in agent.pending()] == ["an.pdf", "scan.png", "an_2026.pdf"]
    assert img.so_trang == 1 and len(list(agent.store.files.iterdir())) == 3


def test_zip_attachment_is_expanded(agent):
    z = make_zip({"hs/a.pdf": make_pdf(cv_text(1)), "hs/b.docx": make_docx(cv_text(2)), "__MACOSX/._a.pdf": b"x",
                  "hs/ghi_chu.txt": b"x" * 2000})
    out = agent.ingest(inc("ho_so.zip", z, tham_chieu="msg-1"))
    assert [(c.ten_tep, c.trang_thai) for c in out] == [("a.pdf", NEW), ("b.docx", NEW), ("ghi_chu.txt", INVALID)]
    assert out[0].tham_chieu == "msg-1!hs/a.pdf"


def test_mark_after_extraction(agent):
    cv = agent.ingest(inc("a.pdf", make_pdf(cv_text(1))))[0]
    agent.mark(cv, CVProfile(thong_tin_ca_nhan=Contact(email="An@X.vn", so_dien_thoai="+84912345678")))
    got = agent.store.get(cv.id)
    assert got.trang_thai == DONE and got.email == "an@x.vn" and got.sdt == "+84912345678"
    bad = agent.ingest(inc("b.pdf", make_pdf(cv_text(2))))[0]
    agent.mark(bad, error="OSError: x")
    assert agent.store.get(bad.id).trang_thai == FAILED and not agent.pending()
    # bản gốc đã xử lý vẫn dùng để loại trùng
    assert agent.ingest(inc("a.pdf", make_pdf(cv_text(1))))[0].trang_thai == DUP


def test_ack_email_once_per_cv(tmp_path):
    mailer = EmailAgent(outbox=tmp_path / "out", cong_ty="ABC")
    sent = []
    mailer.send = lambda drafts, dry_run=False: [sent.append(d) or type("R", (), {"status": "sent"})() for d in drafts]
    a = CVCollectorAgent(CollectorStore(tmp_path / "in"), mailer=mailer, sent_log=SentLog(tmp_path / "log.json"),
                         ack=True)
    a.ingest(inc("a.pdf", make_pdf(cv_text(1)), nguoi_gui="an@x.vn", ten_nguoi_gui="An", vi_tri="Tester"))
    a.ingest(inc("a.pdf", make_pdf(cv_text(1)), nguoi_gui="an@x.vn"))  # gửi lại: trùng, không gửi thư
    assert len(sent) == 1 and sent[0].loai == ACK and "Tester" in sent[0].subject and sent[0].to == "an@x.vn"


def test_purge_removes_old_files(agent):
    cv = agent.ingest(inc("a.pdf", make_pdf(cv_text(1))))[0]
    assert agent.store.purge(30, now=datetime.now() + timedelta(days=10)) == 0
    assert agent.store.purge(30, now=datetime.now() + timedelta(days=31)) == 1
    assert not os.path.exists(cv.duong_dan) and agent.store.all() == []


# ---- nguồn
def test_folder_source_incremental_and_job_from_subfolder(agent, tmp_path):
    d = tmp_path / "drop"
    (d / "Backend Developer").mkdir(parents=True)
    (d / "Backend Developer" / "an.pdf").write_bytes(make_pdf(cv_text(1)))
    (d / "~$an.docx").write_bytes(b"lock")
    (d / "binh.docx").write_bytes(make_docx(cv_text(2)))
    src = FolderSource(d, settle=0)
    r = agent.collect([src])
    s = r.nguon[0]
    assert (s.thu, s.hop_le) == (2, 2) and {c.vi_tri for c in r.moi} == {"Backend Developer", None}
    assert agent.collect([src]).nguon[0].thu == 0               # lần sau: không đọc lại
    (d / "chi.pdf").write_bytes(make_pdf(cv_text(3)))
    assert agent.collect([src]).nguon[0].hop_le == 1
    fresh = FolderSource(d, settle=60)                             # tệp vừa ghi: đợi ổn định
    (d / "dung.pdf").write_bytes(make_pdf(cv_text(4)))
    assert agent.collect([fresh]).nguon[0].thu == 0
    assert len(agent.store.runs()) == 4


class FakeImap:
    def __init__(self, messages: dict[int, bytes], password="ok", validity="7"):
        self.messages, self.password, self.validity = messages, password, validity
        self.fetched = []

    def login(self, user, password):
        import imaplib
        if password != self.password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, folder, readonly=False):
        assert readonly
        return "OK", [b"3"]

    def response(self, code):
        return code, [self.validity.encode()]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            lo = int(args[1].split()[1].split(":")[0])
            ids = [u for u in self.messages if u >= lo] or [max(self.messages)]
            return "OK", [" ".join(map(str, ids)).encode()]
        self.fetched.append(int(args[0]))
        return "OK", [(b"1 (RFC822 {n}", self.messages[int(args[0])]), b")"]

    def logout(self):
        pass


def mail(subject, frm, files: dict[str, bytes]) -> bytes:
    m = email.message.EmailMessage()
    m["Subject"], m["From"], m["Message-ID"] = subject, frm, f"<{abs(hash(subject))}@x>"
    m["Date"] = "Mon, 26 Oct 2026 09:00:00 +0700"
    m.set_content("Em gửi CV ạ")
    for n, d in files.items():
        maintype, subtype = ("application", "pdf") if n.endswith(".pdf") else ("application", "zip")
        m.add_attachment(d, maintype=maintype, subtype=subtype, filename=n)
    return bytes(m)


def test_imap_source_reads_attachments_incrementally(agent):
    msgs = {11: mail("[Ứng tuyển] Data Analyst - Lê C", "Lê C <c@x.vn>", {"CV_LeC.pdf": make_pdf(cv_text(1))}),
            12: mail("Hỏi lịch phỏng vấn", "d@x.vn", {}),
            13: mail("Ứng tuyển vị trí Tester", "e@x.vn", {"hs.zip": make_zip({"e.pdf": make_pdf(cv_text(2))})})}
    conn = FakeImap(msgs)
    src = ImapSource("hr@x.vn", "ok", conn_factory=lambda: conn)
    r = agent.collect([src])
    assert r.nguon[0].hop_le == 2
    a, e = r.moi
    assert (a.nguoi_gui, a.ten_nguoi_gui, a.vi_tri) == ("c@x.vn", "Lê C", "Data Analyst")
    assert e.vi_tri == "Tester" and e.tham_chieu.endswith("!e.pdf")
    msgs[14] = mail("Apply for Tester", "f@x.vn", {"f.pdf": make_pdf(cv_text(3))})
    conn.fetched = []
    assert agent.collect([src]).nguon[0].hop_le == 1 and conn.fetched == [14]
    bad = ImapSource("hr@x.vn", "sai", conn_factory=lambda: FakeImap(msgs))
    s = agent.collect([bad]).nguon[0]
    assert "AUTHENTICATIONFAILED" in s.loi and s.thu == 0


@pytest.fixture
def site(tmp_path):
    d = tmp_path / "site"
    (d / "Backend Developer").mkdir(parents=True)
    (d / "Backend Developer" / "an.pdf").write_bytes(make_pdf(cv_text(1)))
    (d / "Backend Developer" / "binh.docx").write_bytes(make_docx(cv_text(2)))
    (d / "Tester").mkdir()
    (d / "Tester" / "chi.pdf").write_bytes(make_pdf(cv_text(3)))
    (d / "Tester" / "an_lai.pdf").write_bytes(make_pdf(cv_text(1)))   # cùng tệp nộp hai tin
    (d / "Tester" / "hong.pdf").write_bytes(make_pdf(cv_text(4)))
    (d / "bi_mat.pdf").write_bytes(make_pdf(cv_text(5)))
    (d / "ung_vien.csv").write_text("ten_tep,ho_ten,email\nan.pdf,Nguyễn Văn An,an@x.vn\n", encoding="utf-8")
    m = MockJobSite(d, page_size=2, hidden=["bi_mat.pdf"], broken=["Tester/hong.pdf"])
    srv, url = m.serve()
    yield m, url
    srv.shutdown()


def test_job_board_crawler_respects_robots_and_is_incremental(agent, site):
    m, url = site
    src = JobBoardSource(url, client=HttpClient(delay=0))
    s = agent.collect([src]).nguon[0]
    assert (s.thu, s.hop_le, s.trung, s.loi_tai) == (5, 3, 1, 1)
    assert not any(p.startswith("/quan-tri") for p in m.requests)        # robots.txt: Disallow /quan-tri/
    assert "/ho-so/bi_mat.pdf" not in m.requests
    an = next(c for c in agent.store.all() if c.ten_tep == "an.pdf")
    assert (an.vi_tri, an.ten_nguoi_gui, an.nguoi_gui) == ("Backend Developer", "Nguyễn Văn An", "an@x.vn")
    m.requests.clear()
    s2 = agent.collect([src]).nguon[0]
    assert s2.thu == 1 and s2.loi_tai == 1 and not any(p.startswith("/ho-so/") and "hong" not in p for p in m.requests)


def test_crawler_waits_between_requests(site):
    m, url = site
    waits = []
    c = HttpClient(delay=0.5, sleep=waits.append)
    c.get(url)
    c.get(url + "viec-lam/0")
    assert waits and 0 < waits[0] <= 0.5


def test_api_source_paginates(agent, site):
    m, url = site
    s = agent.collect([ApiSource(url + "api/ung-tuyen")]).nguon[0]
    assert (s.thu, s.hop_le, s.trung, s.loi_tai) == (5, 3, 1, 1)
    assert sum(p == "/api/ung-tuyen" for p in m.requests) == 3       # 5 hồ sơ, 2 hồ sơ / trang
    assert agent.collect([ApiSource(url + "api/ung-tuyen")]).nguon[0].thu == 1   # chỉ thử lại tệp lỗi


def test_api_source_base64_and_field_map(agent, tmp_path):
    class Client:
        headers = {}

        def get(self, url):
            body = {"data": {"rows": [{"uid": 1, "cv": base64.b64encode(make_pdf(cv_text(1))).decode(),
                                       "file": "a.pdf", "job": {"title": "QA"}},
                                      {"uid": 2, "cv": "!!!", "file": "b.pdf"}]}}
            return type("R", (), {"body": json.dumps(body).encode()})()

    src = ApiSource("http://x/api", fields={"items": "data.rows", "id": "uid", "cv_base64": "cv", "ten_tep": "file",
                                           "vi_tri": "job.title"}, client=Client())
    r = agent.collect([src])
    assert r.moi[0].vi_tri == "QA" and r.nguon[0].loi_tai == 1


def test_dataset_zip_with_csv(agent, tmp_path):
    z = tmp_path / "resume.zip"
    z.write_bytes(make_zip({"data/INFORMATION-TECHNOLOGY/101.pdf": make_pdf(cv_text(1)),
                            "data/HR/202.pdf": make_pdf(cv_text(2)),
                            "Resume.csv": "ID,Category\n101,IT\n".encode()}))
    r = agent.collect([DatasetSource(z)])
    assert sorted(c.vi_tri for c in r.moi) == ["HR", "IT"]


class FakeDrive:
    def __init__(self):
        self.calls = []

    def files(self):
        return self

    def list(self, q, pageToken=None, **kw):
        if "'root'" in q:
            files = [{"id": "f1", "name": "an.pdf", "mimeType": "application/pdf", "modifiedTime": "2026-10-01T00:00:00Z",
                      "owners": [{"displayName": "An", "emailAddress": "an@x.vn"}]},
                     {"id": "d1", "name": "Tester", "mimeType": "application/vnd.google-apps.folder"}]
        else:
            files = [{"id": "g1", "name": "Binh CV", "mimeType": "application/vnd.google-apps.document",
                      "modifiedTime": "2026-10-02T00:00:00Z"}]
        return _Exec({"files": files})

    def get_media(self, fileId):
        self.calls.append(("get", fileId))
        return _Exec(make_pdf(cv_text(1)))

    def export_media(self, fileId, mimeType):
        self.calls.append(("export", fileId))
        return _Exec(make_docx(cv_text(2)))


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


def test_google_drive_source(agent):
    drive = FakeDrive()
    r = agent.collect([GoogleDriveSource("root", service=drive)])
    assert [(c.ten_tep, c.vi_tri, c.nguoi_gui) for c in r.moi] == [("an.pdf", None, "an@x.vn"),
                                                                  ("Binh CV.docx", "Tester", None)]
    assert agent.collect([GoogleDriveSource("root", service=drive)]).nguon[0].thu == 0


def test_one_failing_source_does_not_stop_others(agent, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.pdf").write_bytes(make_pdf(cv_text(1)))
    r = agent.collect([FolderSource(tmp_path / "khong_co"), FolderSource(d, settle=0)])
    assert "Không có thư mục" in r.nguon[0].loi and r.nguon[1].hop_le == 1
    t = r.total()
    assert t.thu == 1 and t.ty_le_hop_le == 1.0


def test_fetch_errors_are_counted(agent):
    class Src:
        nguon, ten = "api", "x"

        def fetch(self, state: SourceState):
            yield Incoming("a.pdf", b"", "api", error="timeout")
            yield Incoming("b.pdf", make_pdf(cv_text(1)), "api")

    s = agent.collect([Src()]).nguon[0]
    assert (s.thu, s.loi_tai, s.hop_le, s.ty_le_thanh_cong) == (2, 1, 1, 0.5)
    assert agent.store.all(FETCH_ERR)[0].ly_do == "timeout"


def test_scheduler_runs_until_stopped(agent, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    seen = []
    n = run_periodically(agent.__class__(agent.store, [FolderSource(d, settle=0)], ack=False), interval=0,
                         max_runs=3, on_run=lambda r: seen.append(r.id))
    assert n == 3 and len(seen) == 3
    stop = threading.Event()
    th = threading.Thread(target=run_periodically, args=(agent,), kwargs={"interval": 10, "stop": stop})
    th.start()
    time.sleep(0.2)
    stop.set()
    th.join(2)
    assert not th.is_alive()


def test_screen_files_hands_over_to_extractor(agent, tmp_path):
    from cvagents.analysis import CVJDAnalysisAgent
    from cvagents.analysis.embedder import HashingEmbedder
    from cvagents.extractor.models import Block, RawDocument
    from cvagents.screening import ERROR, screen_files

    good = agent.ingest(inc("a.pdf", make_pdf(cv_text(1))))[0]
    bad = agent.ingest(inc("b.pdf", make_pdf(cv_text(2))))[0]

    class FakeExtractor:
        def read(self, path):
            if str(path) == bad.duong_dan:
                raise OSError("hỏng")
            return RawDocument([Block("Nguyễn Văn An"), Block("an@x.vn"), Block("KỸ NĂNG"), Block("Python, SQL")]), []

        def process(self, raw, ocr):
            from cvagents.extractor import CVExtractorAgent
            return CVExtractorAgent().process(raw, ocr)

    an = CVJDAnalysisAgent(embedder=HashingEmbedder())
    by_key = {c.key: c for c in agent.pending()}
    res = screen_files([(c.ten_tep, c.duong_dan) for c in agent.pending()], "Dev\nYêu cầu:\n- Python", FakeExtractor(),
                       lambda lang: (an, "hashing"), on_done=lambda r: agent.mark(by_key[r.key], r.profile, r.error))
    assert [r.file for r in res] == ["a.pdf", "b.pdf"] and res[1].status == ERROR
    assert agent.store.get(good.id).trang_thai == DONE and agent.store.get(good.id).email == "an@x.vn"
    assert agent.store.get(bad.id).trang_thai == FAILED


def test_collector_logs_to_file(agent, tmp_path):
    from cvagents.collector import setup_logging
    p = setup_logging(tmp_path / "logs" / "c.log")
    agent.ingest(inc("x.txt", b"x" * 2000))
    for h in logging.getLogger("cvagents").handlers:
        h.flush()
    assert "khong_hop_le x.txt" in p.read_text(encoding="utf-8")
