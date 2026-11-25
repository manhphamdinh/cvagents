# cvagents – hệ thống đa tác tử sàng lọc hồ sơ: CV_Collector, CV_Extractor, CV/JD_Analysis, Email

Cài đặt theo Chương 2 của khóa luận (mục 2.3 trích xuất, 2.4–2.5 đối chiếu ngữ nghĩa).

## Cấu trúc

```
src/cvagents/
  common/      schemas (CVProfile, JobDescription, AnalysisReport), config, tiện ích chuỗi
  extractor/   CV_Extractor_Agent – pipeline 8 bước (mục 2.3.4)
    reader.py      bước 1–2  Docling + OCR (adapter)
    quality.py     bước 1    (2.2)–(2.5)  mật độ chữ, ký tự lạ, từ điển
    layout.py      bước 2    (2.6)–(2.7)  lọc OCR, thứ tự đọc hai cột
    normalize.py   bước 3    NFC, bullet, gạch ngang
    sections.py    bước 4–5  tiêu đề mục (Bảng 2.1), bảng
    entities.py    bước 6    regex, gazetteer + rapidfuzz, ngày, GPA   (2.8), (2.9), (2.11)
    fields.py      bước 7    gom trường, tổng tháng kinh nghiệm        (2.10)
    confidence.py  bước 8    κ (2.12), φ (2.13), cảnh báo
    visualize.py   vẽ khối bố cục (nhãn, thứ tự đọc) và ô OCR lên ảnh trang
  analysis/    CV/JD_Analysis_Agent
    jd_parser.py   JD → yêu cầu bắt buộc / ưu tiên / kinh nghiệm / học vấn
    embedder.py    Vietnamese_Embedding (BGE-M3) + bộ nhớ đệm; chọn mô hình theo ngôn ngữ JD; HashingEmbedder để test
    matching.py    cosine từng yêu cầu ↔ bằng chứng trong CV
    scoring.py     điểm nhiều thành phần, trọng số trong config.toml
  collector/   CV_Collector_Agent – thu thập, kiểm tra định dạng, loại trùng, chuyển giao cho Extractor
    sources/       nguồn: folder (thư mục, theo dõi), imap (Gmail/IMAP), web (crawler trang tuyển dụng, robots.txt;
                   danh sách URL theo dõi), search (tìm CV qua API máy tìm kiếm), net (HTTP: robots.txt, giãn cách,
                   thử lại), api (JSON phân trang), dataset (thư mục/.zip + CSV), drive (Google Drive)
    changes.py     phát hiện thay đổi trang giữa các lần crawl (tỷ lệ dòng chữ khác δ), lưu phiên bản trang
    validate.py    định dạng theo magic bytes; PDF hỏng / có mật khẩu / quá nhiều trang, .doc cũ, zip bomb, ảnh nhỏ
    fingerprint.py loại trùng 3 mức: trùng byte (SHA-1), gần trùng (simhash văn bản, dHash ảnh), cùng ứng viên
    store.py       sổ cái SQLite (data/inbox/collector.db): hồ sơ + metadata, trạng thái nguồn, thống kê lần chạy,
                   trang đã crawl + phiên bản (HTML và phần chữ trong data/inbox/pages/)
    agent.py       điều phối: nguồn → giải nén → kiểm tra → loại trùng → lưu → thư xác nhận; mark() sau Extractor
    scheduler.py   thu thập định kỳ theo chu kỳ hoặc giờ cố định, dọn hồ sơ quá hạn, ghi log data/logs/collector.log
    mocksite.py    trang tuyển dụng + API + máy tìm kiếm giả lập cho kịch bản K3
  mailer/      Email_Agent – thư mời phỏng vấn / từ chối, gửi SMTP hoặc lưu .eml, nhật ký chống gửi trùng
  screening.py điều phối một đợt sàng lọc: (Collector →) Extractor → Analysis → trạng thái (Đạt / Cần xem xét lại / Không đạt) → Email
  resources/   config.toml (ngưỡng, trọng số, email), sections.json, skills.json, email_templates.json
scripts/
  recruiter_app.py   giao diện Streamlit cho nhà tuyển dụng
  candidate_portal.py form nộp hồ sơ cho ứng viên (nguồn của CV_Collector)
  collect.py         CV_Collector dòng lệnh: thu thập một lần / theo dõi, tuỳ chọn đánh giá và gửi thư ngay
  mock_jobsite.py    chạy trang tuyển dụng giả lập
  eval_collector.py  kịch bản K3: đo Collector theo nguồn (Bảng 3.8)
  send_emails.py     Email_Agent dòng lệnh, đọc kết quả của run_pipeline.py
tests/  data/{raw,processed}/  docs/
```

## Chạy

```
python -m venv .venv && .venv\Scripts\pip install -e ".[docling,embedding,dev]" easyocr
set PYTHONPATH=src            # đường dẫn có chữ có dấu làm `pip install -e` lỗi trên Windows
python -m pytest
python scripts/run_pipeline.py <đường dẫn CV .pdf/.docx/.png> data/raw/sample_jd.txt
```

Giao diện nhà tuyển dụng gồm 4 trang (thanh điều hướng phía trên): Thu thập hồ sơ (cấu hình nguồn, thu thập ngay hoặc
tự động theo chu kỳ, sổ cái hồ sơ), Đánh giá hồ sơ (tải JD + nhiều CV, hoặc lấy hồ sơ đang chờ trong hộp thu thập), Xếp hạng ứng
viên (trạng thái từng CV; bấm một dòng để mở hộp thoại chi tiết: đánh giá, ảnh gốc và khối nhận diện, hồ sơ trích xuất,
duyệt hồ sơ cần xem xét) và Cài đặt email. Theme đặt trong `.streamlit/config.toml` (chạy từ thư mục gốc dự án).
Mô hình nhúng chọn tự động theo ngôn ngữ JD (`embed_model_vi` / `embed_model` trong config.toml). Ứng viên Đạt được
tự động gửi thư mời; điểm trong [τS − review_margin, τS) chờ nhà tuyển dụng duyệt.

```
.venv\Scripts\pip install streamlit
.venv\Scripts\streamlit run scripts/recruiter_app.py
```

Email_Agent: chưa có tài khoản SMTP thì thư được lưu nháp `.eml` vào `data/outbox`. Để gửi thật, nhập tài khoản ở
trang Cài đặt email của giao diện hoặc đặt biến môi trường (Gmail cần App Password):

```
set SMTP_USER=tuyendung@gmail.com
set SMTP_PASSWORD=<app password>
python scripts/send_emails.py data/processed --vi-tri "Backend Developer"          # lưu nháp
python scripts/send_emails.py data/processed --vi-tri "Backend Developer" --send   # gửi thật
```

## CV_Collector

Hồ sơ đi qua: nguồn → giải nén .zip → kiểm tra định dạng → loại trùng byte → loại gần trùng → nhận diện cùng ứng viên
(chỉ đánh dấu) → lưu vào `data/inbox/files/` với trạng thái "Chờ đánh giá". Mọi tệp, kể cả bị loại, đều có một dòng
trong sổ cái kèm lý do, nên tính được các độ đo K3. Mỗi nguồn thu thập tăng dần (thư mục: kích thước + thời điểm sửa;
IMAP: UID; web/API: URL / id), một nguồn lỗi không làm dừng các nguồn khác.

```
set IMAP_USER=tuyendung@gmail.com
set IMAP_PASSWORD=<app password>
python scripts/collect.py --folder "D:/Tuyen dung/CV" --imap --watch --interval 300
python scripts/collect.py --imap --jd data/raw/sample_jd.txt --vi-tri "Backend Developer"   # thu thập rồi đánh giá
python scripts/collect.py --list moi
streamlit run scripts/candidate_portal.py --server.port 8502   # form nộp hồ sơ, ?vi_tri=Tester,Backend%20Developer
```

Google Drive cần `pip install google-api-python-client google-auth` và service account được chia sẻ thư mục.
Ngưỡng (kích thước, số trang, Hamming của simhash / dHash, chu kỳ, số ngày lưu) ở mục `[collector]` của config.toml.

Kịch bản K3 (Bảng 3.8): chia CV gốc cho ba nguồn (trang tuyển dụng giả lập, API, bộ dữ liệu .zip), cài thêm bản sao,
bản lưu lại, tệp hỏng và liên kết 404 có nhãn, chạy Collector rồi so với nhãn:

```
python scripts/eval_collector.py --cv-dir data/k3_cv --extract    # CV thật; --extract cần docling
python scripts/eval_collector.py --synthetic 90                   # thử nhanh bằng CV tổng hợp
```

### Crawl CV trên Internet

Ba nguồn web dùng chung một crawler (`sources/web.py`), tuân thủ robots.txt, không đăng nhập, không vượt CAPTCHA:

| Nguồn | Tham số | Cách đi |
|---|---|---|
| Trang tuyển dụng | `--web URL` | theo liên kết trong cùng tên miền, tối đa `crawl_max_depth` bước |
| Danh sách URL theo dõi | `--url URL`, `--url-file ds.txt` | chỉ các URL đã cho (tệp CV hoặc trang chứa CV) |
| Máy tìm kiếm | `--search "truy vấn"` | URL kết quả của API tìm kiếm (SearXNG / Brave / Google Programmable Search), rồi như danh sách URL |

Chế độ chạy: thủ công (chạy một lần, nút "Thu thập ngay") hoặc đặt lịch: theo chu kỳ (`--watch --interval 3600`) hay
giờ cố định mỗi ngày (`--at 08:00,20:00`; trên giao diện: bật "Tự động thu thập", chọn "Giờ cố định"). Muốn chạy khi
máy không mở giao diện, đăng ký một lần chạy với Task Scheduler của Windows (phải `cd` vào thư mục dự án vì
`data/inbox`, `data/logs` là đường dẫn tương đối):

```
schtasks /Create /SC DAILY /ST 08:00 /TN "CV crawler" /TR "cmd /c cd /d \"<thư mục dự án>\" && \"<python.exe của .venv>\" scripts\collect.py --url-file ds_url.txt"
```

Ngưỡng (mục `[collector]` của config.toml, ghi đè được bằng tham số dòng lệnh hoặc trên giao diện):

| Ngưỡng | Khoá | Mặc định | Hành động khi chạm ngưỡng |
|---|---|---|---|
| Giãn cách giữa hai yêu cầu | `crawl_delay` | 1 s | chờ (lấy max với Crawl-delay của robots.txt) |
| Số trang / số tệp mỗi nguồn mỗi lần | `crawl_max_pages`, `crawl_max_tep` | 50 / 500 | dừng lần chạy của nguồn |
| Độ sâu liên kết | `crawl_max_depth` | 3 | không đi tiếp |
| Thử lại lỗi tạm thời (429, 5xx, hết giờ) | `crawl_retries`, `crawl_backoff` | 2 lần, 2 s ×2 | chờ rồi thử lại, theo Retry-After nếu có |
| Lỗi tải liên tiếp | `crawl_max_loi_lien_tiep` | 5 | dừng nguồn, báo lỗi nguồn |
| Thay đổi trang | `nguong_thay_doi` | 0,02 | lưu phiên bản trang mới, kiểm tra lại các CV trên trang |

Phát hiện thay đổi (`changes.py`): mỗi trang HTML đã crawl được so phần chữ hiển thị với phiên bản đã lưu gần nhất,
δ = 1 − 2M / (|A| + |B|) trên các dòng chữ (M: số dòng khớp, difflib). Trang mới hoặc δ ≥ `nguong_thay_doi` được lưu
thành phiên bản kế tiếp (`data/inbox/pages/`, bảng `trang`, `phien_ban`); thay đổi nhỏ hơn được cộng dồn tới khi vượt
ngưỡng. Tệp CV đã tải chỉ được tải lại khi trang chứa nó thay đổi (hoặc tệp nằm trong danh sách URL) và chỉ chuyển cho
Extractor khi SHA-1 khác lần trước. Xem lại phiên bản và khác biệt ở mục 4 trang Thu thập hồ sơ, hoặc `collect.py --trang`.

Thử không cần Internet bằng trang giả lập (đọc lại thư mục ở mỗi yêu cầu, chép thêm CV vào thư mục để thấy trang thay đổi):

```
python scripts/mock_jobsite.py data/cv_demo_moi/trang_gia_lap --port 8765
python scripts/collect.py --web http://127.0.0.1:8765/ --search backend --search-api searxng --search-url http://127.0.0.1:8765/tim-kiem
```

Giới hạn: crawler đọc HTML tĩnh (không chạy JavaScript), nên trang tải nội dung bằng JavaScript hoặc yêu cầu đăng nhập
(phần lớn kho CV của TopCV, VietnamWorks, LinkedIn) không lấy được CV; trang CV dạng HTML được lưu phiên bản nhưng không
chuyển cho Extractor (chỉ đọc PDF, DOCX, PNG, JPG). CV công khai trên Internet vẫn là dữ liệu cá nhân (Nghị định
13/2023/NĐ-CP): chỉ thu thập khi điều khoản của trang cho phép, dùng cho mục đích nghiên cứu, và để `luu_toi_da_ngay`
xoá đúng hạn (phiên bản trang cũ cũng bị xoá, chỉ giữ phiên bản mới nhất làm mốc so).

## Tệp không kèm trong bản nộp

Do dung lượng lớn, các phần sau được lưu riêng, không nằm trong thư mục nộp:
- `models/vn-embedding-cvjd` và `models/vn-embedding-cvjd-vi`: hai mô hình Vietnamese_Embedding đã tinh chỉnh (mỗi mô hình khoảng 2,3 GB),
  tạo lại bằng `scripts/finetune_embedding.py` (tiếng Việt: thêm `--data-dir` trỏ tới bộ dữ liệu đã dịch bằng `scripts/translate_dataset.py`).
  Trên máy tác giả, `embed_model` / `embed_model_vi` trong `src/cvagents/resources/config.toml` trỏ tới thư mục lưu riêng đó;
  trên máy khác, đặt mô hình vào `models/` rồi sửa hai đường dẫn này.
  Thiếu mô hình tinh chỉnh thì có thể trỏ tới mô hình gốc `AITeamVN/Vietnamese_Embedding`.
- `data/cv_mau` (10 CV mẫu × 6 định dạng) và `data/cv_demo_moi/trang_gia_lap` (trang tuyển dụng giả lập) dùng để chạy minh hoạ.
- Môi trường `.venv`: cài lại theo mục Chạy.

## Lưu ý
- OCR mặc định là EasyOCR (`vi`, `en`). Với các mô hình hiện có, RapidOCR (PP-OCRv5 Latin) làm rơi dấu
  ("Nguyễn Văn" → "Nguyn Vn"). Đổi engine ở `ocr_engine` trong `resources/config.toml`.
- Không có mô hình NER: họ tên và tên tổ chức dùng quy tắc (xem `entities.find_name`, `is_org`).
- Ngưỡng và trọng số trong `config.toml` là giá trị khởi tạo, chưa hiệu chỉnh bằng thực nghiệm.
- docling-parse không mở được tệp tài nguyên khi thư mục cài đặt có chữ có dấu (Windows); khi đó `reader.py` tự
  chuyển sang backend pypdfium2 cho PDF.
- DOCX không có ảnh trang (Docling đọc cấu trúc tài liệu, không qua bố cục trang), nên trang chi tiết chỉ liệt kê khối.
- Gần trùng dùng ngưỡng Hamming 3/64 bit (simhash) và 4/64 bit (dHash): CV sửa vài dòng vẫn được nhận như hồ sơ
  mới; cùng email / SĐT với hồ sơ cũ thì chỉ đánh dấu "Cùng ứng viên" để nhà tuyển dụng tự quyết.
- Crawler chỉ đi trong cùng tên miền với trang bắt đầu và tuân thủ robots.txt; không dùng để thu thập từ trang
  tuyển dụng của bên thứ ba khi điều khoản của trang đó không cho phép.
