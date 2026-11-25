"""Dịch bộ Resume–Job Matching (rahulrajpu676/job-resume-matching) sang tiếng Việt để tinh chỉnh Vietnamese_Embedding.

    python scripts/translate_dataset.py --out data/cvjd_vi [--only-matched] [--max-cv 0] [--max-job 0] [--beams 1]

Cần GPU (Google Colab): pip install transformers sentencepiece pandas huggingface_hub
Tệp này không phụ thuộc gói cvagents nên có thể tải riêng lên Colab.

Chỉ dịch cột resume_text (cv.csv) và job_text (job.csv); các cột còn lại, kể cả candidate_id, job_id, nhóm ngành,
được giữ nguyên. matches.csv (cặp và nhãn final_score) được chép nguyên vẹn, nên nhãn không thay đổi.
Văn bản được tách thành câu/đoạn ngắn, mỗi đoạn khác nhau chỉ dịch một lần và được lưu vào <out>/cache.jsonl;
chạy lại cùng lệnh sẽ bỏ qua các đoạn đã dịch (hữu ích khi Colab bị ngắt).
Sau khi chạy: python scripts/finetune_embedding.py --data-dir data/cvjd_vi --out models/vn-embedding-cvjd-vi
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path

import pandas as pd
import torch
from huggingface_hub import hf_hub_download
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

REPO = "rahulrajpu676/job-resume-matching"
MODEL = "vinai/vinai-translate-en2vi-v2"
MAX_CHARS = 400  # đoạn dài hơn được cắt theo dấu câu / khoảng trắng
_SENT = re.compile(r"(?<=[.!?;])\s+")
_HAS_LETTER = re.compile(r"[A-Za-z]")


def segments(text: str) -> list[str]:
    """Tách theo dòng rồi theo câu; câu quá dài cắt tiếp theo khoảng trắng."""
    out = []
    for line in str(text).splitlines():
        for s in _SENT.split(line.strip()):
            while len(s) > MAX_CHARS:
                cut = s.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > 0 else MAX_CHARS
                out.append(s[:cut])
                s = s[cut:].lstrip()
            if s:
                out.append(s)
    return out


def rebuild(text: str, tr: dict[str, str]) -> str:
    """Ghép lại văn bản đã dịch, giữ nguyên ngắt dòng của bản gốc."""
    lines = []
    for line in str(text).splitlines():
        lines.append(" ".join(tr.get(s, s) for s in segments(line)))
    return "\n".join(lines)


class Translator:
    def __init__(self, model: str, beams: int, batch_size: int, cache: Path):
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(model, src_lang="en_XX")
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model).to(self.dev).eval()
        if self.dev == "cuda":
            self.model.half()
        self.vi = self.tok.convert_tokens_to_ids("vi_VN")
        self.beams, self.batch_size, self.cache = beams, batch_size, cache
        self.done: dict[str, str] = {}
        if cache.exists():
            for line in cache.open(encoding="utf-8"):
                r = json.loads(line)
                self.done[r["en"]] = r["vi"]

    @torch.inference_mode()
    def _batch(self, texts: list[str]) -> list[str]:
        enc = self.tok(texts, padding=True, truncation=True, max_length=256, return_tensors="pt").to(self.dev)
        gen = self.model.generate(**enc, decoder_start_token_id=self.vi, num_beams=self.beams,
                                  max_new_tokens=320, early_stopping=True)
        return self.tok.batch_decode(gen, skip_special_tokens=True)

    def run(self, segs: set[str]) -> dict[str, str]:
        todo = sorted((s for s in segs if s not in self.done and _HAS_LETTER.search(s)), key=len)  # độ dài gần nhau → ít padding
        print(f"đoạn cần dịch: {len(todo)} (đã có trong cache: {len(segs) - len(todo)})")
        t0 = time.time()
        with self.cache.open("a", encoding="utf-8") as f:
            for i in range(0, len(todo), self.batch_size):
                part = todo[i:i + self.batch_size]
                for en, vi in zip(part, self._batch(part)):
                    self.done[en] = vi
                    f.write(json.dumps({"en": en, "vi": vi}, ensure_ascii=False) + "\n")
                f.flush()
                n = i + len(part)
                if n % (self.batch_size * 50) < self.batch_size or n == len(todo):
                    rate = n / (time.time() - t0)
                    print(f"  {n}/{len(todo)}  {rate:.1f} đoạn/s  còn ~{(len(todo) - n) / rate / 60:.0f} phút")
        return self.done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--only-matched", action="store_true",
                    help="chỉ dịch CV/JD xuất hiện trong matches.csv (ứng viên không có cặp nào không được dùng khi tinh chỉnh)")
    ap.add_argument("--max-cv", type=int, default=0, help="giới hạn số CV (0 = tất cả), lấy ngẫu nhiên seed 42")
    ap.add_argument("--max-job", type=int, default=0, help="giới hạn số JD (0 = tất cả)")
    ap.add_argument("--beams", type=int, default=1, help="1 = greedy (nhanh); 4–5 cho chất lượng tốt hơn, chậm hơn ~3 lần")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--dry-run", action="store_true", help="chỉ đếm số đoạn cần dịch, không tải mô hình")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    paths = {n: hf_hub_download(REPO, n, repo_type="dataset") for n in ("cv.csv", "job.csv", "matches.csv")}
    cv, job, pos = (pd.read_csv(paths[n]) for n in ("cv.csv", "job.csv", "matches.csv"))
    print(f"gốc: cv {len(cv)}  job {len(job)}  matches {len(pos)}")

    if args.only_matched:
        cv = cv[cv.candidate_id.isin(pos.candidate_id)]
    if args.max_cv:
        cv = cv.sample(n=min(args.max_cv, len(cv)), random_state=42)
    if args.max_job:
        job = job.sample(n=min(args.max_job, len(job)), random_state=42)
    pos = pos[pos.candidate_id.isin(cv.candidate_id) & pos.job_id.isin(job.job_id)]
    cv, job = cv.fillna({"resume_text": ""}), job.fillna({"job_text": ""})

    segs = {s for t in cv.resume_text for s in segments(t)} | {s for t in job.job_text for s in segments(t)}
    chars = sum(map(len, segs))
    print(f"sẽ dịch: cv {len(cv)}  job {len(job)}  matches {len(pos)}  — {len(segs)} đoạn khác nhau, {chars:,} ký tự")
    if args.dry_run:
        return

    tr = Translator(args.model, args.beams, args.batch_size, out / "cache.jsonl").run(segs)
    cv.assign(resume_text_en=cv.resume_text, resume_text=cv.resume_text.map(lambda t: rebuild(t, tr))) \
        .to_csv(out / "cv.csv", index=False)
    job.assign(job_text_en=job.job_text, job_text=job.job_text.map(lambda t: rebuild(t, tr))) \
        .to_csv(out / "job.csv", index=False)
    if len(pos) == len(pd.read_csv(paths["matches.csv"])):
        shutil.copy(paths["matches.csv"], out / "matches.csv")
    else:
        pos.to_csv(out / "matches.csv", index=False)

    # kiểm tra: nhãn và khoá nối không đổi so với bản gốc
    m = pd.read_csv(out / "matches.csv")
    assert m.candidate_id.isin(pd.read_csv(out / "cv.csv").candidate_id).all()
    assert m.job_id.isin(pd.read_csv(out / "job.csv").job_id).all()
    assert len(m) == len(pos) and (m.final_score.to_numpy() == pos.final_score.to_numpy()).all()
    print(f"xong → {out}/cv.csv, job.csv, matches.csv")


if __name__ == "__main__":
    main()
