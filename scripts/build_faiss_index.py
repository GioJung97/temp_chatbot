#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

import faiss
from sentence_transformers import SentenceTransformer


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/processed/birds_wikipedia_full.jsonl")
    ap.add_argument("--out-dir", default="data/index")
    ap.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--min-chars", type=int, default=200)
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        raise SystemExit(f"Missing input: {in_path}")

    rows = load_jsonl(in_path)

    # Filter
    docs: List[Dict[str, Any]] = []
    texts: List[str] = []
    for r in rows:
        text = r.get("text")
        if not isinstance(text, str):
            continue
        text = text.strip()
        if len(text) < args.min_chars:
            continue

        # keep the minimal fields you need to show citations
        doc = {
            "doc_id": r.get("doc_id"),
            "species_id": r.get("species_id"),
            "title": r.get("title"),
            "url": r.get("url"),
            "section": r.get("section", "unknown"),
            "text": text,
        }
        docs.append(doc)
        texts.append(text)

    print(f"Loaded {len(rows)} rows, kept {len(docs)} chunks after filtering.")

    model = SentenceTransformer(args.model)

    # Embed in batches
    emb_list: List[List[float]] = []
    total = len(texts)
    for i in range(0, total, args.batch_size):
        batch = texts[i : i + args.batch_size]
        emb = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        emb_list.append(emb)

        done = min(i + args.batch_size, total)
        if done % 500 == 0 or done == total:
            print(f"Embedded {done}/{total}")

    import numpy as np

    vectors = np.vstack(emb_list).astype("float32")
    dim = vectors.shape[1]
    print(f"Vector shape: {vectors.shape} (dim={dim})")

    # FAISS cosine similarity = inner product if vectors are normalized
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    faiss_path = out_dir / "faiss.index"
    meta_path = out_dir / "chunks.jsonl"

    faiss.write_index(index, str(faiss_path))
    write_jsonl(meta_path, docs)

    print(f"Saved index: {faiss_path}")
    print(f"Saved metadata: {meta_path}")
    print("Done.")


if __name__ == "__main__":
    main()
