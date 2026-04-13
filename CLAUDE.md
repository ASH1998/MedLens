# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MedLens** is an on-device polypharmacy drug interaction detection agent built for the **Gemma 4 Good Hackathon** (deadline: May 18, 2026, up to $80K across 4 tracks).

**The mission:** An Android app that photographs medication bottles, extracts drug info via OCR + vision, reasons about multi-drug interaction risks using a fine-tuned Gemma 4 E2B model, and generates severity-ranked safety reports — 100% offline, on-device, privacy-preserving.

**Target tracks:** LiteRT ($10K) + Unsloth ($10K) + Health & Sciences ($10K) + Main Track ($10K–$50K)

## Development Commands

```bash
# Install dependencies
uv sync

# Run the pediatric FAERS ingestion pipeline
# (requires zcat, sqlite3, pgloader installed + PostgreSQL running)
uv run python data/pg_builder.py

# Load raw FAERS quarterly data into the `faers` schema
uv run python data/faers_explorer.py --load --quarters 2024Q1
uv run python data/faers_explorer.py --load            # load all 24 quarters

# Inspect loaded FAERS data
uv run python data/faers_explorer.py --explore         # file-level stats (no DB)
uv run python data/faers_explore.py                    # DB-level exploration (all sections)
uv run python data/faers_explore.py --list             # list sections
uv run python data/faers_explore.py --sections 6 7 10  # run specific sections

# Run main entry point (currently a stub)
uv run python main.py
```

**Python version:** 3.13 (enforced via `.python-version`)

**Package manager:** `uv` — use `uv add <pkg>` to add dependencies, never `pip install`.

## Configuration

- **`.env`** — PostgreSQL connection string (`POSTGRES_URI`). Required for data pipeline.
- **`.claude/settings.local.json`** — MCP server config (PostgreSQL MCP enabled for direct DB queries).

The PostgreSQL MCP server is available in Claude Code sessions, allowing direct SQL queries against the local `medlens` database without shelling out.

## Architecture: 5-Phase Plan

See `medlens-plan.md` for the detailed implementation plan. High-level:

### Phase 1: Data & Fine-Tuning Dataset (current)

**Source 1 — Pediatric FAERS (preprocessed):**
- `data/raw/effect_peds_19q2_v0.3_20211119.sql.gz` — 263MB compressed SQL dump (SQLite format)
- `data/pg_builder.py` ingests: decompress → temp SQLite → pgloader → PostgreSQL `public` schema (despite the script's `raw_data` target, data lands in `public`)
- 17 tables, ~1.1GB loaded: `ade_nichd` (540MB), `ade_raw` (536MB), `sider`, `drug_gene`, `gene_expression`, `atc_raw_map`, etc.
- Precomputed ADE signals, SIDER side effects, DrugBank pharmacogenomics — NOT raw DDI pairs

**Source 2 — Raw FAERS quarterly (ready to load):**
- `data/raw/faers/*.zip` — 24 quarterly dumps (2020Q1 → 2025Q3) from FDA
- `data/raw/faers_index.md` + `faers_readme.md` — official FDA schema docs
- `data/faers_explorer.py` loads each quarter into `faers` schema, 6 tables: `demo`, `drug`, `reac`, `outc`, `ther`, `indi`
- All columns are `TEXT` (FDA real data overflows their declared VARCHAR limits — see Gotchas)
- Single quarter (2024Q1) = ~1.9M drug rows, ~400K cases; 24 quarters = ~51M drug rows estimated
- **role_cod key:** `PS`=Primary Suspect, `SS`=Secondary Suspect, `C`=Concomitant, `I`=Interacting (FDA-flagged DDI), `DN`=Not Administered
- **outc_cod severity:** `DE`/`LT`/`HO` → Major, `DS`/`CA`/`RI` → Moderate, `OT` → Minor
- `prod_ai` (active ingredient) is FDA-normalized → 6.4K distinct ingredients vs 27.7K distinct brand names per quarter — brand→generic already done

**Still to integrate:**
- DrugBank 6.0 (1.4M interactions) — still the primary pairwise DDI knowledge base
- OpenFDA Drug Labels, RxNorm (brand→generic where `prod_ai` is missing)

**Output target:** ~7,000 instruction-tuning examples in Unsloth chat format + `interaction_db.json` (top 200 drugs, ~5MB) for on-device lookup

**Training signal in raw FAERS (per quarter):**
- ~73K multi-drug suspect cases (≥2 drugs with PS/SS/I role)
- ~36K of those with severe outcomes (DE/LT/HO) — classic Type B training examples
- ~10K cases with FDA-coded `role_cod='I'` (explicit DDI flags)

### Phase 2: Fine-Tuning with Unsloth
- Model: Gemma 4 E2B with LoRA on Google Colab (T4 GPU)
- 3 training example types: single interaction query, multi-drug analysis, agentic follow-up
- The `<|think|>` tag is used for chain-of-thought reasoning in training examples
- Evaluation benchmark: DDI Corpus (792 DrugBank texts, 5,028 annotated DDIs) — evaluation only, not training
- Export chain: safetensors → GGUF → LiteRT `.task` format → Hugging Face

### Phase 3: Android App (LiteRT-LM)
Kotlin app with 4 screens: Camera → Medication List → Interaction Report → Chat.

**Layer stack:**
```
UI (Jetpack Compose)
    ↓
Agent/Reasoning (ConversationManager + ToolSet)
    ↓
Tool Layer: extractMedication(image) | checkInteractions(drugs) | getContraindications(drug) | generateReport(interactions)
    ↓
Data Layer: InteractionDB (local JSON) | RxNormMapping | Room DB (history)
    ↓
ML Layer: LiteRT-LM (Gemma 4 E2B .task file) | ML Kit Text Recognition (OCR)
```

### Phase 4: Agentic Loop & Polish
- Native function calling with Gemma 4's ToolSet pattern
- `<|think|>` reasoning traces integrated
- Follow-up question logic for incomplete medication lists

### Phase 5: Submission
- 3-minute demo video showing 6 real OTC medications with 4 known interactions
- ≤1,500 word Kaggle writeup
- Public GitHub repo

## Key Design Decisions

- **Offline-first:** The bundled `interaction_db.json` (~2–5MB) covers the top 200 drugs / ~5K pairs — no network calls needed for core functionality
- **RxNorm normalization:** Brand names must be normalized to generics before lookups ("Advil" → "ibuprofen")
- **Severity levels:** Major / Moderate / Minor — always surface Major interactions prominently
- **Evaluation metric:** DDI Corpus benchmark improvements over base Gemma 4 E2B are the quantitative claim

## Submission Criteria

Evaluation: Impact (40%), Demo Video (30%), Technical Depth (30%). The video is a primary deliverable — it must show real medications and real detected interactions, not mocked output.

## Database Schema Map

| Schema | Populated by | Contents |
|--------|--------------|----------|
| `public` | `pg_builder.py` | Pediatric FAERS processed (ade_raw, ade_nichd) + SIDER + DrugBank pharmacogenomics — 17 tables, ~1.1GB |
| `faers` | `faers_explorer.py --load` | Raw FAERS quarterly dumps — 6 tables keyed on `primaryid` (case ID) |
| `raw_data` | `pg_builder.py` (intent) | Empty in practice — `pg_builder.py` intends to move tables here but they remain in `public` |

## Data Files

- `data/pg_builder.py` — loads pediatric FAERS SQL dump into PostgreSQL
- `data/faers_explorer.py` — loads raw FAERS quarterly zips into `faers` schema (file-level `--explore` or DB `--load`)
- `data/faers_explore.py` — interactive DB exploration, 11 sections covering schema, null rates, role codes, outcomes, polypharmacy, DDI pairs, drug-reaction patterns, demographics, training example previews, quality flags. Each section is a standalone function, designed for easy conversion to a Jupyter notebook.

## Gotchas

- **Do not trust FAERS VARCHAR limits.** FDA's own schema doc (`faers_index.md`) declares column lengths that their actual data overflows (`REPT_COD` is declared VARCHAR(9) but real values hit longer strings; `OCCR_COUNTRY` declared 2 chars but data is 2-3). `faers_explorer.py` uses `TEXT` for all columns to avoid `StringDataRightTruncation`. Cast at query time when needed.
- **`CREATE TABLE IF NOT EXISTS` + failed partial load = stuck with wrong schema.** `faers_explorer.py` now uses `DROP TABLE IF EXISTS ... CASCADE` to force clean recreation on every `--load` run. This means re-running `--load` wipes and reloads, it is not incremental.
- **MCP PostgreSQL is read-only.** Schema/DDL operations (DROP, CREATE, TRUNCATE) must go through a direct `psql` call or `psycopg` connection — not MCP.
- **Primary key strategy:** `primaryid` (NOT `caseid`) is the case+version identifier and the join key across all FAERS tables. `caseid` alone is not unique across versions of the same case.
- **`drugname` vs `prod_ai`:** `drugname` is verbatim brand name as reported (noisy, 27K distinct); `prod_ai` is FDA-normalized active ingredient (6.4K distinct). Always prefer `prod_ai` for drug-level aggregation; use `drugname` for OCR-like brand→generic training signal.
