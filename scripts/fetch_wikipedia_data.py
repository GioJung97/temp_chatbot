#!/usr/bin/env python3
"""
Fetch full Wikipedia plaintext content for each bird record (from your Wikidata-derived JSONL),
and write a RAG-friendly JSONL of chunks.

Input:  data/processed/birds.jsonl
Output: data/processed/birds_wikipedia_full.jsonl
Cache:  data/raw/wikipedia_full/ (one json per page title)

Uses MediaWiki API (full page extract as plain text):
  https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext=1&exsectionformat=wiki...

Features:
- rate limiting (requests/sec)
- retries + exponential backoff (handles 429/5xx)
- resume via state file
- safe title parsing from wikipedia URL
- chunking
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

USER_AGENT = "GioBirdRAG/0.1 (local educational project)"

# MediaWiki API endpoint
MEDIAWIKI_API = "https://en.wikipedia.org/w/api.php"

DEFAULT_CHUNK_CHARS = 1600
DEFAULT_CHUNK_OVERLAP = 200


def load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def wikipedia_title_from_url(url: str) -> Optional[str]:
    if not url or "wikipedia.org/wiki/" not in url:
        return None
    title = url.split("/wiki/", 1)[1].strip()
    if not title:
        return None
    return urllib.parse.unquote(title)


def safe_cache_key(title: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9._-]+", "_", title)
    return key[:180]


def load_state(state_file: Path) -> Set[str]:
    if not state_file.exists():
        return set()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(x for x in data if isinstance(x, str))
    except Exception:
        pass
    return set()


def save_state(state_file: Path, done: Set[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(sorted(done)), encoding="utf-8")


def fetch_with_backoff(session: requests.Session, params: Dict[str, Any], max_retries: int = 8) -> Dict[str, Any]:
    backoff = 1.0
    last_status = None

    for _ in range(max_retries + 1):
        r = session.get(MEDIAWIKI_API, params=params, timeout=45)
        last_status = r.status_code

        if r.status_code == 200:
            return r.json()

        if r.status_code in (429, 502, 503, 504):
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff

            wait = wait + random.random() * 0.6
            time.sleep(wait)
            backoff = min(backoff * 2, 60.0)
            continue

        r.raise_for_status()

    raise RuntimeError(f"Exceeded retries (last_status={last_status})")


def chunk_text(text: str, chunk_chars: int, overlap: int) -> List[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_chars, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(0, end - overlap)
    return chunks


def extract_plaintext_from_mediawiki(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """
    Returns: (title, page_url, extract_text, is_disambiguation)
    """
    query = payload.get("query")
    if not isinstance(query, dict):
        return None, None, None, False

    pages = query.get("pages")
    if not isinstance(pages, dict) or not pages:
        return None, None, None, False

    # There is typically one page entry keyed by pageid
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return None, None, None, False

    title = page.get("title") if isinstance(page.get("title"), str) else None
    extract = page.get("extract") if isinstance(page.get("extract"), str) else None

    # Disambiguation detection: pageprops.disambiguation exists
    pageprops = page.get("pageprops")
    is_disambig = isinstance(pageprops, dict) and "disambiguation" in pageprops

    page_url = None
    if title:
        page_url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")

    return title, page_url, extract, is_disambig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="data/processed/birds.jsonl")
    ap.add_argument("--out", dest="out_path", default="data/processed/birds_wikipedia_full.jsonl")
    ap.add_argument("--cache-dir", default="data/raw/wikipedia_full")
    ap.add_argument("--state-file", default="data/state/wiki_full_done_titles.json")
    ap.add_argument("--rps", type=float, default=0.5, help="Start lower for full pages.")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    ap.add_argument("--overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    cache_dir = Path(args.cache_dir)
    state_file = Path(args.state_file)

    if not in_path.exists():
        print(f"Missing input JSONL: {in_path}", file=sys.stderr)
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    done_titles = load_state(state_file)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    interval = 1.0 / max(args.rps, 0.1)

    processed = 0
    fetched = 0
    cached = 0
    skipped_no_wiki = 0
    skipped_disambig = 0
    skipped_no_text = 0
    failed = 0
    written_chunks = 0

    for bird in load_jsonl(in_path):
        if args.max and processed >= args.max:
            break
        processed += 1

        qid = bird.get("doc_id") or bird.get("qid") or bird.get("id")
        wiki_url = (bird.get("external_links") or {}).get("wikipedia")
        if not isinstance(qid, str):
            continue

        if not isinstance(wiki_url, str) or not wiki_url:
            skipped_no_wiki += 1
            continue

        title_in_url = wikipedia_title_from_url(wiki_url)
        if not title_in_url:
            skipped_no_wiki += 1
            continue

        if title_in_url in done_titles:
            continue

        t0 = time.time()
        try:
            cache_key = safe_cache_key(title_in_url)
            cache_path = cache_dir / f"{cache_key}.json"

            if cache_path.exists():
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                cached += 1
            else:
                # Full plaintext extract (single long string)
                params = {
                    "action": "query",
                    "format": "json",
                    "redirects": 1,
                    "prop": "extracts|pageprops",
                    "explaintext": 1,
                    "exsectionformat": "wiki",
                    "titles": title_in_url,
                }
                payload = fetch_with_backoff(session, params=params)
                cache_path.write_text(json.dumps(payload), encoding="utf-8")
                fetched += 1

            title, page_url, extract_text, is_disambig = extract_plaintext_from_mediawiki(payload)

            if is_disambig:
                skipped_disambig += 1
                done_titles.add(title_in_url)
                continue

            if not isinstance(extract_text, str) or not extract_text.strip():
                skipped_no_text += 1
                done_titles.add(title_in_url)
                continue

            chunks = chunk_text(extract_text, args.chunk_chars, args.overlap)
            if not chunks:
                skipped_no_text += 1
                done_titles.add(title_in_url)
                continue

            for j, chunk in enumerate(chunks):
                doc = {
                    "doc_id": f"{qid}:wikipedia_full:{j}",
                    "species_id": qid,
                    "source": "wikipedia",
                    "title": title or title_in_url.replace("_", " "),
                    "url": page_url or wiki_url,
                    "section": "full_page_extract",
                    "text": chunk,
                    "license_note": "Wikipedia text is CC BY-SA; include attribution via URL."
                }
                append_jsonl(out_path, doc)
                written_chunks += 1

            done_titles.add(title_in_url)
            if len(done_titles) % 25 == 0:
                save_state(state_file, done_titles)

        except Exception as e:
            failed += 1
            print(f"FAILED {qid} ({title_in_url}): {e}", file=sys.stderr)

        # rate limit
        elapsed = time.time() - t0
        sleep_for = interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)

        if processed % 50 == 0:
            print(
                f"[{processed}] fetched={fetched} cached={cached} "
                f"no_wiki={skipped_no_wiki} disambig={skipped_disambig} "
                f"no_text={skipped_no_text} failed={failed} chunks={written_chunks}"
            )

    save_state(state_file, done_titles)
    print("Done.")
    print(f"Processed birds: {processed}")
    print(f"Fetched: {fetched}  Cached: {cached}")
    print(f"Skipped: no_wiki={skipped_no_wiki} disambig={skipped_disambig} no_text={skipped_no_text}")
    print(f"Failed: {failed}")
    print(f"Wrote chunks: {written_chunks}")
    print(f"Output: {out_path}")
    print(f"State: {state_file}")
    print(f"Cache: {cache_dir}")


if __name__ == "__main__":
    main()
