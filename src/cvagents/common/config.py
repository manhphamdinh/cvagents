"""Nạp cấu hình (ngưỡng, trọng số) từ resources/config.toml."""
from __future__ import annotations

import json
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

RESOURCES = Path(__file__).resolve().parent.parent / "resources"


@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else RESOURCES / "config.toml"
    with p.open("rb") as f:
        return tomllib.load(f)


@lru_cache(maxsize=None)
def load_resource(name: str) -> Any:
    with (RESOURCES / name).open(encoding="utf-8") as f:
        return json.load(f)
