# MedLens

On-device polypharmacy drug interaction detection agent for Android.
Fine-tuned Gemma 4 E2B — 100% offline, privacy-preserving.

---

## Prerequisites

- Python 3.13
- `uv` package manager
- PostgreSQL running locally
- `zcat`, `sqlite3`, `pgloader` installed (for pediatric FAERS pipeline)

```bash
uv sync
cp .env.example .env   # add your POSTGRES_URI
```

---

## Data Pipeline

### Step 1 — Load Pediatric FAERS (preprocessed, `public` schema)

Ingests `data/raw/effect_peds_19q2_v0.3_20211119.sql.gz` (~263MB compressed)
into PostgreSQL via temp SQLite + pgloader. Produces 17 tables (~1.1GB):
`ade_raw`, `ade_nichd`, `sider`, `drug_gene`, etc.

```bash
uv run python data/pg_builder.py
```

---

### Step 2 — Load Raw FAERS Quarterly Data (`faers` schema)

Loads FDA adverse event reports from `data/raw/faers/*.zip` (2020Q1–2025Q3).
6 tables: `demo`, `drug`, `reac`, `outc`, `ther`, `indi`.

```bash
# Load a single quarter (fast, ~1.9M drug rows)
uv run python data/faers_explorer.py --load --quarters 2024Q1

# Load all 24 quarters (~51M drug rows estimated — takes time)
uv run python data/faers_explorer.py --load

# Explore file-level stats without loading
uv run python data/faers_explorer.py --explore
```

> **Note:** Each `--load` run drops and recreates all tables (not incremental).

---

### Step 3 — Explore Loaded FAERS Data

Interactive DB-level exploration (11 sections). Designed for conversion to Jupyter notebook.

```bash
# Run all 11 sections
uv run python data/faers_explore.py

# List available sections
uv run python data/faers_explore.py --list

# Run specific sections
uv run python data/faers_explore.py --sections 1 6 7
```

Sections cover: schema overview, null rates, role codes, outcome severity,
drug normalization, polypharmacy, DDI pairs, drug-reaction patterns,
demographics, training example previews, quality flags.

---

### Step 4 — Build Training Data (`medlens` schema)

Generates Unsloth-compatible instruction-tuning examples from FAERS cases.
One table: `medlens.training_examples`.

```bash
# Create/reset the table (destructive — drops if exists)
uv run python data/training_data_builder.py --create-schema

# Populate from FAERS multi-drug suspect cases (Type B examples)
uv run python data/training_data_builder.py --build-faers --limit 5000 --thinking-mode never

# Control drug count per example (default: 3–8 drugs)
uv run python data/training_data_builder.py --build-faers --min-drugs 2 --max-drugs 6 --limit 10000

# Mix direct answers with a smaller number of think-tagged examples
uv run python data/training_data_builder.py --build-faers --limit 10000 --thinking-mode mixed

# Check what was loaded
uv run python data/training_data_builder.py --stats
```

---

### Step 5 — Export Training Data to JSONL

```bash
# Export all examples
uv run python data/training_data_builder.py --export data/medlens_train.jsonl

# Export train split only
uv run python data/training_data_builder.py --export data/medlens_train.jsonl --split train
```

Output format (Unsloth-compatible):
```json
{"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
```

If `--thinking-mode mixed` or `--thinking-mode always` is used, assistant labels may include an optional `<|think|>...</think>` prefix.

---

## Database Schema

| Schema     | Populated by             | Contents |
|------------|--------------------------|----------|
| `public`   | `pg_builder.py`          | Pediatric FAERS processed — 17 tables, ~1.1GB |
| `faers`    | `faers_explorer.py`      | Raw FAERS quarterly dumps — 6 tables, keyed on `primaryid` |
| `raw_data` | `pg_builder.py` (intent) | Empty in practice |
| `medlens`  | `training_data_builder.py` | Training examples — 1 table, Unsloth JSONB format |

---

## Training Data Coverage (from `faers` schema, 2024Q1)

| Metric | Count |
|--------|-------|
| Multi-drug suspect cases (≥2 drugs) | ~73,000 / quarter |
| Severe outcomes (DE/LT/HO = Major) | ~36,000 / quarter |
| FDA-flagged DDI cases (role=I) | ~10,000 / quarter |
| Distinct generic ingredients (`prod_ai`) | ~6,400 |
| 24 quarters total — estimated multi-drug | ~870,000 cases |

---

## Architecture Overview

```
Camera (ML Kit OCR)
    ↓
Gemma 4 E2B (fine-tuned, LiteRT .task) — on-device, offline
    ↓ function calling
checkInteractions(drugs) → interaction_db.json (top 200 drugs, ~5K pairs)
    ↓
Severity-ranked safety report (Major / Moderate / Minor)
```

See `medlens-plan.md` for the full 5-phase implementation plan.
