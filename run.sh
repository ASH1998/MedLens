#!/usr/bin/env bash
# MedLens full data pipeline — run top to bottom
# Skip steps you've already completed by commenting them out.
set -euo pipefail

# echo "=== Step 1: Load pediatric FAERS (public schema) ==="
# uv run python data/pg_builder.py

echo ""
echo "=== Step 2: Load raw FAERS quarterly data (faers schema) ==="
# Single quarter for a quick test:
# uv run python data/faers_explorer.py --load --quarters 2024Q1
# All 24 quarters:
uv run python data/faers_explorer.py --load --quarters 2024Q1

echo ""
echo "=== Step 3: Create training table (medlens schema) ==="
uv run python data/training_data_builder.py --create-schema

# echo ""
# echo "=== Step 4: Index faers source tables (run once) ==="
# uv run python data/training_data_builder.py --create-faers-indexes

echo ""
echo "=== Step 5: Build training examples from FAERS ==="
uv run python data/training_data_builder.py --build-faers --limit 10000

echo ""
echo "=== Step 6: Stats ==="
uv run python data/training_data_builder.py --stats

# echo ""
# echo "=== Step 7: Export JSONL for Unsloth ==="
# uv run python data/training_data_builder.py --export data/medlens_train.jsonl --split train

# echo ""
# echo "Done. Training data at data/medlens_train.jsonl"
