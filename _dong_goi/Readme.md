# cvagents – Hệ thống đánh giá hồ sơ ứng viên tự động dựa trên kiến trúc đa tác tử

Mã nguồn sản phẩm của khoá luận tốt nghiệp **"Nghiên cứu và phát triển hệ thống đánh giá hồ sơ ứng viên tự động dựa trên kiến trúc đa tác tử"**.

- Sinh viên: Phạm Đình Mạnh – MSV 11236150
- Giảng viên hướng dẫn: TS. Lưu Minh Tuấn
- Trường Đại học Kinh tế Quốc dân

## 1. Giới thiệu sản phẩm

Hệ thống nhận hồ sơ (CV) của ứng viên từ nhiều nguồn, đọc và trích xuất thông tin từ CV ở nhiều định dạng (PDF có lớp văn bản, PDF quét, DOCX, ảnh PNG/JPG), đối chiếu ngữ nghĩa CV với mô tả công việc (JD), chấm điểm, xếp hạng và gửi thư mời phỏng vấn hoặc thư từ chối. Hệ thống gồm bốn tác tử phối hợp theo một luồng:

| Tác tử | Chức năng |
|---|---|
| CV_Collector_Agent | Thu thập CV từ thư mục, hộp thư IMAP/Gmail, trang tuyển dụng (crawler tuân thủ robots.txt), API, bộ dữ liệu .zip/CSV, Google Drive và form nộp hồ sơ; kiểm tra định dạng theo magic bytes; loại trùng 3 mức (trùng byte SHA-1, gần trùng SimHash/dHash, cùng ứng viên); lưu sổ cái SQLite |
| CV_Extractor_Agent | Pipeline 8 bước: kiểm tra chất lượng lớp văn bản, chuyển OCR có điều kiện (Docling + EasyOCR), sắp thứ tự đọc hai cột, chuẩn hoá, nhận diện mục, trích xuất thực thể (regex, gazetteer + so khớp mờ rapidfuzz), gom trường, tính độ tin cậy và cảnh báo |
| CV/JD_Analysis_Agent | Phân tích JD thành yêu cầu bắt buộc / ưu tiên / kinh nghiệm / học vấn; mã hoá bằng Vietnamese_Embedding đã tinh chỉnh; đối sánh từng yêu cầu với bằng chứng trong CV; tính điểm tổng hợp sáu thành phần và phân loại Đạt / Cần xem xét lại / Không đạt |
| Email_Agent | Soạn và gửi thư mời phỏng vấn / thư từ chối qua SMTP (hoặc lưu nháp .eml), có nhật ký chống gửi trùng |

Nhà tuyển dụng thao tác qua giao diện web Streamlit gồm 4 trang: Thu thập hồ sơ, Đánh giá hồ sơ, Xếp hạng ứng viên, Cài đặt email.

## 2. Cấu trúc thư mục

```
Source/
├── README.md                  ghi chú kỹ thuật chi tiết (ánh xạ mã nguồn ↔ công thức trong khoá luận)
├── pyproject.toml             khai báo gói cvagents và các nhóm thư viện phụ thuộc
├── .streamlit/config.toml     giao diện (theme) của ứng dụng Streamlit
├── src/cvagents/
│   ├── common/                schemas (CVProfile, JobDescription, AnalysisReport), nạp cấu hình, tiện ích chuỗi
│   ├── collector/             CV_Collector_Agent
│   │   ├── sources/           các nguồn: folder, imap, web, api, dataset, drive
│   │   ├── validate.py        kiểm tra định dạng tệp
│   │   ├── fingerprint.py     loại trùng 3 mức
│   │   ├── store.py           sổ cái SQLite
│   │   ├── scheduler.py       thu thập định kỳ, ghi log
│   │   ├── mocksite.py        trang tuyển dụng + API giả lập (phục vụ thử nghiệm)
│   │   └── agent.py           điều phối tác tử
│   ├── extractor/             CV_Extractor_Agent (reader, quality, layout, normalize, sections,
│   │                          entities, fields, confidence, visualize, agent)
│   ├── analysis/              CV/JD_Analysis_Agent (jd_parser, embedder, matching, scoring, agent)
│   ├── mailer/                Email_Agent
│   ├── screening.py           điều phối một đợt sàng lọc: Collector → Extractor → Analysis → Email
│   └── resources/             config.toml (ngưỡng, trọng số, email), sections.json, skills.json,
│                              email_templates.json, english_words.json
├── scripts/
│   ├── recruiter_app.py       giao diện Streamlit cho nhà tuyển dụng
│   ├── candidate_portal.py    form nộp hồ sơ cho ứng viên
│   ├── collect.py             CV_Collector dòng lệnh (thu thập một lần / theo dõi liên tục)
│   ├── mock_jobsite.py        chạy trang tuyển dụng giả lập
│   ├── run_pipeline.py        chạy Extractor + Analysis trên một CV và một JD
│   ├── send_emails.py         Email_Agent dòng lệnh
│   ├── demo_pdf.py            chạy minh hoạ toàn hệ thống trên vài CV PDF
│   ├── translate_dataset.py   dịch bộ dữ liệu CV–JD sang tiếng Việt (cần GPU)
│   ├── finetune_embedding.py  tinh chỉnh Vietnamese_Embedding (cần GPU)
│   ├── eval_matching.py       thử nghiệm nhóm A: đối sánh CV–JD (KB1–KB4, BM25, VM)
│   ├── compare_configs.py     kiểm định bootstrap ghép cặp giữa các cấu hình
│   ├── eval_pdf.py            đối sánh khi CV vào hệ thống dưới dạng PDF / PDF quét
│   ├── make_noisy.py          tạo bản quét có nhiễu từ CV PDF
│   ├── eval_extractor.py      thử nghiệm nhóm B: CV_Extractor
│   ├── eval_collector.py      kịch bản K3: CV_Collector
│   └── eval_e2e.py            kiểm thử tích hợp toàn hệ thống
├── tests/                     kiểm thử đơn vị (pytest)
└── data/raw/                  JD mẫu: sample_jd.txt, jd_backend_dotnet.txt
```

Các thư mục `data/inbox`, `data/outbox`, `data/logs`, `data/processed` được tạo tự động khi chạy.

## 3. Yêu cầu hệ thống

- Python 3.11 trở lên (đã chạy trên Windows).
- RAM tối thiểu 8 GB; CPU chạy được giao diện và đánh giá hồ sơ. GPU chỉ cần khi dịch dữ liệu và tinh chỉnh mô hình (đã dùng Google Colab).
- Khoảng 6 GB dung lượng trống cho thư viện và hai mô hình nhúng.
- Kết nối Internet ở lần chạy đầu để tải mô hình OCR và (nếu dùng) mô hình gốc từ Hugging Face.

## 4. Cài đặt

```bat
cd Source
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[docling,embedding,ui,eval,dev]" easyocr
```

Nếu đường dẫn thư mục có chữ tiếng Việt có dấu và `pip install -e` báo lỗi, cài thư viện rồi chạy bằng `PYTHONPATH`:

```bat
pip install pydantic rapidfuzz docling rapidocr onnxruntime sentence-transformers streamlit pandas openpyxl scikit-learn scipy pypdfium2 pytest easyocr
set PYTHONPATH=src
```

Nguồn Google Drive (tuỳ chọn) cần thêm `pip install google-api-python-client google-auth`.

Kiểm tra cài đặt:

```bat
python -m pytest
```

## 5. Mô hình nhúng (không kèm trong tệp nộp)

Hai mô hình đã tinh chỉnh từ `AITeamVN/Vietnamese_Embedding` (mỗi mô hình khoảng 2,3 GB) **không được đưa vào tệp ZIP** do giới hạn dung lượng nộp 20 MB:

| Thư mục | Dùng cho | Dữ liệu tinh chỉnh |
|---|---|---|
| `models/vn-embedding-cvjd` | JD tiếng Anh | bộ Resume–Job Matching `rahulrajpu676/job-resume-matching` (Hugging Face) |
| `models/vn-embedding-cvjd-vi` | JD tiếng Việt | bộ trên được dịch sang tiếng Việt bằng `vinai-translate-en2vi-v2` |

Đường dẫn mô hình khai báo ở `src/cvagents/resources/config.toml`, mục `[analysis]`, khoá `embed_model` và `embed_model_vi` (mặc định `models/vn-embedding-cvjd` và `models/vn-embedding-cvjd-vi`, tính từ thư mục `Source`). Có ba cách để có mô hình:

1. **Dùng mô hình gốc (nhanh nhất, để chạy thử):** sửa hai khoá trên thành `"AITeamVN/Vietnamese_Embedding"`; mô hình được tải tự động từ Hugging Face ở lần chạy đầu. Hệ thống chạy đầy đủ chức năng nhưng độ chính xác đối sánh thấp hơn kết quả trong khoá luận.
2. **Nhận bản mô hình từ tác giả:** liên hệ sinh viên để nhận thư mục `models/` rồi đặt vào `Source/models/`.
3. **Huấn luyện lại** (cần GPU, ví dụ Google Colab T4):

```bat
pip install -e ".[train]"
python scripts/finetune_embedding.py --out models/vn-embedding-cvjd
python scripts/translate_dataset.py --out data/cvjd_vi
python scripts/finetune_embedding.py --data-dir data/cvjd_vi --out models/vn-embedding-cvjd-vi --grad-checkpointing
```

Kết quả trên tập kiểm tra của mô hình gốc và mô hình sau tinh chỉnh được ghi vào `<thư mục --out>/results.json`.

## 6. Chạy và triển khai

### 6.1. Giao diện nhà tuyển dụng

```bat
streamlit run scripts/recruiter_app.py
```

Mở `http://localhost:8501`. Luồng sử dụng: trang **Đánh giá hồ sơ** → nhập/tải JD (ví dụ `data/raw/jd_backend_dotnet.txt`) và tải lên nhiều CV (PDF, DOCX, PNG, JPG) → **Xếp hạng ứng viên** xem điểm, trạng thái và bằng chứng của từng yêu cầu (bấm một dòng để xem chi tiết) → ứng viên Đạt được gửi thư mời tự động, ứng viên "Cần xem xét lại" chờ nhà tuyển dụng duyệt. Trang **Thu thập hồ sơ** cấu hình nguồn và thu thập ngay hoặc theo chu kỳ.

### 6.2. Form nộp hồ sơ cho ứng viên

```bat
streamlit run scripts/candidate_portal.py --server.port 8502
```

### 6.3. Gửi email thật

Khi chưa cấu hình SMTP, thư được lưu nháp `.eml` trong `data/outbox`. Để gửi thật, nhập tài khoản ở trang **Cài đặt email** hoặc đặt biến môi trường (Gmail cần App Password):

```bat
set SMTP_USER=tuyendung@gmail.com
set SMTP_PASSWORD=<app password>
```

### 6.4. Thu thập hồ sơ bằng dòng lệnh

```bat
set IMAP_USER=tuyendung@gmail.com
set IMAP_PASSWORD=<app password>
python scripts/collect.py --folder "D:/Tuyen dung/CV" --imap --watch --interval 300
python scripts/collect.py --imap --jd data/raw/sample_jd.txt --vi-tri "Backend Developer"
python scripts/mock_jobsite.py          # trang tuyển dụng giả lập để thử crawler
```

### 6.5. Chạy một CV với một JD

```bat
python scripts/run_pipeline.py <đường dẫn CV .pdf/.docx/.png> data/raw/jd_backend_dotnet.txt
```

### 6.6. Cấu hình

Mọi ngưỡng và trọng số nằm trong `src/cvagents/resources/config.toml`: ngưỡng trích xuất và OCR (`[extractor]`), trọng số điểm tổng hợp, ngưỡng Đạt `pass_threshold` và biên xem xét lại `review_margin` (`[analysis]`), máy chủ email (`[email]`), thu thập (`[collector]`). Trọng số hiện tại được học trên tập kiểm định (Chương 3 của khoá luận).

## 7. Tái lập kết quả thử nghiệm (Chương 3)

Bộ dữ liệu `rahulrajpu676/job-resume-matching` được tải tự động từ Hugging Face; bản tiếng Việt do `translate_dataset.py` tạo.

```bat
python scripts/eval_matching.py --lang en --out results/A_en
python scripts/eval_matching.py --lang vi --data-dir data/cvjd_vi --out results/A_vi
python scripts/compare_configs.py --dir results/A_vi --pairs KB2:KB1,KB3:KB2,KB4:KB3,KB4:KB1,KB4:BM25,KB4:VM
python scripts/eval_pdf.py --lang vi --data-dir data/cvjd_vi --out results/pdf_vi --n-cv 200 --ket-qua-chinh results/A_vi/ket_qua.json
python scripts/eval_collector.py --synthetic 90
```

## 8. Lưu ý

- OCR mặc định là EasyOCR (tiếng Việt và tiếng Anh). RapidOCR làm rơi dấu tiếng Việt nên không dùng mặc định; đổi ở `ocr_engine` trong `config.toml`.
- Hệ thống không dùng mô hình NER: họ tên và tên tổ chức được nhận diện bằng quy tắc.
- Trên Windows, nếu thư mục cài đặt có chữ có dấu, docling-parse có thể không mở được tệp tài nguyên; khi đó hệ thống tự chuyển sang backend pypdfium2 cho PDF.
- Crawler chỉ đi trong cùng tên miền với trang bắt đầu và tuân thủ robots.txt.
- Chi tiết kỹ thuật bổ sung xem `Source/README.md`.
