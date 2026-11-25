"""Crawler CV: ngưỡng (độ sâu, số tệp, lỗi liên tiếp, thử lại), phát hiện thay đổi trang và lưu phiên bản,
danh sách URL theo dõi, nguồn máy tìm kiếm, lập lịch theo giờ cố định."""
import email.message
import urllib.error
from datetime import datetime, timedelta

import pytest

from cvagents.collector import CollectorStore, CVCollectorAgent, parse_times
from cvagents.collector.changes import change_ratio, diff_lines, page_text, track_page
from cvagents.collector.mocksite import MockJobSite
from cvagents.collector.models import FETCH_ERR, PAGE_CHANGED, PAGE_NEW, PAGE_SAME
from cvagents.collector.scheduler import next_slot, previous_slot
from cvagents.collector.sources import JobBoardSource, SearchSource, UrlListSource
from cvagents.collector.sources.net import HttpClient, Response
from cvagents.collector.synthetic import cv_text, make_docx, make_pdf


@pytest.fixture
def agent(tmp_path):
    a = CVCollectorAgent(CollectorStore(tmp_path / "inbox"), ack=False)
    yield a
    a.store.close()


@pytest.fixture
def site(tmp_path):
    d = tmp_path / "site"
    (d / "Backend Developer").mkdir(parents=True)
    (d / "Backend Developer" / "an.pdf").write_bytes(make_pdf(cv_text(1)))
    (d / "Backend Developer" / "binh.docx").write_bytes(make_docx(cv_text(2)))
    (d / "Tester").mkdir()
    (d / "Tester" / "chi.pdf").write_bytes(make_pdf(cv_text(3)))
    (d / "Tester" / "hong.pdf").write_bytes(make_pdf(cv_text(4)))
    m = MockJobSite(d, broken=["Tester/hong.pdf"])
    srv, url = m.serve()
    yield m, url, d
    srv.shutdown()


def fast(**kw) -> HttpClient:
    return HttpClient(delay=0, retries=0, **kw)


def url_of(store: CollectorStore, job: str) -> str:
    """URL trang tin tuyển dụng của vị trí `job` (theo <h1> trong phiên bản đã lưu)."""
    for p in store.pages_seen():
        v = store.last_page_version(p["url"])
        if v and v.text().splitlines()[0] == job:
            return p["url"]
    raise KeyError(job)


# ---- phát hiện thay đổi
def test_page_text_ignores_markup_and_scripts():
    html = ("<html><head><title>CV</title><script>var t = 123;</script><style>p{}</style></head>"
            "<body><h1>Nguyễn  Văn An</h1><p>Kinh nghiệm: <b>3 năm</b></p></body></html>")
    assert page_text(html) == "CV\nNguyễn Văn An\nKinh nghiệm: 3 năm"
    assert page_text(html.replace("123", "456")) == page_text(html)   # đổi script không phải thay đổi nội dung


def test_change_ratio():
    a = "\n".join(f"dòng {i}" for i in range(20))
    assert change_ratio(a, a) == 0.0
    assert change_ratio(a, "khác hẳn") == 1.0
    one = a.replace("dòng 7", "dòng bảy")
    assert 0 < change_ratio(a, one) < 0.1
    assert "-dòng 7" in diff_lines(a, one) and "+dòng bảy" in diff_lines(a, one)


def test_track_page_threshold_and_cumulative_changes(agent):
    s, base = agent.store, "\n".join(f"<p>dòng {i}</p>" for i in range(20))
    assert track_page(s, "http://x/", "t", base.encode(), 0.1)[0] == PAGE_NEW
    assert track_page(s, "http://x/", "t", base.encode(), 0.1) == (PAGE_SAME, 0.0)
    small = base.replace("dòng 1<", "dòng một<")
    st, r = track_page(s, "http://x/", "t", small.encode(), 0.1)
    assert st == PAGE_SAME and 0 < r < 0.1                   # dưới ngưỡng: không lưu
    more = small.replace("dòng 2<", "dòng hai<").replace("dòng 3<", "dòng ba<")
    assert track_page(s, "http://x/", "t", more.encode(), 0.1)[0] == PAGE_CHANGED   # cộng dồn so với bản đã lưu
    vs = s.page_versions("http://x/")
    assert [v.phien_ban for v in vs] == [1, 2] and "dòng hai" in vs[1].text() and vs[0].html() == base.encode()
    p = s.pages_seen()[0]
    assert (p["so_lan"], p["so_phien_ban"]) == (4, 2)


def test_crawler_saves_pages_and_rechecks_files_when_page_changes(agent, site):
    m, url, d = site
    src = JobBoardSource(url, client=fast())
    s1 = agent.collect([src]).nguon[0]
    assert (s1.trang, s1.trang_moi, s1.trang_thay_doi) == (3, 3, 0)    # trang chủ + 2 tin tuyển dụng
    assert (s1.thu, s1.hop_le, s1.loi_tai) == (4, 3, 1)
    tester = url_of(agent.store, "Tester")

    m.requests.clear()
    s2 = agent.collect([src]).nguon[0]
    assert (s2.trang, s2.trang_moi, s2.trang_thay_doi) == (3, 0, 0)
    assert (s2.thu, s2.loi_tai) == (1, 1)                                # chỉ thử lại liên kết hỏng
    assert "/ho-so/Tester/chi.pdf" not in m.requests

    (d / "Tester" / "dung.pdf").write_bytes(make_pdf(cv_text(6)))        # tin Tester có thêm ứng viên
    m.requests.clear()
    s3 = agent.collect([src]).nguon[0]
    assert (s3.trang_moi, s3.trang_thay_doi) == (0, 1)
    assert (s3.thu, s3.hop_le, s3.loi_tai) == (2, 1, 1)
    assert "/ho-so/Tester/chi.pdf" in m.requests                        # tệp cũ được kiểm tra lại, không đổi nên bỏ qua
    assert [v.phien_ban for v in agent.store.page_versions(tester)] == [1, 2]
    assert "dung" in agent.store.page_versions(tester)[1].text()


def test_new_file_is_fetched_even_if_page_change_is_below_threshold(agent, site):
    m, url, d = site
    src = JobBoardSource(url, client=fast(), threshold=0.99)
    agent.collect([src])
    (d / "Tester" / "dung.pdf").write_bytes(make_pdf(cv_text(6)))
    s = agent.collect([src]).nguon[0]
    assert s.trang_thay_doi == 0 and s.hop_le == 1


def post_cv(url: str, fields: dict[str, str], filename: str, data: bytes) -> tuple[str, str]:
    """Nộp form /nop-ho-so như trình duyệt (multipart); trả về (URL sau chuyển hướng, HTML)."""
    import urllib.request
    b = "----ranhgioi"
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="cv"; filename="{filename}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    req = urllib.request.Request(url + "nop-ho-so", data=b"".join(parts) + f"--{b}--\r\n".encode(),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req) as r:
        return r.url, r.read().decode()


def test_upload_form_adds_cv_with_metadata_and_crawler_picks_it_up(agent, site):
    m, url, d = site
    src = JobBoardSource(url, client=fast())
    agent.collect([src])
    final, page = post_cv(url, {"vi_tri": "Tester", "ho_ten": "Đặng Thu Hà", "email": "ha@example.com"},
                          "Đặng Thu Hà.pdf", make_pdf(cv_text(7)))
    assert "da_nop=" in final and "Đã nhận hồ sơ: Đặng Thu Hà.pdf" in page and "<form" in page
    assert (d / "Tester" / "Đặng Thu Hà.pdf").exists()
    assert "data-email='ha@example.com'" in page and ">Đặng Thu Hà</a>" in page   # họ tên, email từ ung_vien.csv

    s = agent.collect([src]).nguon[0]
    assert (s.trang_thay_doi, s.hop_le) == (1, 1)
    c = next(c for c in agent.store.all() if c.ten_tep == "Đặng Thu Hà.pdf")
    assert (c.nguoi_gui, c.ten_nguoi_gui, c.vi_tri) == ("ha@example.com", "Đặng Thu Hà", "Tester")

    _, again = post_cv(url, {"vi_tri": "Tester"}, "Đặng Thu Hà.pdf", make_pdf(cv_text(8)))   # trùng tên: đổi tên
    assert "Đặng Thu Hà_2.pdf" in again and (d / "Tester" / "Đặng Thu Hà_2.pdf").exists()


def test_upload_form_rejects_bad_files(site):
    m, url, d = site
    assert "Chỉ nhận tệp" in post_cv(url, {"vi_tri": "Tester"}, "virus.exe", b"MZ" * 200)[1]
    assert "Chưa chọn tệp" in post_cv(url, {"vi_tri": "Tester"}, "cv.pdf", b"")[1]
    assert "Không có tin" in post_cv(url, {"vi_tri": "Không có"}, "cv.pdf", make_pdf(cv_text(1)))[1]
    assert not (d / "Tester" / "virus.exe").exists() and not (d / "Không có").exists()


def test_url_list_detects_changed_file(agent, site):
    m, url, d = site
    src = UrlListSource([url + "ho-so/Tester/chi.pdf", url + "ho-so/Tester/khong_co.pdf"], client=fast())
    s1 = agent.collect([src]).nguon[0]
    assert (s1.thu, s1.hop_le, s1.loi_tai) == (2, 1, 1)
    s2 = agent.collect([src]).nguon[0]
    assert (s2.thu, s2.hop_le) == (1, 0)                                  # tệp không đổi: không nhận lại
    (d / "Tester" / "chi.pdf").write_bytes(make_pdf(cv_text(7)))         # ứng viên cập nhật CV ở cùng URL
    s3 = agent.collect([src]).nguon[0]
    assert s3.hop_le == 1
    assert agent.store.all(FETCH_ERR)[0].tham_chieu.endswith("khong_co.pdf")


# ---- ngưỡng
def test_depth_and_file_limits(agent, site):
    m, url, _ = site
    s = agent.collect([JobBoardSource(url, client=fast(), max_depth=0)]).nguon[0]
    assert (s.trang, s.thu) == (1, 0)                                     # chỉ trang chủ, không có liên kết CV
    s = agent.collect([JobBoardSource(url, ten="b", client=fast(), max_files=1)]).nguon[0]
    assert s.thu == 1


def test_source_stops_after_consecutive_errors(agent, site):
    m, url, _ = site
    src = UrlListSource([url + f"khong-co/{i}" for i in range(5)], client=fast(), max_errors=2)
    s = agent.collect([src]).nguon[0]
    assert "2 lỗi tải liên tiếp" in s.loi
    assert sum(p.startswith("/khong-co/") for p in m.requests) == 2


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    h = email.message.Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x/", code, "err", h, None)


def test_http_client_retries_transient_errors_with_backoff():
    waits = []
    c = HttpClient(delay=0, retries=3, backoff=1.5, respect_robots=False, sleep=waits.append)
    errors = [_http_error(503), _http_error(429, "7"), TimeoutError("hết giờ")]

    def fake_get(url):
        if errors:
            raise errors.pop(0)
        return Response(url, 200, email.message.Message(), b"ok")

    c._get = fake_get
    assert c.get("http://x/").body == b"ok"
    assert waits == [1.5, 7.0, 6.0]                                       # 1.5·2⁰, Retry-After, 1.5·2²

    calls = []
    c._get = lambda url: calls.append(url) or (_ for _ in ()).throw(_http_error(404))
    with pytest.raises(urllib.error.HTTPError):
        c.get("http://x/")
    assert len(calls) == 1                                                # 404 không thử lại


# ---- máy tìm kiếm
def test_search_source_crawls_results(agent, site):
    m, url, _ = site
    src = SearchSource(["tester"], provider="searxng", api_url=url + "tim-kiem", client=fast(),
                       api_client=fast(respect_robots=False))
    assert sorted(src.search("tester")) == sorted(
        [url + "viec-lam/1", url + "ho-so/Tester/chi.pdf", url + "ho-so/Tester/hong.pdf"])
    s = agent.collect([src]).nguon[0]
    assert (s.trang, s.thu, s.hop_le, s.loi_tai) == (1, 2, 1, 1)
    assert agent.collect([src]).nguon[0].hop_le == 0                      # lần sau: không có gì mới


def test_search_source_needs_api_key(agent, monkeypatch):
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    s = agent.collect([SearchSource(["cv python"], provider="brave")]).nguon[0]
    assert "SEARCH_API_KEY" in s.loi


# ---- lập lịch theo giờ cố định
def test_fixed_time_schedule():
    times = parse_times("20:30, 8:00")
    assert times == ["08:00", "20:30"]
    assert next_slot(times, datetime(2026, 11, 24, 9, 0)) == datetime(2026, 11, 24, 20, 30)
    assert next_slot(times, datetime(2026, 11, 24, 21, 0)) == datetime(2026, 11, 25, 8, 0)
    assert next_slot(times, datetime(2026, 11, 24, 8, 0)) == datetime(2026, 11, 24, 20, 30)
    assert previous_slot(times, datetime(2026, 11, 24, 7, 0)) == datetime(2026, 11, 23, 20, 30)
    with pytest.raises(ValueError):
        parse_times("25:00")


def test_run_stats_with_pages_are_stored(agent, site):
    m, url, _ = site
    agent.collect([JobBoardSource(url, client=fast())])
    s = agent.store.runs(1)[0].nguon[0]
    assert (s.trang, s.trang_moi) == (3, 3)


def test_purge_keeps_latest_page_version(agent):
    s = agent.store
    track_page(s, "http://x/", "t", b"<p>a</p>", 0.01)
    track_page(s, "http://x/", "t", b"<p>b</p>", 0.01)
    v1, v2 = s.page_versions("http://x/")
    s.purge(1, now=datetime.now() + timedelta(days=3))
    assert [v.phien_ban for v in s.page_versions("http://x/")] == [2]
    assert v2.text() == "b"
    with pytest.raises(FileNotFoundError):
        v1.text()
