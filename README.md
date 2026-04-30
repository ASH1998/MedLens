# MedLens

MedLens is an offline medication-safety prototype. It normalizes medication
names, checks local DDI/adverse-effect signal data, and returns deterministic
structured reports that a Gemma agent can later explain.

Current MVP boundary:

- use the four curated DDI-ADE CSVs in `data/raw/DDI/` as the safety evidence;
- build reproducible SQLite artifacts from those CSVs;
- run all lookup/report logic locally;
- do not use FAERS/PostgreSQL, fine-tuning, or an ML classifier for the active MVP.

The local evidence is a screening/reference signal pack, not patient-specific
clinical ground truth.

## Current Status

Implemented:

- `normalization.sqlite` builder with canonical ingredients and aliases.
- `evidence.sqlite` builder from the DDI-ADE CSVs.
- deterministic Python lookup/report tools.
- CLI harness for JSON or text reports.
- unit tests for artifact builds, lookup tools, and CLI output.

Current artifact shape:

| Artifact | Contents |
|---|---:|
| `data/artifacts/normalization.sqlite` | 933 canonical medications, 1,339 aliases |
| `data/artifacts/evidence.sqlite` | 19,706 interaction pairs, 99,813 pair-effect rows, 157,600 raw DDI signal rows |

## Evidence Inputs

Active raw inputs:

```text
data/raw/DDI/usa_prioritized_ddi_ade_signals.csv
data/raw/DDI/eu_eea_prioritized_ddi_ade_signals.csv
data/raw/DDI/india_prioritized_ddi_ade_signals.csv
data/raw/DDI/india_expanded_prioritized_ddi_ade_signals.csv
```

These CSVs include drug pairs, adverse effects, severity, mechanism/rationale,
regional relevance, patient risk flags, source basis, source URLs, and screening
caveats.

PostgreSQL and FAERS-derived training tables are not required for the current
CSV-only MVP.

## Setup

Use Python 3.12 with `uv`.

```bash
uv sync
```

The code itself only needs the Python standard library for the current artifact
builders and CLI path. The larger dependency set in `pyproject.toml` is legacy
from earlier experiments and should be trimmed later.

## Build Artifacts

Build the normalization database:

```bash
python3 -m medlens.artifacts.build_normalization \
  --output data/artifacts/normalization.sqlite
```

Build the DDI evidence database from CSVs:

```bash
python3 -m medlens.artifacts.build_evidence \
  --input-dir data/raw/DDI \
  --normalization-db data/artifacts/normalization.sqlite \
  --output data/artifacts/evidence.sqlite
```

The evidence builder records source-file import stats in
`evidence_import_file`, unresolved rows in `ddi_import_issue`, raw rows in
`ddi_raw_signal`, and pair summaries in `known_interaction`.

## Run Reports

JSON output:

```bash
python3 -m medlens.cli Advil Warfarin
```

Text output:

```bash
python3 -m medlens.cli --format text Advil Warfarin Paracetamol "Mystery Pill"
```

Agent explanation over the same structured report:

```bash
# Offline deterministic explanation, useful for development/tests.
python3 -m medlens.cli --format agent --provider template Advil Warfarin

# Gemini, using GOOGLE_API_KEY and GOOGLE_MODEL from .env.
python3 -m medlens.cli --format agent --provider gemini Advil Warfarin

# AWS Bedrock Claude, using AWS_* and CLAUDE_MODEL from .env.
python3 -m medlens.cli --format agent --provider bedrock Advil Warfarin
```

Interactive terminal chat:

```bash
python3 -m medlens.cli --chat --provider bedrock
python3 -m medlens.cli --chat --provider template Advil Warfarin
```

Installed package entrypoint:

```bash
medlens-report Advil Warfarin
```

## Test

```bash
python3 -m unittest \
  tests.test_normalization_artifact \
  tests.test_evidence_artifact \
  tests.test_local_safety_tools \
  tests.test_cli \
  tests.test_agent

python3 -m compileall medlens tests
```

## Architecture

```text
typed meds / later OCR text
    ↓
normalizeMedicationNames()
    ↓
lookupKnownInteraction(a, b)
    ↓
buildStructuredReport()
    ↓
JSON/text report
    ↓ later
Gemma explanation over structured tool output only
```

The deterministic tool layer is the authority for safety findings. Gemma should
not invent interactions, assign severity, or add adverse effects that are not in
the local tool output.

## Next Work

1. Improve normalization coverage from `ddi_import_issue`.
2. Add a compact demo/evaluation set of medication lists.
3. Expand the model-agnostic agent wrapper and add response verification.
4. Build OCR and Android flows after the report schema stabilizes.
5. Trim legacy dependencies and stale data-pipeline references.
