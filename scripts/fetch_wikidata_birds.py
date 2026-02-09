#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

QID_RE = re.compile(r"\bQ[1-9]\d*\b")
ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
USER_AGENT = "GioBirdRAG/0.1 (local educational project)"

P_SCI_NAME = "P225"
P_IUCN = "P141"
P_PARENT = "P171"
P_RANK = "P105"


def qid_from_url(url: str) -> Optional[str]:
    m = QID_RE.search(url)
    return m.group(0) if m else None


def load_qids_from_row_array_json(path: Path) -> List[str]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    qids: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        taxon_url = row.get("taxon")
        if isinstance(taxon_url, str):
            qid = qid_from_url(taxon_url)
            if qid:
                qids.append(qid)
    return qids


def first_claim_value(entity: Dict[str, Any], pid: str) -> Optional[Any]:
    claims = entity.get("claims", {})
    stmts = claims.get(pid)
    if not isinstance(stmts, list):
        return None
    for st in stmts:
        mainsnak = st.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        if "value" in datavalue:
            return datavalue["value"]
    return None


def get_label(entity: Dict[str, Any], lang: str = "en") -> Optional[str]:
    d = entity.get("labels", {}).get(lang)
    return d.get("value") if isinstance(d, dict) else None


def get_description(entity: Dict[str, Any], lang: str = "en") -> Optional[str]:
    d = entity.get("descriptions", {}).get(lang)
    return d.get("value") if isinstance(d, dict) else None


def get_aliases(entity: Dict[str, Any], lang: str = "en") -> List[str]:
    aliases = entity.get("aliases", {}).get(lang)
    if not isinstance(aliases, list):
        return []
    out = []
    for a in aliases:
        if isinstance(a, dict) and isinstance(a.get("value"), str):
            out.append(a["value"])
    return out


def wikipedia_url(entity: Dict[str, Any]) -> Optional[str]:
    sl = entity.get("sitelinks", {}).get("enwiki")
    if not isinstance(sl, dict):
        return None
    title = sl.get("title")
    if not isinstance(title, str) or not title:
        return None
    return "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")


def wikidata_url(qid: str) -> str:
    return f"https://www.wikidata.org/wiki/{qid}"


def normalize_entity_to_rag_doc(qid: str, entity: Dict[str, Any]) -> Dict[str, Any]:
    common = get_label(entity, "en")
    desc = get_description(entity, "en")
    aliases = get_aliases(entity, "en")

    sci = first_claim_value(entity, P_SCI_NAME)
    scientific_name = sci if isinstance(sci, str) else None

    rank_val = first_claim_value(entity, P_RANK)
    rank_qid = rank_val.get("id") if isinstance(rank_val, dict) else None

    parent_val = first_claim_value(entity, P_PARENT)
    parent_qid = parent_val.get("id") if isinstance(parent_val, dict) else None

    iucn_val = first_claim_value(entity, P_IUCN)
    iucn_qid = iucn_val.get("id") if isinstance(iucn_val, dict) else None

    wiki = wikipedia_url(entity)
    wd = wikidata_url(qid)

    overview_parts = []
    if common:
        overview_parts.append(f"Common name: {common}.")
    if scientific_name:
        overview_parts.append(f"Scientific name: {scientific_name}.")
    if desc:
        overview_parts.append(f"Description: {desc}.")
    if iucn_qid:
        overview_parts.append(f"IUCN conservation status (Wikidata item): {iucn_qid}.")
    if parent_qid:
        overview_parts.append(f"Parent taxon (Wikidata item): {parent_qid}.")
    if wiki:
        overview_parts.append(f"Wikipedia page: {wiki}.")
    overview_text = " ".join(overview_parts).strip()

    return {
        "doc_id": qid,
        "entity_type": "bird_species",
        "common_name": common,
        "scientific_name": scientific_name,
        "description": desc,
        "aliases": aliases,
        "taxonomy": {"rank_qid": rank_qid, "parent_taxon_qid": parent_qid},
        "conservation": {"iucn_status_qid": iucn_qid},
        "external_links": {"wikidata": wd, "wikipedia": wiki},
        "sections": [{"section": "overview", "text": overview_text, "source": "wikidata", "url": wd}],
        "license": "CC BY-SA 4.0",
    }


def fetch_entity_with_backoff(qid: str, session: requests.Session, max_retries: int = 8) -> Dict[str, Any]:
    """
    Retries on 429/503/502 with exponential backoff and respects Retry-After if provided.
    """
    url = ENTITY_URL.format(qid=qid)
    backoff = 1.0

    for attempt in range(max_retries + 1):
        r = session.get(url, timeout=30)

        if r.status_code == 200:
            return r.json()

        if r.status_code in (429, 502, 503):
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff

            # jitter helps avoid synchronized retries
            wait = wait + random.random() * 0.5
            time.sleep(wait)
            backoff = min(backoff * 2, 60.0)
            continue

        # other errors: raise immediately
        r.raise_for_status()

    raise RuntimeError(f"Exceeded retries for {qid} (last status={r.status_code})")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wdqs-json", nargs="+")
    ap.add_argument("--out", default="data/processed/birds.jsonl")
    ap.add_argument("--raw-dir", default="data/raw/wikidata")
    ap.add_argument("--state-file", default="data/state/done_qids.json")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--rps", type=float, default=1.0, help="Target requests per second (default 1.0).")
    args = ap.parse_args()

    qids: List[str] = []
    wdqs_inputs = args.wdqs_json
    if not wdqs_inputs:
        wdqs_inputs = [str(p) for p in Path("qids").glob("*.json")]

    if not wdqs_inputs:
        print("No QID source files found. Provide --wdqs-json or add files under meta_data/.", file=sys.stderr)
        sys.exit(1)

    for p in map(Path, wdqs_inputs):
        qids.extend(load_qids_from_row_array_json(p))

    # dedupe preserve order
    seen: Set[str] = set()
    uniq: List[str] = []
    for q in qids:
        if q not in seen:
            seen.add(q)
            uniq.append(q)

    if args.max > 0:
        uniq = uniq[:args.max]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    state_file = Path(args.state_file)
    done = load_state(state_file)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    interval = 1.0 / max(args.rps, 0.1)

    written = 0
    skipped = 0
    failed = 0

    with out_path.open("a", encoding="utf-8") as f:
        for idx, qid in enumerate(uniq, 1):
            if qid in done:
                skipped += 1
                continue

            t0 = time.time()
            try:
                raw_path = raw_dir / f"{qid}.json"
                if raw_path.exists():
                    payload = json.loads(raw_path.read_text(encoding="utf-8"))
                else:
                    payload = fetch_entity_with_backoff(qid, session=session)
                    raw_path.write_text(json.dumps(payload), encoding="utf-8")

                entity = payload.get("entities", {}).get(qid)
                if not isinstance(entity, dict):
                    raise RuntimeError("Missing entity in response")

                doc = normalize_entity_to_rag_doc(qid, entity)
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                f.flush()

                done.add(qid)
                if len(done) % 25 == 0:
                    save_state(state_file, done)

                written += 1
                if idx % 50 == 0:
                    print(f"[{idx}/{len(uniq)}] written={written} skipped={skipped} failed={failed}")

            except Exception as e:
                failed += 1
                print(f"FAILED {qid}: {e}", file=sys.stderr)

            # rate limit to target rps
            elapsed = time.time() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    save_state(state_file, done)
    print(f"Done. total_unique={len(uniq)} written={written} skipped={skipped} failed={failed}")
    print(f"Resume state saved to: {state_file}")


if __name__ == "__main__":
    main()
