"""Tinh chỉnh Vietnamese_Embedding trên bộ Resume–Job Matching bằng Cosine Similarity Loss (2.17).

    python scripts/finetune_embedding.py --out models/vn-embedding-cvjd [--max-train 80000] [--epochs 1]
    python scripts/finetune_embedding.py --data-dir data/cvjd_vi --out models/vn-embedding-cvjd-vi   # bản tiếng Việt

Cần GPU (Google Colab): pip install sentence-transformers datasets accelerate
Tệp này không phụ thuộc gói cvagents nên có thể tải riêng lên Colab.

Chia tập theo candidate_id (80/10/10) để một ứng viên không xuất hiện ở nhiều tập. Mọi bản ghi của bộ dữ liệu
đều là cặp dương, nên mỗi cặp dương được ghép thêm một cặp âm: tin tuyển dụng khác nhóm ngành với ứng viên, nhãn 0.
Kết quả trên tập kiểm tra của mô hình gốc và mô hình sau tinh chỉnh được ghi vào <out>/results.json.
--data-dir đọc cv.csv/job.csv/matches.csv từ thư mục cục bộ (bản dịch do scripts/translate_dataset.py tạo).
Sau khi chạy, đặt embed_model trong resources/config.toml thành thư mục --out.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from datasets import Dataset
from huggingface_hub import hf_hub_download
from sentence_transformers import (
    SentenceTransformer,
    SentenceTransformerTrainer,
    SentenceTransformerTrainingArguments,
)
from sentence_transformers.evaluation import BinaryClassificationEvaluator, EmbeddingSimilarityEvaluator
from sentence_transformers.losses import CosineSimilarityLoss

REPO = "rahulrajpu676/job-resume-matching"
SEED = 42
VAL_PAIRS, TEST_PAIRS = 2000, 5000  # số cặp dùng để đánh giá; mã hoá toàn bộ tập sẽ rất chậm


DATA_DIR: Path | None = None


def _read(name: str, cols: list[str]) -> pd.DataFrame:
    path = DATA_DIR / name if DATA_DIR else hf_hub_download(REPO, name, repo_type="dataset")
    return pd.read_csv(path, usecols=cols)


def sample_negatives(pos: pd.DataFrame, cv: pd.DataFrame, job: pd.DataFrame) -> pd.DataFrame:
    """Một cặp âm cho mỗi cặp dương; ứng viên có nhóm ngành UNKNOWN không sinh cặp âm."""
    rng = np.random.default_rng(SEED)
    cat = pos.candidate_id.map(cv.set_index("candidate_id").inferred_category)
    parts = []
    for c in job.category.unique():
        cand = pos.candidate_id[cat == c].to_numpy()
        pool = job.job_id[job.category != c].to_numpy()
        parts.append(pd.DataFrame({"candidate_id": cand, "job_id": rng.choice(pool, len(cand))}))
    neg = pd.concat(parts).drop_duplicates()
    neg = neg.merge(pos[["candidate_id", "job_id"]], how="left", indicator=True)  # bỏ cặp trùng với cặp dương
    return neg[neg._merge == "left_only"].drop(columns="_merge").assign(final_score=0.0)


def load_splits(max_train: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cv = _read("cv.csv", ["candidate_id", "resume_text", "inferred_category"])
    job = _read("job.csv", ["job_id", "job_text", "category"])
    pos = _read("matches.csv", ["candidate_id", "job_id", "final_score"])
    pairs = pd.concat([pos, sample_negatives(pos, cv, job)])
    pairs = pairs.merge(cv[["candidate_id", "resume_text"]]).merge(job[["job_id", "job_text"]])

    ids = pairs.candidate_id.drop_duplicates().sample(frac=1, random_state=SEED).to_numpy()
    a, b = int(0.8 * len(ids)), int(0.9 * len(ids))

    def take(part: np.ndarray, n: int) -> pd.DataFrame:
        df = pairs[pairs.candidate_id.isin(part)]
        return df.sample(n=min(n, len(df)), random_state=SEED) if n else df

    return take(ids[:a], max_train), take(ids[a:b], VAL_PAIRS), take(ids[b:], TEST_PAIRS)


def evaluate(model: SentenceTransformer, df: pd.DataFrame, name: str) -> dict[str, float]:
    """Spearman giữa cosine và nhãn, cùng Accuracy/Precision/Recall/F1 cho phân loại cặp dương – cặp âm."""
    s1, s2 = df.resume_text.tolist(), df.job_text.tolist()
    out = EmbeddingSimilarityEvaluator(s1, s2, df.final_score.tolist(), name=name)(model)
    out.update(BinaryClassificationEvaluator(s1, s2, (df.final_score > 0).astype(int).tolist(), name=name)(model))
    return {k: float(v) for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--data-dir", help="thư mục chứa cv.csv, job.csv, matches.csv; mặc định tải bản gốc từ Hugging Face")
    ap.add_argument("--base-model", default="AITeamVN/Vietnamese_Embedding")
    ap.add_argument("--max-seq-length", type=int, default=256)
    ap.add_argument("--max-train", type=int, default=80000, help="số cặp huấn luyện (dương + âm); 0 = dùng hết")
    ap.add_argument("--epochs", type=float, default=1)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--grad-accum", type=int, default=1, help="cộng dồn gradient; batch hiệu dụng = batch-size × grad-accum")
    ap.add_argument("--grad-checkpointing", action="store_true", help="tiết kiệm bộ nhớ GPU (T4 16GB), chậm hơn ~30%%")
    args = ap.parse_args()

    global DATA_DIR
    DATA_DIR = Path(args.data_dir) if args.data_dir else None
    train, val, test = load_splits(args.max_train)
    print(f"train {len(train)}  val {len(val)}  test {len(test)}")

    model = SentenceTransformer(args.base_model)
    model.max_seq_length = args.max_seq_length
    results = {"base": evaluate(model, test, "test")}
    if args.grad_checkpointing:  # bật thẳng trên mô hình HF; cờ của Trainer lỗi với một số phiên bản sentence-transformers
        from transformers import PreTrainedModel
        for m in model.modules():
            if isinstance(m, PreTrainedModel):
                m.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
                break

    trainer = SentenceTransformerTrainer(
        model=model,
        args=SentenceTransformerTrainingArguments(
            output_dir=args.out,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            per_device_eval_batch_size=args.batch_size * 2,
            learning_rate=args.lr,
            warmup_ratio=0.1,
            fp16=torch.cuda.is_available(),
            eval_strategy="steps",
            eval_steps=500,
            logging_steps=100,
            save_strategy="no",
            report_to="none",
            seed=SEED,
        ),
        train_dataset=Dataset.from_dict({
            "sentence1": train.resume_text.tolist(),
            "sentence2": train.job_text.tolist(),
            "score": train.final_score.tolist(),
        }),
        loss=CosineSimilarityLoss(model),
        evaluator=EmbeddingSimilarityEvaluator(
            val.resume_text.tolist(), val.job_text.tolist(), val.final_score.tolist(), name="val"),
    )
    trainer.train()

    results["finetuned"] = evaluate(model, test, "test")
    model.save(args.out)
    (Path(args.out) / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
