"""Mã hoá văn bản thành vector L2-chuẩn hoá; có bộ nhớ đệm để mỗi chuỗi chỉ mã hoá một lần (mục 2.4.2.4)."""
from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np

from cvagents.common.config import load_config
from cvagents.common.text import norm_key


def model_for_lang(lang: str) -> str:
    """Tiếng Việt → embed_model_vi; tiếng Anh → embed_model (mô hình tinh chỉnh trên dữ liệu CV–JD tiếng Anh)."""
    cfg = load_config()["analysis"]
    return cfg["embed_model_vi"] if lang == "vi" else cfg["embed_model"]


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.clip(n, 1e-12, None)


class _Cached:
    def __init__(self):
        self._cache: dict[str, np.ndarray] = {}

    def _encode_new(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def encode(self, texts: list[str]) -> np.ndarray:
        missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if missing:
            for t, v in zip(missing, _l2(self._encode_new(missing))):
                self._cache[t] = v
        return np.stack([self._cache[t] for t in texts]) if texts else np.zeros((0, 1))


class SentenceTransformerEmbedder(_Cached):
    """Vietnamese_Embedding (tinh chỉnh từ BGE-M3), độ dài tối đa 256 token."""

    def __init__(self, model_name: str | None = None, max_seq_length: int | None = None):
        super().__init__()
        from sentence_transformers import SentenceTransformer

        cfg = load_config()["analysis"]
        self.model = SentenceTransformer(model_name or cfg["embed_model"])
        self.model.max_seq_length = max_seq_length or cfg["max_seq_length"]

    def _encode_new(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=False))


class HashingEmbedder(_Cached):
    """Phương án dự phòng không cần mô hình: túi n-gram ký tự băm. Chỉ bắt tương đồng bề mặt, không phải ngữ nghĩa."""

    def __init__(self, dim: int = 512):
        super().__init__()
        self.dim = dim

    def _encode_new(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim))
        for i, t in enumerate(texts):
            s = f" {norm_key(t)} "
            for n in (3, 4):
                for j in range(len(s) - n + 1):
                    h = int(hashlib.md5(s[j:j + n].encode()).hexdigest(), 16)
                    out[i, h % self.dim] += 1.0
        return out
