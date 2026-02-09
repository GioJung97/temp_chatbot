#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import faiss
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

INDEX_DIR = Path("data/index")
FAISS_PATH = INDEX_DIR / "faiss.index"
META_PATH = INDEX_DIR / "chunks.jsonl"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

app = FastAPI(title="Bird RAG Retrieval (Local)")

index = None
meta: List[Dict[str, Any]] = []
model = None


class SearchResult(BaseModel):
    doc_id: Optional[str] = None
    species_id: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    section: Optional[str] = None
    score: float
    text: str


class SearchResponse(BaseModel):
    query: str
    k: int
    results: List[SearchResult]


@app.on_event("startup")
def load_all():
    global index, meta, model
    if not FAISS_PATH.exists() or not META_PATH.exists():
        raise RuntimeError("Missing FAISS index or metadata. Run build_faiss_index.py first.")

    index = faiss.read_index(str(FAISS_PATH))

    with META_PATH.open("r", encoding="utf-8") as f:
        meta = [json.loads(line) for line in f if line.strip()]

    model = SentenceTransformer(MODEL_NAME)
    print(f"Loaded index with {index.ntotal} vectors and {len(meta)} metadata rows.")


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1),
    k: int = Query(5, ge=1, le=20),
):
    assert index is not None and model is not None

    vec = model.encode([q], normalize_embeddings=True).astype("float32")
    scores, ids = index.search(vec, k)

    results: List[SearchResult] = []
    for score, idx in zip(scores[0].tolist(), ids[0].tolist()):
        if idx < 0 or idx >= len(meta):
            continue
        row = meta[idx]
        results.append(
            SearchResult(
                doc_id=row.get("doc_id"),
                species_id=row.get("species_id"),
                title=row.get("title"),
                url=row.get("url"),
                section=row.get("section"),
                score=float(score),
                text=row.get("text"),
            )
        )

    return SearchResponse(query=q, k=k, results=results)
