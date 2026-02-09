# Bird Chat

Minimal chat UI built with Next.js (App Router), TypeScript, Tailwind CSS, and a local SQLite backend.

## Setup

```bash
npm install
```

## Initialize the database

```bash
npm run db:migrate
```

By default the app uses `DATABASE_URL="file:./data/app.db"`. You can override it in `.env.local`.

## Run

```bash
npm run dev
```

Open `http://localhost:3000`.

## Quick Test

1. Start a new chat by typing a message and optionally attaching an image.
2. Reload the page to confirm the landing state resets to “Let’s talk about birds!”.
3. Open the history drawer (top-left menu button) to see saved conversations.
4. Click a previous conversation to continue it.

If uploads fail, ensure the `uploads/` folder is writable and that images are under 5MB.

## RAG Data Setup (Local)

The bird knowledge base lives in `data/` and is intentionally **not tracked in git**. The QID lists are tracked under `qids/`.

### Python deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-rag.txt
```

### One-command pipeline

This runs the full pipeline end-to-end:

```bash
python3 scripts/run_rag_pipeline.py
```

You can control rate limits and a max count for testing:

```bash
python3 scripts/run_rag_pipeline.py --max 200 --rps-wikidata 1.0 --rps-wikipedia 0.5
```

### Step-by-step (manual)

1. Fetch Wikidata entities and normalize into `birds.jsonl`:

```bash
python3 scripts/fetch_wikidata_birds.py \\
  --wdqs-json qids/*.json \\
  --out data/processed/birds.jsonl \\
  --raw-dir data/raw/wikidata \\
  --state-file data/state/done_qids.json \\
  --rps 1.0 \\
  --max 6000
```

2. Fetch full Wikipedia plaintext and chunk it:

```bash
python3 scripts/fetch_wikipedia_data.py \\
  --in data/processed/birds.jsonl \\
  --out data/processed/birds_wikipedia_full.jsonl \\
  --cache-dir data/raw/wikipedia_full \\
  --state-file data/state/wiki_full_done_titles.json \\
  --rps 0.5
```

3. Build local embeddings + FAISS index:

```bash
python3 scripts/build_faiss_index.py \\
  --in data/processed/birds_wikipedia_full.jsonl \\
  --out-dir data/index
```

### Test retrieval server

```bash
uvicorn scripts.retrieval_server:app --reload --port 8081
curl "http://localhost:8081/search?q=habitat%20of%20owl&k=5"
```

### Data layout (expected)

- `qids/` tracked QID lists
- `data/raw/wikidata/` raw Wikidata entity JSON (per QID)
- `data/processed/birds.jsonl` normalized Wikidata docs
- `data/raw/wikipedia_full/` cached MediaWiki responses
- `data/processed/birds_wikipedia_full.jsonl` chunked Wikipedia text
- `data/index/faiss.index` FAISS index
- `data/index/chunks.jsonl` metadata for each chunk
