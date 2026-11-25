"""Dữ liệu của CV_Collector_Agent: tệp nhận từ nguồn (Incoming), bản ghi trong sổ cái (CollectedCV), thống kê."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

# Trạng thái một tệp trong sổ cái
NEW, DONE, FAILED = "moi", "da_xu_ly", "loi_xu_ly"            # hồ sơ hợp lệ: chờ / đã qua CV_Extractor
DUP, NEAR_DUP, INVALID, FETCH_ERR = "trung", "gan_trung", "khong_hop_le", "loi_tai"  # không chuyển giao
ACCEPTED = (NEW, DONE, FAILED)
STATUS_LABEL = {NEW: "Chờ đánh giá", DONE: "Đã đánh giá", FAILED: "Lỗi trích xuất", DUP: "Trùng lặp",
                NEAR_DUP: "Gần trùng", INVALID: "Không hợp lệ", FETCH_ERR: "Lỗi tải"}

# Loại nguồn
FOLDER, IMAP, WEB, API, DATASET, DRIVE, UPLOAD, FORM, URL_LIST, SEARCH = (
    "thu_muc", "email", "trang_tuyen_dung", "api", "bo_du_lieu", "google_drive", "tai_len", "form_ung_tuyen",
    "danh_sach_url", "tim_kiem")
SOURCE_LABEL = {FOLDER: "Thư mục", IMAP: "Email (IMAP)", WEB: "Trang tuyển dụng", API: "API", DATASET: "Bộ dữ liệu",
                DRIVE: "Google Drive", UPLOAD: "Tải lên", FORM: "Form ứng tuyển", URL_LIST: "Danh sách URL",
                SEARCH: "Máy tìm kiếm"}

# Kết quả so một trang web với phiên bản đã lưu gần nhất (changes.track_page)
PAGE_NEW, PAGE_CHANGED, PAGE_SAME = "moi", "thay_doi", "khong_doi"
PAGE_LABEL = {PAGE_NEW: "Trang mới", PAGE_CHANGED: "Đã thay đổi", PAGE_SAME: "Không đổi"}


@dataclass
class Incoming:
    """Một tệp do nguồn trả về, chưa kiểm tra. `error` khác rỗng nghĩa là nguồn không tải được tệp này."""
    name: str
    data: bytes
    nguon: str
    tham_chieu: str = ""             # đường dẫn, Message-ID, URL, mã hồ sơ trên API …
    nguoi_gui: str | None = None     # email người nộp (nếu nguồn biết)
    ten_nguoi_gui: str | None = None
    tieu_de: str | None = None       # tiêu đề thư / tin tuyển dụng
    vi_tri: str | None = None        # vị trí ứng tuyển
    thoi_diem: datetime | None = None
    error: str = ""


class CollectedCV(BaseModel):
    """Một dòng trong sổ cái của Collector; hồ sơ trạng thái `moi` là đầu vào của CV_Extractor_Agent."""
    id: int | None = None
    key: str                          # băm nội dung (screening.content_key)
    ten_tep: str
    nguon: str
    ten_nguon: str = ""               # tên cấu hình nguồn, ví dụ "Gmail tuyển dụng"
    tham_chieu: str = ""
    dinh_dang: str = ""               # pdf | docx | png | jpg
    kich_thuoc: int = 0
    so_trang: int | None = None
    nguoi_gui: str | None = None
    ten_nguoi_gui: str | None = None
    tieu_de: str | None = None
    vi_tri: str | None = None
    thoi_diem_nhan: str = ""          # ISO 8601
    trang_thai: str = NEW
    ly_do: str = ""
    trung_voi: str | None = None      # khoá hồ sơ gốc nếu trùng / gần trùng / cùng ứng viên
    duong_dan: str | None = None
    van_tay: str | None = None        # simhash văn bản hoặc dHash ảnh (hex)
    loai_van_tay: str | None = None   # "text" | "image"
    email: str | None = None          # email / SĐT ứng viên đọc được trong CV (nhận diện cùng ứng viên)
    sdt: str | None = None
    lan_chay: int | None = None


@dataclass
class SourceStats:
    """Thống kê một nguồn trong một lần chạy, đúng các độ đo của kịch bản K3 (Bảng 3.8)."""
    ten: str
    nguon: str
    thu: int = 0           # số tệp nguồn trả về (kể cả tải lỗi)
    loi_tai: int = 0
    trung: int = 0
    gan_trung: int = 0
    khong_hop_le: int = 0
    hop_le: int = 0        # chuyển giao cho CV_Extractor
    cung_ung_vien: int = 0
    giay: float = 0.0
    loi: str = ""          # lỗi làm dừng cả nguồn (đăng nhập, mất kết nối …)
    trang: int = 0         # nguồn web: số trang HTML đã đọc, trong đó mới / thay đổi so với phiên bản đã lưu
    trang_moi: int = 0
    trang_thay_doi: int = 0

    @property
    def tai_duoc(self) -> int:
        return self.thu - self.loi_tai

    @property
    def ty_le_thanh_cong(self) -> float:
        return self.tai_duoc / self.thu if self.thu else 0.0

    @property
    def ty_le_trung(self) -> float:
        return (self.trung + self.gan_trung) / self.tai_duoc if self.tai_duoc else 0.0

    @property
    def ty_le_hop_le(self) -> float:
        n = self.tai_duoc - self.trung - self.gan_trung
        return self.hop_le / n if n > 0 else 0.0

    @property
    def giay_moi_ho_so(self) -> float:
        return self.giay / self.thu if self.thu else 0.0


@dataclass
class PageVersion:
    """Một phiên bản đã lưu của trang web: HTML gốc ở `<duong_dan>.html`, phần chữ dùng để so ở `<duong_dan>.txt`."""
    url: str
    phien_ban: int
    thoi_diem: str
    sha1: str               # băm phần chữ
    ty_le_thay_doi: float   # so với phiên bản trước (1.0 với phiên bản đầu)
    duong_dan: str
    nguon: str = ""

    def html(self) -> bytes:
        return Path(self.duong_dan + ".html").read_bytes()

    def text(self) -> str:
        return Path(self.duong_dan + ".txt").read_text(encoding="utf-8")


@dataclass
class RunReport:
    id: int | None = None
    bat_dau: str = ""
    ket_thuc: str = ""
    nguon: list[SourceStats] = field(default_factory=list)
    moi: list[CollectedCV] = field(default_factory=list)  # hồ sơ hợp lệ mới nhận trong lần chạy

    def total(self) -> SourceStats:
        t = SourceStats("Tổng", "")
        for s in self.nguon:
            for f in ("thu", "loi_tai", "trung", "gan_trung", "khong_hop_le", "hop_le", "cung_ung_vien", "giay",
                      "trang", "trang_moi", "trang_thay_doi"):
                setattr(t, f, getattr(t, f) + getattr(s, f))
        return t
