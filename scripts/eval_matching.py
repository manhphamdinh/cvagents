"""Nhóm thử nghiệm A – đối sánh CV–JD: từ baseline thêm dần từng thành phần của hệ thống, trên cùng một tập kiểm tra.

    python scripts/eval_matching.py --lang en --out results/A_en
    python scripts/eval_matching.py --lang vi --data-dir cvjd_vi --out results/A_vi
    python scripts/eval_matching.py --lang vi --data-dir cvjd_vi --cap kho --configs KB3,KB4 --out results/A_vi_kho

--cap kho: cặp dương là cặp medium/strong của matches.csv, cặp âm là tin cùng nhóm ngành mà ứng viên không được ghép
(cặp âm khó). Với --cap goc (mặc định, như khi tinh chỉnh) mọi cặp trong matches.csv là dương (final_score ≥ 0,43) và
cặp âm là tin khác ngành, nên ngưỡng học được chỉ tách "cùng ngành / khác ngành" (τS ≈ 0,31, gần như mọi ứng viên
cùng ngành đều qua). Trọng số và τS trong config.toml nên lấy từ lần chạy --cap kho (results/<out>/trong_so_kb4.json
và cột nguong của KB4 trong ket_qua.csv).

Baseline tái hiện từ bài báo (không học, chạy trên CPU trong vài phút; thêm vào kết quả có sẵn: --configs BM25,VM):
    BM25 Okapi BM25 (k1=1,5; b=0,75; IDF và độ dài trung bình lấy từ JD của tập huấn luyện), truy vấn = các từ của CV,
          tài liệu = JD – baseline từ vựng được Vanetik & Kogan (2023) dùng để so sánh
    VM   Vector Matching của Vanetik & Kogan (2023), cấu hình tốt nhất của bài: vector n-gram ký tự n = 1–3 trên văn bản
          đã bỏ chữ số và viết thường, điểm = −khoảng cách L1. Khác bài báo: (i) vector được chuẩn hoá về tần suất tương
          đối (tổng = 1) để CV dài và JD ngắn so được với nhau; (ii) không tóm tắt JD bằng BERT (JD trong bộ dữ liệu đã ngắn).
    [N. Vanetik and G. Kogan, "Job vacancy ranking with sentence embeddings, keywords, and named entities,"
     Information, vol. 14, no. 8, Art. no. 468, 2023, doi: 10.3390/info14080468]

Cấu hình (mỗi cấu hình thêm đúng một thành phần so với cấu hình trước):
    KB1  TF-IDF + cosine trên toàn văn CV và JD                     (baseline từ vựng)
    KB2  Vietnamese_Embedding gốc, cosine trên toàn văn             (baseline ngữ nghĩa)
    KB3  + tinh chỉnh trên dữ liệu CV–JD (vn-embedding-cvjd / -vi)
    KB4  + đối sánh theo từng yêu cầu và điểm tổng hợp = hệ thống đầy đủ: CV → CVProfile (CV_Extractor, bước 3–8),
          JD → tập yêu cầu R, mỗi yêu cầu lấy bằng chứng có cosine lớn nhất; điểm tổng S từ năm thành phần
          (bắt buộc, ưu tiên, ngữ nghĩa, kinh nghiệm, học vấn)

Tập dữ liệu được chia giống hệt scripts/finetune_embedding.py (cùng hạt giống, theo candidate_id 80/10/10, mỗi cặp dương
ghép một cặp âm khác nhóm ngành), nên mọi cấu hình và cả hai ngôn ngữ dùng đúng các cặp kiểm tra mà mô hình tinh chỉnh
chưa từng thấy. Kết quả được lưu sau mỗi cấu hình; chạy lại cùng lệnh sẽ bỏ qua cấu hình đã xong (--redo để chạy lại).
Ngưỡng phân loại của mỗi cấu hình được chọn trên tập kiểm định (F1 lớn nhất) rồi cố định trên tập kiểm tra.

Độ đo: Spearman giữa điểm và nhãn final_score; Accuracy, Precision, Recall, F1, MCC tại ngưỡng đã chọn; AP và ROC-AUC
(không phụ thuộc ngưỡng); thời gian trung bình mỗi cặp. Kết quả: <out>/ket_qua.csv, ket_qua.json, diem_tung_cap.csv.

Cần: pip install sentence-transformers scikit-learn pandas huggingface_hub  (và gói cvagents: thư mục src/ của dự án)
Chạy thử nhanh không cần GPU/mô hình: --embedder hashing --max-test 200 --max-val 200
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REPO = "rahulrajpu676/job-resume-matching"
SEED = 42
VAL_PAIRS, TEST_PAIRS = 2000, 5000
CONFIGS = ["BM25", "VM", "KB1", "KB2", "KB3", "KB4"]
NAMES = {
    "BM25": "Okapi BM25 (baseline trong Vanetik & Kogan, 2023)",
    "VM": "Vector Matching: n-gram ký tự 1–3 + L1 (Vanetik & Kogan, 2023)",
    "KB1": "TF-IDF + cosine (baseline từ vựng)",
    "KB2": "Vietnamese_Embedding gốc, toàn văn (baseline ngữ nghĩa)",
    "KB3": "+ tinh chỉnh trên dữ liệu CV–JD",
    "KB4": "+ đối sánh theo yêu cầu, điểm tổng hợp (đầy đủ)",
}
DATA_DIR: Path | None = None
PAIRING = "goc"  # goc: dương = mọi cặp trong matches.csv, âm = khác ngành; kho: dương = medium/strong, âm = cùng ngành
KB4_VERSION = 4  # 2: tách bản ghi phẳng thành các mục; 3: thêm thành phần toàn văn, trọng số học trên tập kiểm định;
                 # 4: thành phần toàn văn tính trên văn bản gốc của bản ghi, không trên bản đã dựng lại
PARTS = ["toan_van", "bat_buoc", "uu_tien", "ngu_nghia", "kinh_nghiem", "hoc_van"]
N_WEIGHT_SAMPLES = 5000
# bộ trọng số khởi tạo chọn tay (trước khi học), giữ cố định để kết quả "trọng số khởi tạo" tái lập được
HAND_WEIGHTS = {"toan_van": 0.30, "bat_buoc": 0.40, "uu_tien": 0.10, "ngu_nghia": 0.25, "kinh_nghiem": 0.15, "hoc_van": 0.10}


# ---------------------------------------------------------------- bản ghi phẳng → văn bản có mục
# Trong bộ dữ liệu, mỗi CV/JD là MỘT dòng dạng "Vị trí: … Kỹ năng: a, b … Giáo dục: … Kinh nghiệm: …". CV_Extractor được
# thiết kế cho tài liệu có tiêu đề mục trên dòng riêng, nên trước KB4 bản ghi được dựng lại thành các dòng như một CV/JD thật.
_CV_LAB = {
    "vị trí": None, "position": None, "chức danh": None,
    "mục tiêu": "Mục tiêu", "objective": "Mục tiêu", "career objective": "Mục tiêu",
    "kỹ năng": "Kỹ năng", "skills": "Kỹ năng",
    "giáo dục": "Học vấn", "học vấn": "Học vấn", "education": "Học vấn",
    "kinh nghiệm": "Kinh nghiệm", "experience": "Kinh nghiệm",
    "trách nhiệm": "Kinh nghiệm", "responsibilities": "Kinh nghiệm",
    "chứng chỉ": "Chứng chỉ", "certifications": "Chứng chỉ", "certification": "Chứng chỉ",
}
_JD_LAB = {
    "chức vụ": "title", "vị trí": "title", "job title": "title", "title": "title", "position": "title",
    "chuyên ngành": "cat", "category": "cat", "ngành": "cat",
    "kỹ năng": "skills", "skills": "skills", "yêu cầu": "skills", "requirements": "skills",
    "kinh nghiệm": "exp", "experience": "exp", "học vấn": "edu", "education": "edu", "trình độ": "edu",
    "mô tả": "desc", "description": "desc", "job description": "desc",
}

def _split(text, labels):
    pat = re.compile(r"(?<![\w])(" + "|".join(sorted(map(re.escape, labels), key=len, reverse=True)) + r")\s*:", re.I)
    ms = list(pat.finditer(text))
    if not ms:
        return [(None, text.strip())]
    out = [(None, text[:ms[0].start()].strip())] if text[:ms[0].start()].strip() else []
    for m, n in zip(ms, ms[1:] + [None]):
        out.append((m.group(1).lower(), text[m.end(): n.start() if n else len(text)].strip()))
    return out

def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"[\[\]'\"]", " ", s)).strip(" ,;•")

def _items(s):
    return [x for x in (_clean(p) for p in re.split(r"[,;•\n]|\]\s*\[", s)) if x]

def _sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?;•])\s+|\s•\s|\n", s) if len(x.strip()) > 2]

def cv_lines(text):
    """Bản ghi CV phẳng (mọi mục trên một dòng 'Nhãn: ...') → các dòng có tiêu đề mục như một CV thật."""
    text = str(text)
    if len(text.splitlines()) > 3:
        return [l for l in text.splitlines() if l.strip()]
    lines = []
    for lab, body in _split(text, _CV_LAB):
        if not body:
            continue
        head = _CV_LAB.get(lab) if lab else None
        if lab and head is None:            # vị trí ứng tuyển → dòng tiêu đề đầu CV
            lines.insert(0, _clean(body)); continue
        if head:
            lines.append(head)
        if head == "Kỹ năng":
            lines += ["• " + x for x in _items(body)]
        else:
            lines += ["• " + _clean(x) for x in _sentences(re.sub(r"\]\s*\[", "; ", body)) if _clean(x)]
    return lines

def jd_text(text):
    """Bản ghi JD phẳng → văn bản JD có mục 'Yêu cầu' (kỹ năng, kinh nghiệm, học vấn) và 'Mô tả công việc'."""
    text = str(text)
    if len(text.splitlines()) > 3:
        return text
    parts = _split(text, _JD_LAB)
    title, req, desc = "", [], []
    for lab, body in parts:
        kind = _JD_LAB.get(lab) if lab else None
        if kind == "title":
            title = _clean(body)
        elif kind == "skills":
            req += _items(body)
        elif kind in ("exp", "edu"):
            req.append(_clean(body))
        elif kind == "desc" or kind is None:
            desc += _sentences(body)
    if not req:  # JD không có nhãn kỹ năng: dùng các câu mô tả làm yêu cầu
        req, desc = desc, []
    return "\n".join(([title] if title else []) + ["Yêu cầu"] + ["- " + r for r in req]
                     + (["Mô tả công việc"] + desc if desc else []))



# ---------------------------------------------------------------- chia tập: giống hệt finetune_embedding.py
def _read(name: str, cols: list[str]) -> pd.DataFrame:
    if DATA_DIR:
        path = DATA_DIR / name
    else:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(REPO, name, repo_type="dataset")
    return pd.read_csv(path, usecols=cols)


def sample_negatives(pos: pd.DataFrame, cv: pd.DataFrame, job: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    cat = pos.candidate_id.map(cv.set_index("candidate_id").inferred_category)
    parts = []
    for c in job.category.unique():
        cand = pos.candidate_id[cat == c].to_numpy()
        pool = job.job_id[job.category != c].to_numpy()
        parts.append(pd.DataFrame({"candidate_id": cand, "job_id": rng.choice(pool, len(cand))}))
    neg = pd.concat(parts).drop_duplicates()
    neg = neg.merge(pos[["candidate_id", "job_id"]], how="left", indicator=True)
    return neg[neg._merge == "left_only"].drop(columns="_merge").assign(final_score=0.0)


def hard_negatives(pos: pd.DataFrame, all_pos: pd.DataFrame, cv: pd.DataFrame, job: pd.DataFrame) -> pd.DataFrame:
    """Cặp âm khó: mỗi cặp dương ghép một tin CÙNG nhóm ngành với ứng viên mà ứng viên không được ghép trong
    matches.csv (all_pos, kể cả cặp weak). Cặp âm khác ngành (sample_negatives) chỉ dạy được "cùng ngành hay không";
    khi dùng thật mọi người ứng tuyển một vị trí thường cùng ngành, nên ngưỡng phải tách được trong cùng một ngành."""
    rng = np.random.default_rng(SEED)
    cat = cv.set_index("candidate_id").inferred_category
    matched = all_pos.groupby("candidate_id").job_id.apply(set)
    by_cat = {c: g.job_id.to_numpy() for c, g in job.groupby("category")}
    rows = []
    for c in pos.candidate_id:
        pool = by_cat.get(cat.get(c))
        if pool is None:  # nhóm ngành UNKNOWN: không có tin cùng ngành
            continue
        for _ in range(20):  # bốc lại nếu trúng tin đã ghép
            j = rng.choice(pool)
            if j not in matched[c]:
                rows.append((c, j))
                break
    return pd.DataFrame(rows, columns=["candidate_id", "job_id"]).drop_duplicates().assign(final_score=0.0)


def load_splits(max_train: int = 80000) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cv = _read("cv.csv", ["candidate_id", "resume_text", "inferred_category"])
    job = _read("job.csv", ["job_id", "job_text", "category"])
    m = _read("matches.csv", lambda c: c in ("candidate_id", "job_id", "final_score", "label_strength"))
    pos = m[["candidate_id", "job_id", "final_score"]]
    pairs = pd.concat([pos, sample_negatives(pos, cv, job)])
    pairs = pairs.merge(cv[["candidate_id", "resume_text"]]).merge(job[["job_id", "job_text"]])

    # chia theo ứng viên đúng như finetune_embedding.py (tính trên cách dựng cặp gốc), nên dù dựng cặp kiểu nào thì
    # tập kiểm định / kiểm tra vẫn chỉ gồm ứng viên mà mô hình tinh chỉnh chưa thấy
    ids = pairs.candidate_id.drop_duplicates().sample(frac=1, random_state=SEED).to_numpy()
    a, b = int(0.8 * len(ids)), int(0.9 * len(ids))
    if PAIRING == "kho":
        strong = m[m.label_strength.isin(["medium_positive", "strong_positive"])][pos.columns]
        pairs = pd.concat([strong, hard_negatives(strong, pos, cv, job)])
        pairs = pairs.merge(cv[["candidate_id", "resume_text"]]).merge(job[["job_id", "job_text"]])

    def take(part: np.ndarray, n: int) -> pd.DataFrame:
        df = pairs[pairs.candidate_id.isin(part)]
        return df.sample(n=min(n, len(df)), random_state=SEED) if n else df

    return take(ids[:a], max_train), take(ids[a:b], VAL_PAIRS), take(ids[b:], TEST_PAIRS)


# ---------------------------------------------------------------- các cấu hình
def cosine_rows(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-12, None)
    b = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-12, None)
    return (a * b).sum(axis=1)


class Scorer:
    def __init__(self, args, train: pd.DataFrame):
        self.args = args
        self.train = train
        self._tfidf = None
        self._bm25 = None
        self._vm = None
        self._models: dict[str, object] = {}
        self._profiles: dict[str, object] = {}
        self._jds: dict[str, object] = {}
        self._agents: dict[str, object] = {}
        self.no_req = 0

    # KB1
    def tfidf(self, cvs: list[str], jds: list[str]) -> np.ndarray:
        if self._tfidf is None:
            from sklearn.feature_extraction.text import TfidfVectorizer
            corpus = pd.concat([self.train.resume_text, self.train.job_text]).drop_duplicates().tolist()
            self._tfidf = TfidfVectorizer(sublinear_tf=True, min_df=2, ngram_range=(1, 2), max_features=200000).fit(corpus)
        a, b = self._tfidf.transform(cvs), self._tfidf.transform(jds)
        return np.asarray(a.multiply(b).sum(axis=1)).ravel()  # vector TF-IDF đã chuẩn hoá L2 → tích vô hướng = cosine

    # baseline BM25
    def bm25(self, cvs: list[str], jds: list[str], k1: float = 1.5, b: float = 0.75) -> np.ndarray:
        from sklearn.feature_extraction.text import CountVectorizer
        if self._bm25 is None:
            docs = self.train.job_text.astype(str).drop_duplicates().tolist()
            cv_ = CountVectorizer(lowercase=True, token_pattern=r"(?u)\b\w+\b").fit(
                docs + self.train.resume_text.astype(str).drop_duplicates().tolist())
            D = cv_.transform(docs)
            df = np.asarray((D > 0).sum(axis=0)).ravel()
            n = D.shape[0]
            idf = np.log((n - df + 0.5) / (df + 0.5) + 1.0)
            self._bm25 = (cv_, idf, float(D.sum(axis=1).mean()))
        cv_, idf, avgdl = self._bm25
        Q = (cv_.transform(cvs) > 0).astype(float)          # mỗi từ của truy vấn (CV) tính một lần
        D = cv_.transform(jds).astype(float).tocsr()
        dl = np.asarray(D.sum(axis=1)).ravel()
        D = D.tocoo()
        denom = D.data + k1 * (1 - b + b * dl[D.row] / avgdl)
        from scipy.sparse import csr_matrix
        T = csr_matrix((D.data * (k1 + 1) / denom * idf[D.col], (D.row, D.col)), shape=D.shape)
        return np.asarray(Q.multiply(T).sum(axis=1)).ravel()

    # baseline VM (Vanetik & Kogan, 2023)
    def vm(self, cvs: list[str], jds: list[str]) -> np.ndarray:
        from sklearn.feature_extraction.text import CountVectorizer
        from sklearn.preprocessing import normalize
        if self._vm is None:
            prep = lambda t: re.sub(r"\d+", " ", str(t).lower())  # noqa: E731  bỏ chữ số, viết thường như bài báo
            corpus = pd.concat([self.train.resume_text, self.train.job_text]).astype(str).drop_duplicates().tolist()
            self._vm = CountVectorizer(analyzer="char", ngram_range=(1, 3), preprocessor=prep, min_df=2,
                                       max_features=300000).fit(corpus)
        A = normalize(self._vm.transform(cvs).astype(float), norm="l1")
        B = normalize(self._vm.transform(jds).astype(float), norm="l1")
        return -np.asarray(abs(A - B).sum(axis=1)).ravel()   # L1 nhỏ = giống nhau → điểm cao

    def embedder(self, name: str):
        if name not in self._models:
            from cvagents.analysis.embedder import HashingEmbedder, SentenceTransformerEmbedder
            self._models[name] = (HashingEmbedder() if self.args.embedder == "hashing"
                                  else SentenceTransformerEmbedder(name, self.args.max_seq_length))
        return self._models[name]

    # KB2, KB3
    def whole_text(self, model: str, cvs: list[str], jds: list[str]) -> np.ndarray:
        e = self.embedder(model)
        return cosine_rows(e.encode(cvs), e.encode(jds))

    # KB4
    def profile(self, text: str):
        if text not in self._profiles:
            from cvagents.extractor.agent import CVExtractorAgent
            from cvagents.extractor.models import Block, RawDocument
            raw = RawDocument(blocks=[Block(text=ln) for ln in cv_lines(text)])
            self._profiles[text] = CVExtractorAgent().process(raw, None)
        return self._profiles[text]

    def jd(self, text: str):
        if text not in self._jds:
            from cvagents.analysis.jd_parser import parse_jd
            self._jds[text] = parse_jd(jd_text(text))
        return self._jds[text]

    def parts(self, model: str, cvs: list[str], jds: list[str]) -> np.ndarray:
        """Ma trận (số cặp × 6) điểm thành phần của CV/JD_Analysis; NaN = JD không có yêu cầu cho thành phần đó."""
        if model not in self._agents:
            from cvagents.analysis.agent import CVJDAnalysisAgent
            self._agents[model] = CVJDAnalysisAgent(embedder=self.embedder(model))
        agent = self._agents[model]
        out = np.full((len(cvs), len(PARTS)), np.nan)
        t0 = time.perf_counter()
        for i, (c, j) in enumerate(zip(cvs, jds)):
            if i and i % 250 == 0:
                el = time.perf_counter() - t0
                print(f"  KB4 {i}/{len(cvs)} cặp, còn khoảng {el / i * (len(cvs) - i) / 60:.0f} phút", flush=True)
            # bước dựng lại bản ghi (cv_lines, jd_text) chỉ phục vụ trích xuất; tương đồng toàn văn tính trên văn bản gốc
            # như khi hệ thống nhận đúng văn bản đó, để không đưa định dạng nhân tạo vào thành phần này
            prof = self.profile(c).model_copy(update={"van_ban_goc": str(c)})
            jd = self.jd(j).model_copy(update={"van_ban_goc": str(j)})
            rep = agent.analyze(prof, jd)
            if not (set(rep.diem_thanh_phan) - {"toan_van"}):
                self.no_req += 1
            for k, name in enumerate(PARTS):
                if name in rep.diem_thanh_phan:
                    out[i, k] = rep.diem_thanh_phan[name]
        return out

    def score(self, cfg: str, df: pd.DataFrame) -> np.ndarray:
        cvs, jds = df.resume_text.astype(str).tolist(), df.job_text.astype(str).tolist()
        if cfg == "BM25":
            return self.bm25(cvs, jds)
        if cfg == "VM":
            return self.vm(cvs, jds)
        if cfg == "KB1":
            return self.tfidf(cvs, jds)
        if cfg == "KB2":
            return self.whole_text(self.args.base_model, cvs, jds)
        if cfg == "KB3":
            return self.whole_text(self.args.ft_model, cvs, jds)
        return self.parts(self.args.ft_model, cvs, jds)


def total_scores(P: np.ndarray, W: np.ndarray) -> np.ndarray:
    """Điểm tổng S như scoring.total_score, cho nhiều cặp (P: n × 6) và nhiều bộ trọng số (W: m × 6) → n × m.
    Trọng số được chuẩn hoá lại trên các thành phần có mặt của từng cặp."""
    have = ~np.isnan(P)
    num = np.nan_to_num(P) @ W.T
    den = have.astype(float) @ W.T
    return np.where(den > 0, num / np.clip(den, 1e-12, None), 0.0)


def fit_weights(P: np.ndarray, y: np.ndarray, seed: int = SEED) -> tuple[dict[str, float], float]:
    """Chọn bộ trọng số (không âm, tổng 1) cho AP lớn nhất trên tập kiểm định: tìm kiếm ngẫu nhiên trên đơn hình
    (phân phối Dirichlet), kèm bộ trọng số chọn tay trong config.toml làm một ứng viên."""
    from sklearn.metrics import average_precision_score
    rng = np.random.default_rng(seed)
    hand = np.array([[HAND_WEIGHTS[k] for k in PARTS]])
    W = np.vstack([hand / hand.sum(), np.eye(len(PARTS)), rng.dirichlet(np.ones(len(PARTS)), N_WEIGHT_SAMPLES)])
    S = total_scores(P, W)
    ap = np.array([average_precision_score(y, S[:, i]) for i in range(len(W))])
    best = int(np.argmax(ap))
    return {k: round(float(v), 4) for k, v in zip(PARTS, W[best])}, float(ap[best])


# ---------------------------------------------------------------- độ đo
def best_threshold(scores: np.ndarray, y: np.ndarray) -> float:
    from sklearn.metrics import precision_recall_curve
    p, r, t = precision_recall_curve(y, scores)
    f = 2 * p * r / np.clip(p + r, 1e-12, None)
    return float(t[int(np.argmax(f[:-1]))]) if len(t) else 0.5


def metrics(scores: np.ndarray, df: pd.DataFrame, thr: float) -> dict[str, float]:
    from scipy.stats import spearmanr
    from sklearn.metrics import (accuracy_score, average_precision_score, f1_score, matthews_corrcoef,
                                 precision_score, recall_score, roc_auc_score)
    y = (df.final_score.to_numpy() > 0).astype(int)
    pred = (scores >= thr).astype(int)
    return {
        "spearman": float(spearmanr(scores, df.final_score).correlation),
        "accuracy": accuracy_score(y, pred), "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred), "f1": f1_score(y, pred), "mcc": matthews_corrcoef(y, pred),
        "ap": average_precision_score(y, scores), "roc_auc": roc_auc_score(y, scores), "nguong": thr,
    }


def main() -> None:
    global DATA_DIR, PAIRING
    ap = argparse.ArgumentParser(description="Nhóm thử nghiệm A – đối sánh CV–JD (BM25, VM, KB1–KB4)")
    ap.add_argument("--lang", choices=["en", "vi"], required=True, help="chỉ dùng để đặt tên mô hình mặc định và kết quả")
    ap.add_argument("--data-dir", help="thư mục cv.csv/job.csv/matches.csv (bản tiếng Việt); bỏ trống = tải bản gốc")
    ap.add_argument("--base-model", default="AITeamVN/Vietnamese_Embedding")
    ap.add_argument("--ft-model", help="mô hình đã tinh chỉnh; mặc định models/vn-embedding-cvjd[-vi]")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--max-seq-length", type=int, default=256)
    ap.add_argument("--max-val", type=int, default=VAL_PAIRS)
    ap.add_argument("--max-test", type=int, default=TEST_PAIRS)
    ap.add_argument("--embedder", choices=["st", "hashing"], default="st", help="hashing: chạy thử không cần mô hình")
    ap.add_argument("--out", required=True)
    ap.add_argument("--redo", action="store_true", help="chạy lại cả các cấu hình đã có kết quả trong --out")
    ap.add_argument("--cap", choices=["goc", "kho"], default="goc",
                    help="cách dựng cặp: goc (âm khác ngành, như khi tinh chỉnh) | kho (dương medium/strong, âm cùng ngành)")
    args = ap.parse_args()
    PAIRING = args.cap
    DATA_DIR = Path(args.data_dir) if args.data_dir else None
    root = Path(__file__).resolve().parents[1]
    args.ft_model = args.ft_model or str(root / "models" / ("vn-embedding-cvjd-vi" if args.lang == "vi" else "vn-embedding-cvjd"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    train, val, test = load_splits()
    val, test = val.head(args.max_val), test.head(args.max_test)
    print(f"train {len(train)}  val {len(val)}  test {len(test)}  (cặp dương trong test: {(test.final_score > 0).sum()})")
    scorer = Scorer(args, train)
    rows, per_pair = [], test[["candidate_id", "job_id", "final_score"]].reset_index(drop=True)
    prev = out / "ket_qua.json"
    if prev.exists() and not args.redo:  # chạy tiếp: giữ các cấu hình đã xong trên đúng tập kiểm tra này
        old = json.loads(prev.read_text(encoding="utf-8"))
        old_pairs = pd.read_csv(out / "diem_tung_cap.csv")
        if old["n_test"] == len(test) and old["n_val"] == len(val) and old_pairs[["candidate_id", "job_id"]].equals(
                per_pair[["candidate_id", "job_id"]].reset_index(drop=True)):
            rows = [r for r in old["ket_qua"] if r["cau_hinh"] in CONFIGS  # bỏ tên cấu hình cũ (A0–A4)
                    and (r["cau_hinh"] != "KB4" or r.get("phien_ban") == KB4_VERSION)]  # và KB4 bản cũ
            per_pair = per_pair.reset_index(drop=True)
            for r in rows:
                per_pair[r["cau_hinh"]] = old_pairs[r["cau_hinh"]]
            print(f"Đã có kết quả {', '.join(r['cau_hinh'] for r in rows)} trong {out}; bỏ qua (dùng --redo để chạy lại)")
    done = {r["cau_hinh"] for r in rows}

    def save() -> pd.DataFrame:
        order = {c: i for i, c in enumerate(CONFIGS)}
        rows.sort(key=lambda r: order[r["cau_hinh"]])
        cols = ["cau_hinh", "mo_ta", "spearman", "accuracy", "precision", "recall", "f1", "mcc", "ap", "roc_auc", "nguong",
                "giay_moi_cap", "ty_le_jd_khong_co_yeu_cau"]
        table = pd.DataFrame(rows)[cols]
        table.to_csv(out / "ket_qua.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
        per_pair[["candidate_id", "job_id", "final_score"] + [r["cau_hinh"] for r in rows]].to_csv(
            out / "diem_tung_cap.csv", index=False)
        (out / "ket_qua.json").write_text(json.dumps({"lang": args.lang, "ft_model": args.ft_model, "n_val": len(val),
                                                      "n_test": len(test), "ket_qua": rows}, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        return table

    for cfg in [c.strip() for c in args.configs.split(",") if c.strip()]:
        if cfg in done:
            continue
        print(f"\n== {cfg}: {NAMES[cfg]}")
        y_val = (val.final_score.to_numpy() > 0).astype(int)
        extra = {}
        if cfg == "KB4":
            P_val = scorer.score(cfg, val)
            w, ap_val = fit_weights(P_val, y_val)
            W = np.array([[w[k] for k in PARTS]])
            hand = np.array([[HAND_WEIGHTS[k] for k in PARTS]])
            thr = best_threshold(total_scores(P_val, W)[:, 0], y_val)
            thr_hand = best_threshold(total_scores(P_val, hand)[:, 0], y_val)
            print("Trọng số học trên tập kiểm định:", w, f"(AP kiểm định {ap_val:.4f})")
        else:
            thr = best_threshold(scorer.score(cfg, val), y_val)
        scorer.no_req = 0
        t0 = time.perf_counter()
        s = scorer.score(cfg, test)
        dt = (time.perf_counter() - t0) / len(test)
        if cfg == "KB4":
            P_test = s
            s = total_scores(P_test, W)[:, 0]
            m_hand = metrics(total_scores(P_test, hand)[:, 0], test, thr_hand)
            m_only = {}  # từng thành phần dùng riêng (không cần ngưỡng: AP, Spearman) – để phân tích đóng góp
            for k, name in enumerate(PARTS):
                col = P_test[:, k]
                if np.isfinite(col).sum() > 10:
                    from sklearn.metrics import average_precision_score
                    from scipy.stats import spearmanr
                    ok = np.isfinite(col)
                    yk = (test.final_score.to_numpy()[ok] > 0).astype(int)
                    m_only[name] = {"ty_le_co": float(ok.mean()),
                                    "ap": float(average_precision_score(yk, col[ok])) if 0 < yk.sum() < len(yk) else None,
                                    "spearman": float(spearmanr(col[ok], test.final_score.to_numpy()[ok]).correlation)}
            extra = {"trong_so": w, "ap_kiem_dinh": ap_val,
                     "trong_so_chon_tay": {k: float(v) for k, v in zip(PARTS, hand[0])},
                     "ket_qua_trong_so_chon_tay": {k: v for k, v in m_hand.items()},
                     "tung_thanh_phan": m_only}
            (out / "trong_so_kb4.json").write_text(json.dumps(extra, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Với trọng số chọn tay:", {k: round(v, 4) for k, v in m_hand.items()})
        m = metrics(s, test, thr)
        m.update({"cau_hinh": cfg, "mo_ta": NAMES[cfg], "giay_moi_cap": dt,
                  "ty_le_jd_khong_co_yeu_cau": scorer.no_req / len(test) if cfg == "KB4" else None})
        if cfg == "KB4":
            m["phien_ban"] = KB4_VERSION
            m["trong_so"] = extra["trong_so"]
        rows.append(m)
        per_pair[cfg] = np.asarray(s)
        save()  # lưu sau mỗi cấu hình: mất phiên Colab giữa chừng thì chạy lại đúng lệnh cũ để làm tiếp
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()})

    table = save()
    print("\n" + table.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(f"\nĐã lưu {out / 'ket_qua.csv'}")


if __name__ == "__main__":
    main()
