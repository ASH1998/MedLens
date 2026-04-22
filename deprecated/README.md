# Deprecated

Artifacts from the previous architecture (LLM fine-tuning + ML classifier), superseded by `new_plan.md` (base Gemma 4 E2B agent + compressed FAERS evidence + tool verification).

Kept for historical reference; safe to delete once `new_plan.md` is fully landed.

## Layout

- `plans/` — old plan docs (`medlens-plan.md`, `claude-plan.md`, `claude-rethink.md`, `gpt-plan.md`, `finetune_review_notes.md` (was `a.md`)).
- `finetune/` — Unsloth/Kaggle/HF fine-tuning notebooks, training scripts, checkpoints, tokenized datasets, LoRA config. Includes `finetune1/` (HF scripts + DeepSpeed config), `finetune_nested/`, `rebuild_ckpts/` (intermediate rebuild stages), `medlens_tokenized/`, `outputs/` (checkpoint-60).
- `classifier/` — LightGBM/MLP severity classifier notebooks and artifacts (dropped per `new_plan.md §1`).
- `caches/` — Unsloth compiled trainer caches (from repo root, `data/`, and `data/finetune/`).

## What was kept in place (still required for the new plan)

- `data/raw/` — source FAERS dumps.
- `data/pg_builder.py`, `data/faers_explorer.py`, `data/faers_data_explore.py`, `data/training_data_builder.py`, `data/tablesize.sql` — data pipeline feeding `medlens.training_examples`.
- `data/finetune/medlens_data_rebuild.ipynb` — builds `medlens.training_examples`, which is the source for the new evidence artifacts.
- `data/*.jsonl` — kept to be safe (previously FT training data); decide separately whether to prune.
- `faers_explore.ipynb` — FAERS DB exploration.
- `docs/overview.txt`, `docs/rules.txt` — not clearly tied to the fine-tune plan.
