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
data/raw/DDI/india_common_generic_ddi_5000.csv
data/raw/DDI/common_medicines_india_dataset_5000.csv
```

The DDI files are screening/reference DDI-ADE signals. They are useful for the
MVP because they include drug pairs, adverse effects, severity,
mechanism/rationale, regional relevance, patient risk flags, source basis,
URLs, and caveats. `common_medicines_india_dataset_5000.csv` is not DDI
evidence; it feeds normalization/OCR recovery through common India medicine
names, brand examples, strengths/forms, and local use context.

PostgreSQL and FAERS-derived tables are not required for the active CSV-only
MVP. They may remain available as historical/build-time data, but do not make
new work depend on them unless the user explicitly asks.

## Implemented Modules

- `medlens/artifacts/build_normalization.py`
  Builds `normalization.sqlite`, including the optional India common-medicine
  CSV when present.
- `medlens/artifacts/build_evidence.py`
  Builds `evidence.sqlite` from the DDI CSVs, including
  `india_common_generic_ddi_5000.csv`.
- `medlens/tools/local_safety.py`
  Provides normalization, pair lookup, single-drug interaction listing, raw
  signal retrieval, common India medicine metadata search/profile lookup,
  evidence source/import issue inspection, and structured report synthesis.
- `medlens/tools/registry.py`
  Exposes deterministic SQLite tools to the native agent loop, including
  `list_interactions_for_drug` for broad questions like "what medicines
  interact with captopril?", `get_common_medicine_profile` for brand/common
  medicine education, and artifact-debug tools such as
  `list_evidence_sources`.
- `medlens/agent.py`
  Provides the model-agnostic agent wrapper, LLM provider interface, offline
  template provider, and chat intent handling.
- `medlens/agent_loop.py`
  Runs provider-native tool-calling turns over the deterministic tool registry.
- `medlens/chat/`
  Provides terminal chat session state, slash commands, rendering, and prompt
  handling.
- `medlens/cli.py`
  CLI harness for JSON/text reports and agent explanations.

Current artifact counts:

- `normalization.sqlite`: 981 canonical medications, 1,669 aliases, 5,000
  India common-medicine rows.
- `evidence.sqlite`: 21,810 known interaction pairs, 105,460 pair-effect rows,
  162,600 raw DDI signal rows, 15,696 unresolved import issues, 5 source
  files.

## Commands

Use Python 3.12 and `uv`.

```bash
uv sync
```

Build artifacts:

```bash
python3 -m medlens.artifacts.build_normalization \
  --output data/artifacts/normalization.sqlite \
  --common-medicines-csv data/raw/DDI/common_medicines_india_dataset_5000.csv

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
./medlens.cli --chat --provider template
```

Run tests:

```bash
python3 -m unittest \
  tests.test_normalization_artifact \
  tests.test_evidence_artifact \
  tests.test_local_safety_tools \
  tests.test_cli \
  tests.test_agent \
  tests.test_tool_registry \
  tests.test_agent_loop \
  tests.test_chat_commands

python3 -m compileall medlens tests
```

## Current Chat Behavior Notes

- Chat now sends raw user messages into the native tool loop; the agent decides
  whether to normalize names, search aliases, add medications, build reports, or
  ask a clarification question.
- The offline template provider mirrors the native tool flow for deterministic
  local tests.
- Natural phrasing such as `I take Advil and Warfarin. Is that okay?` should
  parse as `advil` + `warfarin` and return the interaction report on the first
  turn.
- Broad accessibility questions such as `what medicines cant be taken with
  captopril` should call `list_interactions_for_drug` and return ranked local
  interaction partners. This is a locally flagged interaction list, not a
  universal do-not-take list.
- India brand/common names from `common_medicines_india_dataset_5000.csv` now
  resolve through the normal alias table, e.g. `Dolo` -> `acetaminophen`,
  `Clavam` -> `amoxicillin clavulanate`, and `Vitamin D3` ->
  `cholecalciferol`.
- Questions about what a medicine is, its common India use, strength/form,
  OTC/Rx context, brands, or risk flags should call
  `get_common_medicine_profile` or `search_common_medicines`, which read
  `normalization.sqlite`.
- Dataset/data-quality questions should call `list_evidence_sources` or
  `list_import_issues`, which read `evidence.sqlite`.
- Patient-facing responses should lead with practical meaning, explain
  unfamiliar terms in plain language when possible, and avoid row-count-first or
  database-first phrasing.
- Source URLs remain important, but default chat output may summarize long URL
  lists and point to `/sources` for the full list.

## SQLite Schema Notes

`normalization.sqlite`:

- `drug`
- `drug_alias`
- `india_common_medicine`

`evidence.sqlite`:

- `known_interaction`
- `known_interaction_effect`
- `ddi_raw_signal`
- `ddi_import_issue`
- `evidence_import_file`

The `evidence_import_file` table proves which CSV files were imported and how
many rows were resolved/unresolved. The `ddi_import_issue` table is the feedback
loop for adding aliases and improving coverage.

## Runtime Tool Coverage

| SQLite DB | Table | Runtime tool coverage |
| --- | --- | --- |
| `normalization.sqlite` | `drug` | `normalize_medications`, `search_drug_aliases`, `get_common_medicine_profile`, `search_common_medicines`, plus pair/report tools through normalization |
| `normalization.sqlite` | `drug_alias` | `normalize_medications`, `search_drug_aliases`, `add_medications`, `remove_medications`, `lookup_pair`, `list_interactions_for_drug`, `build_structured_report` |
| `normalization.sqlite` | `india_common_medicine` | `get_common_medicine_profile`, `search_common_medicines` |
| `evidence.sqlite` | `known_interaction` | `lookup_pair`, `list_interactions_for_drug`, `build_structured_report`, `get_pair_effects`, `get_raw_signals`, `get_full_raw_signals`, `severity_consensus`, `find_pairs_by_effect` |
| `evidence.sqlite` | `known_interaction_effect` | `lookup_pair`, `list_interactions_for_drug`, `build_structured_report`, `get_pair_effects`, `find_pairs_by_effect` |
| `evidence.sqlite` | `ddi_raw_signal` | `lookup_pair`, `build_structured_report`, `get_raw_signals`, `get_full_raw_signals`, `severity_consensus` |
| `evidence.sqlite` | `evidence_import_file` | `list_evidence_sources` |
| `evidence.sqlite` | `ddi_import_issue` | `list_import_issues` |

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
