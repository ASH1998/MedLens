# MedLens Plan

MedLens is now a CSV-backed, offline medication-safety agent. The active plan is
to finish the deterministic evidence and tool layer first, then put Gemma on top
as an explanation and dialogue layer.

## Current Decision

Use only the curated DDI/adverse-effect CSVs for the MVP:

```text
data/raw/DDI/usa_prioritized_ddi_ade_signals.csv
data/raw/DDI/eu_eea_prioritized_ddi_ade_signals.csv
data/raw/DDI/india_prioritized_ddi_ade_signals.csv
data/raw/DDI/india_expanded_prioritized_ddi_ade_signals.csv
```

Do not use FAERS/PostgreSQL, fine-tuning, or an ML classifier for the current
product path. FAERS is older and can remain historical context, but it should
not block the MVP.

## Product Goal

Build an offline medication-safety flow that:

- accepts typed names first, OCR text later;
- normalizes brand/generic/regional names to canonical ingredients;
- checks local DDI-ADE evidence from SQLite;
- returns a deterministic structured report;
- lets Gemma explain the report without adding unsupported safety claims.

## Architecture

```text
input medication names
    ↓
normalizeMedicationNames(names[])
    ↓
deduplicate resolved canonical ingredients
    ↓
lookupKnownInteraction(a, b) for each pair
    ↓
buildStructuredReport()
    ↓
Gemma explanation later, constrained to the report
```

The tool layer is the authority. The model may not invent interactions,
severity, mechanisms, adverse effects, or evidence.

## Implemented

### Normalization artifact

Files:

- `medlens/artifacts/build_normalization.py`
- `medlens/artifacts/common_meds.py`
- `medlens/artifacts/schema.py`

Output:

- `data/artifacts/normalization.sqlite`
- 933 canonical medications
- 1,339 aliases

### DDI evidence artifact

Files:

- `medlens/artifacts/build_evidence.py`
- `medlens/artifacts/schema.py`

Output:

- `data/artifacts/evidence.sqlite`
- 19,706 known interaction pairs
- 99,813 pair-effect rows
- 157,600 raw DDI signal rows
- 16,896 unresolved import issues
- 4 source CSVs imported

### Local tool layer

Files:

- `medlens/tools/local_safety.py`
- `medlens/tools/__init__.py`

Implemented:

- normalize medication names
- lookup known/reference DDI interaction
- retrieve top effects
- retrieve raw supporting DDI rows
- build deterministic structured regimen reports
- track unresolved medications and limitations

### CLI harness

Files:

- `medlens/cli.py`
- `tests/test_cli.py`

Commands:

```bash
python3 -m medlens.cli Advil Warfarin
python3 -m medlens.cli --format text Advil Warfarin Paracetamol "Mystery Pill"
```

### Model-agnostic agent wrapper

Files:

- `medlens/agent.py`
- `tests/test_agent.py`

Implemented:

- `LlmProvider` protocol
- offline `TemplateProvider`
- Gemini HTTP provider
- AWS Bedrock Claude provider
- CLI support for `--format agent` and `--format agent-json`
- interactive terminal chat through `--chat`

The agent first builds the deterministic structured report, then asks the model
to explain that report. The model is not the evidence authority.

## Evidence Semantics

The CSVs are screening/reference DDI-ADE signals. Product wording should reflect
that:

- “local DDI reference signal”
- “known/reference interaction”
- “screening output”
- “not patient-specific medical advice”

Avoid wording that implies diagnosis, definitive causality, or complete
interaction coverage.

## Next Steps

1. Improve normalization coverage.
   - Query `ddi_import_issue`.
   - Add safe obvious canonical names and aliases.
   - Rebuild both SQLite artifacts.
   - Track coverage improvement in `BUILD_PROGRESS.md`.

2. Build a demo/evaluation set.
   - Add a small checked list of medication regimens.
   - Include expected normalized ingredients, expected flagged pairs, and
     expected unresolved behavior.
   - Use it for CLI, future Gemma wrapper, and Android demos.

3. Add report policy fields.
   - Separate verified findings, unresolved inputs, caveats, and next-step
     guidance slots.
   - Keep guidance templated/deterministic before adding Gemma.

4. Expand the model wrapper.
   - Feed only the structured report to the model.
   - Require the model to preserve severity and evidence status.
   - Add a deterministic verifier for generated answers.
   - Keep providers swappable so CLI APIs can be replaced by a local mobile LLM.

5. Build OCR/Android.
   - Camera/OCR extracts candidate names.
   - User confirms/edits names.
   - App runs the same local SQLite tools.
   - Gemma explains the already-built report.

6. Clean repo dependencies.
   - Trim fine-tuning/classifier dependencies from `pyproject.toml` when no
     longer needed.
   - Keep generated SQLite outputs ignored.

## Out Of Scope For Current MVP

- LLM fine-tuning.
- Separate ML severity classifier.
- FAERS/PostgreSQL-derived pair priors.
- Patient-specific clinical decision support.
- Online lookup as an authority for safety findings.
