"""Trang tuyển dụng giả lập (kịch bản K3): phục vụ một thư mục CV như một cổng tuyển dụng có trang HTML và API.

    /                       danh sách tin tuyển dụng
    /viec-lam/<n>           tin thứ n: tiêu đề <h1> là vị trí, liên kết tới CV của từng ứng viên
    /ho-so/<tệp>            tải CV
    /quan-tri/              trang bị cấm trong robots.txt (để kiểm tra crawler tuân thủ)
    /api/ung-tuyen?page=n   API JSON phân trang (định dạng mặc định của ApiSource)
    /tim-kiem?q=…&format=json  máy tìm kiếm giả lập theo định dạng SearXNG (nguồn SearchSource)
    POST /nop-ho-so         form nộp hồ sơ ở mỗi tin: lưu CV vào thư mục của tin, ghi họ tên / email vào ung_vien.csv

Thư mục con là vị trí ứng tuyển; các tệp ung_vien.csv (ten_tep, ho_ten, email, vi_tri) ở thư mục gốc hoặc thư mục
con nếu có thì dùng làm metadata. Thư mục được đọc lại ở mỗi yêu cầu: thêm, sửa, xoá CV (chép vào thư mục hoặc nộp
qua form) khi máy chủ đang chạy thì trang tin tuyển dụng thay đổi theo (minh hoạ phát hiện thay đổi trang của crawler).
"""
from __future__ import annotations

import csv
import html
import json
import re
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from cvagents.collector.sources.dataset import read_metadata
from cvagents.common.config import load_config

GENERAL = "Ứng tuyển chung"
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword"}


class MockJobSite:
    def __init__(self, folder: str | Path, page_size: int = 5, hidden: list[str] | None = None,
                 broken: list[str] | None = None):
        self.folder = Path(folder)
        self.page_size = page_size
        self.hidden = set(hidden or [])  # chỉ có liên kết trong /quan-tri/
        self.broken = set(broken or [])  # liên kết trả về 404 (đo tỷ lệ tải thành công)
        self.requests: list[str] = []
        self._lock = threading.Lock()
        self.refresh()

    def refresh(self) -> None:
        """Đọc lại thư mục CV và các tệp ung_vien.csv."""
        meta: dict[str, dict[str, str]] = {}
        for csv_path in sorted(self.folder.rglob("ung_vien.csv")):
            meta.update(read_metadata(csv_path.read_text(encoding="utf-8-sig")))
        files: dict[str, Path] = {}
        jobs: dict[str, list[dict]] = {}
        for p in sorted(self.folder.rglob("*")):
            if not p.is_file() or p.name == "ung_vien.csv" or p.name.startswith("."):
                continue
            rel = p.relative_to(self.folder).as_posix()
            files[rel] = p
            m = meta.get(p.name, {})
            job = m.get("vi_tri") or (p.parent.name if p.parent != self.folder else GENERAL)
            jobs.setdefault(job, []).append({"id": f"hs{len(files):04d}", "tep": rel, "ten_tep": p.name,
                                             "ho_ten": m.get("ho_ten") or p.stem, "email": m.get("email")})
        self.files, self.jobs = files, jobs

    def applications(self) -> list[dict]:
        return [{**a, "vi_tri": job} for job, apps in self.jobs.items() for a in apps if a["tep"] not in self.hidden]

    def submit(self, job: str, filename: str, data: bytes, ho_ten: str = "", email: str = "") -> str:
        """Lưu CV nộp qua form vào thư mục của tin `job`; trả về tên tệp đã lưu. ValueError nếu tệp không hợp lệ."""
        name = re.sub(r'[\\/:*?"<>|]', "_", Path(filename.replace("\\", "/")).name).strip()
        ext = Path(name).suffix.lower()
        if ext not in MIME:
            raise ValueError(f"Chỉ nhận tệp {', '.join(sorted(MIME))}.")
        if not data:
            raise ValueError("Chưa chọn tệp CV.")
        max_mb = load_config()["collector"]["kich_thuoc_max_mb"]
        if len(data) > max_mb * 2**20:
            raise ValueError(f"Tệp lớn hơn {max_mb} MB.")
        with self._lock:
            self.refresh()
            if job not in self.jobs:
                raise ValueError("Không có tin tuyển dụng này.")
            d = self.folder if job == GENERAL else self.folder / re.sub(r'[\\/:*?"<>|]', "_", job)
            d.mkdir(parents=True, exist_ok=True)
            taken = {p.name.lower() for p in self.files.values()}  # metadata ghép theo tên tệp: tránh trùng tên
            stem, n = Path(name).stem, 1
            while name.lower() in taken:
                n += 1
                name = f"{stem}_{n}{ext}"
            (d / name).write_bytes(data)
            csv_path = d / "ung_vien.csv"
            new = not csv_path.exists()
            with csv_path.open("a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if not new and not csv_path.read_bytes().endswith(b"\n"):
                    f.write("\r\n")
                if new:
                    w.writerow(["ten_tep", "ho_ten", "email", "vi_tri"])
                w.writerow([name, ho_ten.strip(), email.strip(), job])
            self.refresh()
        return name

    def handler(self):
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _page(self, title: str, body: str):
                self._send(200, f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}"
                                f"</title></head><body><h1>{html.escape(title)}</h1>{body}</body></html>".encode())

            def do_GET(self):  # noqa: N802
                u = urlsplit(self.path)
                path = unquote(u.path)
                site.requests.append(path)
                if path != "/robots.txt":
                    site.refresh()
                jobs = list(site.jobs)
                if path == "/robots.txt":
                    return self._send(200, b"User-agent: *\nDisallow: /quan-tri/\n", "text/plain")
                if path == "/":
                    items = "".join(f"<li><a href='/viec-lam/{i}'>{html.escape(j)}</a></li>" for i, j in enumerate(jobs))
                    return self._page("Cổng tuyển dụng (giả lập)", f"<ul>{items}</ul><a href='/quan-tri/'>Quản trị</a>")
                if path.startswith("/viec-lam/"):
                    try:
                        job = jobs[int(path.rsplit("/", 1)[1])]
                    except (ValueError, IndexError):
                        return self._send(404, b"not found")
                    links = "".join(
                        f"<li><a href='/ho-so/{quote(a['tep'])}' data-ung-vien='{html.escape(a['ho_ten'])}'"
                        + (f" data-email='{html.escape(a['email'])}'" if a["email"] else "")
                        + f">{html.escape(a['ho_ten'])}</a></li>"
                        for a in site.jobs[job] if a["tep"] not in site.hidden)
                    done = parse_qs(u.query).get("da_nop", [""])[0]
                    note = f"<p><b>Đã nhận hồ sơ: {html.escape(done)}</b></p>" if done else ""
                    form = ("<h2>Nộp hồ sơ</h2><form method='post' action='/nop-ho-so' enctype='multipart/form-data'>"
                            f"<input type='hidden' name='vi_tri' value='{html.escape(job)}'>"
                            "<p>Họ tên <input name='ho_ten'> Email <input type='email' name='email'></p>"
                            f"<p><input type='file' name='cv' required accept='{','.join(MIME)}'> "
                            "<button type='submit'>Nộp hồ sơ</button></p></form>")
                    return self._page(job, f"{note}<ul>{links}</ul>{form}<a href='/'>Trang chủ</a>")
                if path.startswith("/quan-tri/"):
                    links = "".join(f"<a href='/ho-so/{quote(t)}'>{html.escape(t)}</a>" for t in site.hidden)
                    return self._page("Quản trị", links)
                if path.startswith("/ho-so/"):
                    rel = path[len("/ho-so/"):]
                    p = site.files.get(rel)
                    if p is None or rel in site.broken:
                        return self._send(404, b"not found", "text/plain")
                    return self._send(200, p.read_bytes(), MIME.get(p.suffix.lower(), "application/octet-stream"),
                                      {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(p.name)}"})
                if path == "/api/ung-tuyen":
                    apps = site.applications()
                    page = int(parse_qs(u.query).get("page", ["1"])[0])
                    chunk = apps[(page - 1) * site.page_size: page * site.page_size]
                    more = page * site.page_size < len(apps)
                    body = {"items": [{"id": a["id"], "ho_ten": a["ho_ten"], "email": a["email"],
                                       "vi_tri": a["vi_tri"], "ten_tep": a["ten_tep"],
                                       "cv_url": f"/ho-so/{quote(a['tep'])}"} for a in chunk],
                            "next": f"/api/ung-tuyen?page={page + 1}" if more else None}
                    return self._send(200, json.dumps(body, ensure_ascii=False).encode(), "application/json")
                if path == "/tim-kiem":  # kết quả: tin tuyển dụng và CV có tên / vị trí chứa mọi từ của truy vấn
                    words = parse_qs(u.query).get("q", [""])[0].lower().split()
                    page = int(parse_qs(u.query).get("pageno", ["1"])[0])
                    base = f"http://{self.headers.get('Host')}"
                    hits = [{"url": f"{base}/viec-lam/{i}", "title": j} for i, j in enumerate(jobs)
                            if all(w in j.lower() for w in words)]
                    hits += [{"url": f"{base}/ho-so/{quote(a['tep'])}", "title": a["ho_ten"]}
                             for a in site.applications() if all(w in f"{a['ho_ten']} {a['vi_tri']}".lower()
                                                                  for w in words)]
                    body = {"query": " ".join(words), "results": hits if page == 1 else []}
                    return self._send(200, json.dumps(body, ensure_ascii=False).encode(), "application/json")
                return self._send(404, b"not found", "text/plain")

            def do_POST(self):  # noqa: N802
                path = unquote(urlsplit(self.path).path)
                site.requests.append("POST " + path)
                if path != "/nop-ho-so":
                    return self._send(404, b"not found", "text/plain")
                size = int(self.headers.get("Content-Length") or 0)
                limit = (load_config()["collector"]["kich_thuoc_max_mb"] + 1) * 2**20
                if size > limit:
                    return self._page("Không nhận được hồ sơ", "<p>Tệp quá lớn.</p><a href='/'>Trang chủ</a>")
                body = self.rfile.read(size)
                msg = BytesParser(policy=policy.default).parsebytes(
                    f"Content-Type: {self.headers.get('Content-Type', '')}\r\n\r\n".encode() + body)
                fields, upload = {}, None
                for part in msg.iter_parts() if msg.is_multipart() else []:
                    name = part.get_param("name", header="content-disposition")
                    if part.get_filename() is not None:
                        upload = (part.get_filename(), part.get_payload(decode=True) or b"")
                    elif name:  # trình duyệt gửi ô chữ bằng UTF-8, không ghi charset
                        fields[name] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace").strip()
                job = fields.get("vi_tri", "")
                try:
                    if upload is None:
                        raise ValueError("Chưa chọn tệp CV.")
                    saved = site.submit(job, upload[0], upload[1], fields.get("ho_ten", ""), fields.get("email", ""))
                except ValueError as e:
                    return self._page("Không nhận được hồ sơ", f"<p>{html.escape(str(e))}</p>"
                                                                "<a href='/'>Trang chủ</a>")
                n = list(site.jobs).index(job)
                self._send(303, b"", "text/plain", {"Location": f"/viec-lam/{n}?da_nop={quote(saved)}"})

        return H

    def serve(self, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
        """Chạy máy chủ ở luồng nền; trả về (máy chủ, URL gốc). Gọi server.shutdown() để dừng."""
        srv = ThreadingHTTPServer((host, port), self.handler())
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://{host}:{srv.server_address[1]}/"
