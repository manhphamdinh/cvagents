# Bộ CV demo mới (CV11–CV16) và 2 JD

6 CV tổng hợp, nhân vật hư cấu, viết dài và tự nhiên hơn bộ cv_mau cũ. CV đạt dùng email **markpham25@gmail.com** để nhận thư mời thật khi demo. CV không đạt dùng email @example.com.

## 2 JD (giống JD trong khoá luận, mục 3.2.4 / Bảng 3.13)

| Tệp | Tin tuyển dụng | Kỹ năng bắt buộc | Ưu tiên |
|---|---|---|---|
| jd/jd_backend_dotnet.txt | Backend Developer (.NET) | C#, .NET, REST API, SQL Server hoặc PostgreSQL, SQL, Git (6) | Entity Framework, Docker |
| jd/jd_backend_python_fastapi.txt | Backend Developer (Python/FastAPI) | Python, FastAPI, PostgreSQL (3) | Docker, Kubernetes |

Hai tệp chép nguyên văn từ data/raw/jd_backend_dotnet.txt và data/raw/sample_jd.txt (bản đã dùng để chạy Bảng 3.13).

## 6 CV

| Đợt | CV | Hồ sơ | Nộp vào tin | Định dạng | Kỹ năng bắt buộc | Kết quả mong đợi | Email |
|---|---|---|---|---|---|---|---|
| 1 (crawl lần đầu) | CV11 Lê Hoàng Phúc | Backend .NET, 3,5 năm | .NET | PDF có lớp chữ | 6/6 | **Đạt** | markpham25@gmail.com |
| 1 (crawl lần đầu) | CV12 Nguyễn Thu Hà | Python/FastAPI, 3 năm | Python | DOCX | 3/3 | **Đạt** | markpham25@gmail.com |
| 1 (crawl lần đầu) | CV13 Hoang Gia Bao | Java Spring Boot, 4 năm, CV tiếng Anh | .NET | JPG (OCR) | 4/6 (thiếu C#, .NET) | Không đạt | hoang.gia.bao@example.com |
| 2 (nộp qua form) | CV15 Phan Ngọc Mai | .NET, 2 năm | .NET | PDF có lớp chữ | 6/6 | **Đạt** | markpham25@gmail.com |
| 2 (nộp qua form) | CV14 Trịnh Quốc Việt | Python Django, 2,5 năm | Python | PNG (OCR) | 2/3 (thiếu FastAPI) | Không đạt | quocviet.django@example.com |
| 2 (nộp qua form) | CV16 Đinh Thảo Vy | Phân tích dữ liệu (Python) | Python | PDF quét (OCR) | 1/3 | Không đạt | thaovy.data@example.com |

CV13 và CV14 là hai ca "gần đạt": cùng ngành, điểm toàn văn có thể cao, nhưng bị luật kỹ năng bắt buộc loại (dưới 75%). Đây là điểm nên chỉ ra khi demo.

## Thư mục

- `trang_gia_lap/`: thư mục cho trang tuyển dụng giả lập, chỉ có 3 CV của đợt 1 (+ ung_vien.csv). Tin Python mới có 1 CV.
- `tai_len_dot_2/`: 3 CV của đợt 2 để nộp qua form "Nộp hồ sơ" trên trang; `thong_tin_nop_form.csv` ghi họ tên, email và tin cần chọn.
- `tat_ca_dinh_dang/`: mỗi CV đủ 6 dạng (.txt, .pdf, _scan.pdf, .docx, .jpg, .png) nếu muốn đổi định dạng.
- `jd/`: 2 JD.
- `_ma_tao/`: mã sinh lại (`python build_demo.py <thư mục ra> <jd .NET> <jd Python>`, cần make_cvs.py và cv_noi_dung.py cùng thư mục).

## Chạy demo

1. Chép `trang_gia_lap` ra một bản để chạy (form nộp hồ sơ ghi tệp vào thư mục này, giữ bản gốc để chạy lại):
   `python scripts/mock_jobsite.py data/cv_demo_moi/trang_gia_lap_chay --port 8765`
2. Trên trang Thu thập hồ sơ, nhập `http://127.0.0.1:8765/`, bấm "Thu thập ngay": nhận 3 hồ sơ mới.
3. Mở `http://127.0.0.1:8765/` trên trình duyệt, vào từng tin, nộp 3 CV trong `tai_len_dot_2` theo `thong_tin_nop_form.csv`.
4. Thu thập lại: chỉ 3 hồ sơ vừa nộp được nhận (trang tin thay đổi).
5. Đánh giá từng tin với JD tương ứng trong `jd/`.

## Lưu ý

- Ba CV đạt cùng email markpham25@gmail.com nên Tác tử thu thập CV sẽ gắn cờ "trùng ứng viên" (chỉ gắn cờ, không loại). Thư mời vẫn gửi đủ vì SentLog ghi khoá theo băm của CV.
- Nên dùng inbox mới (hoặc dọn inbox cũ) để các CV cũ không lẫn vào kết quả.
- Phần kỹ năng bắt buộc đã kiểm tra bằng đúng mã jd_parser/matching. Riêng điểm S (ngưỡng 0,589) chưa chạy được vì máy cloud không có mô hình đã tinh chỉnh. Các CV .NET thật trong Bảng 3.13 đạt 0,66–0,72, nên CV11, CV12, CV15 dự kiến vượt ngưỡng; nếu một CV chỉ ra "Cần xem xét lại" thì có thể duyệt tay trên giao diện.
