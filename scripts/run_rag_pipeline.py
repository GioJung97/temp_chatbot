#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("\n" + " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the full bird RAG data pipeline.")
    ap.add_argument("--qids-glob", default="qids/*.json")
    ap.add_argument("--max", type=int, default=0)

    ap.add_argument("--rps-wikidata", type=float, default=1.0)
    ap.add_argument("--rps-wikipedia", type=float, default=0.5)

    ap.add_argument("--birds-out", default="data/processed/birds.jsonl")
    ap.add_argument("--birds-raw", default="data/raw/wikidata")
    ap.add_argument("--birds-state", default="data/state/done_qids.json")

    ap.add_argument("--wiki-out", default="data/processed/birds_wikipedia_full.jsonl")
    ap.add_argument("--wiki-raw", default="data/raw/wikipedia_full")
    ap.add_argument("--wiki-state", default="data/state/wiki_full_done_titles.json")
    ap.add_argument("--chunk-chars", type=int, default=1600)
    ap.add_argument("--overlap", type=int, default=200)

    ap.add_argument("--index-dir", default="data/index")
    ap.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--min-chars", type=int, default=200)

    ap.add_argument("--skip-wikidata", action="store_true")
    ap.add_argument("--skip-wikipedia", action="store_true")
    ap.add_argument("--skip-index", action="store_true")

    args = ap.parse_args()

    qid_files = list(Path().glob(args.qids_glob))
    if not qid_files:
        print(f"No QID files found for glob: {args.qids_glob}")
        sys.exit(1)

    if not args.skip_wikidata:
        cmd = [
            sys.executable,
            "scripts/fetch_wikidata_birds.py",
            "--out",
            args.birds_out,
            "--raw-dir",
            args.birds_raw,
            "--state-file",
            args.birds_state,
            "--rps",
            str(args.rps_wikidata),
        ]
        if args.max:
            cmd += ["--max", str(args.max)]
        cmd += ["--wdqs-json", *[str(p) for p in qid_files]]
        run(cmd)

    if not args.skip_wikipedia:
        cmd = [
            sys.executable,
            "scripts/fetch_wikipedia_data.py",
            "--in",
            args.birds_out,
            "--out",
            args.wiki_out,
            "--cache-dir",
            args.wiki_raw,
            "--state-file",
            args.wiki_state,
            "--rps",
            str(args.rps_wikipedia),
            "--chunk-chars",
            str(args.chunk_chars),
            "--overlap",
            str(args.overlap),
        ]
        if args.max:
            cmd += ["--max", str(args.max)]
        run(cmd)

    if not args.skip_index:
        cmd = [
            sys.executable,
            "scripts/build_faiss_index.py",
            "--in",
            args.wiki_out,
            "--out-dir",
            args.index_dir,
            "--model",
            args.model,
            "--batch-size",
            str(args.batch_size),
            "--min-chars",
            str(args.min_chars),
        ]
        run(cmd)


if __name__ == "__main__":
    main()
