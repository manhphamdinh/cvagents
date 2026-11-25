"""Bước 3: chuẩn hoá văn bản tiếng Việt."""
from __future__ import annotations

import re

from cvagents.common.text import nfc

_BULLETS = re.compile(r"^[\s•▪●■◦‣∙·*]+(?=\S)")
_DASHES = re.compile(r"[‐‑‒–—―−]")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def normalize_text(s: str) -> str:
    s = nfc(s)
    s = _HYPHEN_BREAK.sub(r"\1\2", s)
    s = _DASHES.sub("-", s)
    lines = [re.sub(r"[ \t ]+", " ", _BULLETS.sub("", ln)).strip() for ln in s.split("\n")]
    return "\n".join(lines)


def is_bullet(raw: str) -> bool:
    return bool(re.match(r"^\s*([•▪●■◦‣∙·*]|-\s)", raw))
