"""Hợp đồng dữ liệu giữa các tác tử: CVProfile (đầu ra Extractor), JobDescription, AnalysisReport."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Contact(BaseModel):
    ho_ten: str | None = None
    email: str | None = None
    so_dien_thoai: str | None = None
    github: str | None = None
    linkedin: str | None = None


class Education(BaseModel):
    truong: str | None = None
    chuyen_nganh: str | None = None
    bac_hoc: int | None = None  # 1 trung cấp … 5 tiến sĩ
    thoi_gian: str | None = None
    gpa4: float | None = None


class Experience(BaseModel):
    cong_ty: str | None = None
    chuc_danh: str | None = None
    thang_bat_dau: int | None = None  # chỉ số tháng, công thức (2.9)
    thang_ket_thuc: int | None = None
    mo_ta: list[str] = Field(default_factory=list)


class Project(BaseModel):
    ten: str | None = None
    thoi_gian: str | None = None
    mo_ta: list[str] = Field(default_factory=list)


class Skills(BaseModel):
    goc: list[str] = Field(default_factory=list)       # chuỗi gốc theo nhóm
    chuan_hoa: list[str] = Field(default_factory=list)  # tập K, công thức (2.8)


class CVProfile(BaseModel):
    thong_tin_ca_nhan: Contact = Field(default_factory=Contact)
    muc_tieu: str | None = None
    hoc_van: list[Education] = Field(default_factory=list)
    kinh_nghiem: list[Experience] = Field(default_factory=list)
    du_an: list[Project] = Field(default_factory=list)
    ky_nang: Skills = Field(default_factory=Skills)
    chung_chi: list[str] = Field(default_factory=list)
    tong_thang_kinh_nghiem: int = 0  # M, công thức (2.10)
    do_tin_cay: float = 1.0          # kappa, (2.12)
    muc_day_du: float = 0.0          # phi, (2.13)
    canh_bao: list[str] = Field(default_factory=list)
    nhanh_doc: str = "text"          # "text" | "ocr"
    van_ban_goc: str = ""            # toàn văn CV sau bước đọc, dùng cho thành phần tương đồng toàn văn


class JobDescription(BaseModel):
    tieu_de: str = ""
    ky_nang_bat_buoc: list[str] = Field(default_factory=list)
    ky_nang_uu_tien: list[str] = Field(default_factory=list)
    so_thang_kinh_nghiem_toi_thieu: int = 0
    bac_hoc_toi_thieu: int | None = None
    yeu_cau_khac: list[str] = Field(default_factory=list)  # các dòng yêu cầu còn lại
    van_ban_goc: str = ""


class RequirementMatch(BaseModel):
    yeu_cau: str
    loai: str  # "bat_buoc" | "uu_tien"
    cosine: float
    bang_chung: str | None = None
    dat: bool


class AnalysisReport(BaseModel):
    ten_ung_vien: str | None = None
    diem_tong: float
    diem_thanh_phan: dict[str, float]
    phu_hop: bool
    da_dap_ung: list[RequirementMatch] = Field(default_factory=list)
    chua_dap_ung: list[RequirementMatch] = Field(default_factory=list)
    ghi_chu: list[str] = Field(default_factory=list)
