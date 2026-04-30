# CLAUDE.md

This file gives coding agents the current repository context.

## Project

MedLens is an offline medication-safety prototype. The active MVP uses curated
DDI/adverse-effect CSVs from `data/raw/DDI/` to build local SQLite evidence
artifacts. It does not currently train or fine-tune an LLM, and it does not use
a separate ML severity classifier.

The intended product flow is:

```text
camera OCR or typed medication names
    -> deterministic normalization
    -> deterministic local DDI lookup
    -> structured safety report
    -> Gemma explains the structured report later
```

The deterministic tools are the authority. The model may explain tool results,
ask follow-up questions, and format user-facing language, but it must not invent
interactions, adverse effects, evidence, or severity levels.

## Active Evidence

Current evidence comes from:

```text
data/raw/DDI/usa_prioritized_ddi_ade_signals.csv
data/raw/DDI/eu_eea_prioritized_ddi_ade_signals.csv
data/raw/DDI/india_prioritized_ddi_ade_signals.csv
data/raw/DDI/india_expanded_prioritized_ddi_ade_signals.csv
```

These files are screening/reference DDI-ADE signals. They are useful for the MVP
because they include drug pairs, adverse effects, severity, mechanism/rationale,
regional relevance, patient risk flags, source basis, URLs, and caveats.

PostgreSQL and FAERS-derived tables are not required for the active CSV-only
MVP. They may remain available as historical/build-time data, but do not make
new work depend on them unless the user explicitly asks.

## Implemented Modules

- `medlens/artifacts/build_normalization.py`
  Builds `normalization.sqlite`.
- `medlens/artifacts/build_evidence.py`
  Builds `evidence.sqlite` from the DDI CSVs.
- `medlens/tools/local_safety.py`
  Provides normalization, pair lookup, raw signal retrieval, and structured
  report synthesis.
- `medlens/agent.py`
  Provides the model-agnostic agent wrapper and LLM provider interface.
- `medlens/cli.py`
  CLI harness for JSON/text reports and agent explanations.

Current artifact counts:

- `normalization.sqlite`: 933 canonical medications, 1,339 aliases.
- `evidence.sqlite`: 19,706 known interaction pairs, 99,813 pair-effect rows,
  157,600 raw DDI signal rows, 16,896 unresolved import issues.

## Commands

Use Python 3.12 and `uv`.

```bash
uv sync
```

Build artifacts:

```bash
python3 -m medlens.artifacts.build_normalization \
  --output data/artifacts/normalization.sqlite

python3 -m medlens.artifacts.build_evidence \
  --input-dir data/raw/DDI \
  --normalization-db data/artifacts/normalization.sqlite \
  --output data/artifacts/evidence.sqlite
```

Run a report:

```bash
python3 -m medlens.cli Advil Warfarin
python3 -m medlens.cli --format text Advil Warfarin Paracetamol "Mystery Pill"
python3 -m medlens.cli --format agent --provider template Advil Warfarin
python3 -m medlens.cli --format agent --provider gemini Advil Warfarin
python3 -m medlens.cli --format agent --provider bedrock Advil Warfarin
python3 -m medlens.cli --chat --provider bedrock
```

Run tests:

```bash
python3 -m unittest \
  tests.test_normalization_artifact \
  tests.test_evidence_artifact \
  tests.test_local_safety_tools \
  tests.test_cli \
  tests.test_agent

python3 -m compileall medlens tests
```

## SQLite Schema Notes

`normalization.sqlite`:

- `drug`
- `drug_alias`

`evidence.sqlite`:

- `known_interaction`
- `known_interaction_effect`
- `ddi_raw_signal`
- `ddi_import_issue`
- `evidence_import_file`

The `evidence_import_file` table proves which CSV files were imported and how
many rows were resolved/unresolved. The `ddi_import_issue` table is the feedback
loop for adding aliases and improving coverage.

## Next Priorities

1. Improve normalization coverage from unresolved DDI rows.
2. Add a small demo/evaluation regimen set.
3. Expand the model-agnostic agent wrapper and add response verification.
4. Add OCR/Android after backend behavior is stable.
5. Trim legacy dependencies from `pyproject.toml`.

## Gotchas

- Generated SQLite artifacts are ignored by git.
- Do not present DDI CSV rows as patient-specific diagnosis or causality.
- Keep safety claims tied to local tool output.
- `main.py` is still a stub; use `medlens.cli`.
