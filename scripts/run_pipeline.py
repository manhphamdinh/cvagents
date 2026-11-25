"""Chạy Extractor rồi Analysis trên một CV và một JD.

    python scripts/run_pipeline.py data/raw/cv.pdf data/raw/jd.txt [--hashing]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cvagents.analysis import CVJDAnalysisAgent
from cvagents.analysis.embedder import HashingEmbedder
from cvagents.extractor import CVExtractorAgent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cv")
    ap.add_argument("jd", nargs="?")
    ap.add_argument("--hashing", action="store_true", help="dùng embedder băm thay cho Vietnamese_Embedding")
    ap.add_argument("--out", default="data/processed")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = CVExtractorAgent().extract(args.cv)
    (out / f"{Path(args.cv).stem}.profile.json").write_text(
        profile.model_dump_json(indent=2), encoding="utf-8")
    print(profile.model_dump_json(indent=2))

    if args.jd:
        agent = CVJDAnalysisAgent(embedder=HashingEmbedder() if args.hashing else None)
        report = agent.analyze(profile, Path(args.jd).read_text(encoding="utf-8"))
        (out / f"{Path(args.cv).stem}.report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8")
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
