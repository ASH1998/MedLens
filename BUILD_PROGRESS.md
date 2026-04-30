# MedLens Build Progress

This document is the durable build log for the current MedLens pivot. It should be updated after each module so work can resume from the repo state without depending on chat context.

## Current Architecture

MedLens is being built as an on-device medication safety agent:

- Gemma handles dialogue, explanation, and follow-up questions.
- Local deterministic tools are the authority for normalization, evidence lookup, severity synthesis, and final structured reports.
- SQLite artifacts provide portable evidence for phone and dashboard runtimes.
- No fine-tuned classifier or separate ML safety model is planned for the current MVP.
- The active MVP uses the curated DDI-ADE CSVs in `data/raw/DDI/` only.
- FAERS/PostgreSQL data is historical/build context and is not part of the current product path.

## Module Sequence

1. `normalization.sqlite` builder: canonical ingredients plus aliases, salts, regional names, and OCR-friendly variants. Complete.
2. DDI `evidence.sqlite` builder: known/reference interaction summaries plus raw DDI signal rows from regional DDI-ADE CSVs. Complete.
3. Deterministic lookup tools: normalization, pair lookup, known interaction lookup, adverse-effect lookup. Complete.
4. Severity consensus and structured report builder. Complete.
5. CLI/local harness for medication-list testing. Complete.
6. Normalization coverage improvement from `ddi_import_issue`. In progress as an iterative loop.
7. Demo/evaluation regimen set.
8. Model-agnostic agent prompt/tool loop. In progress.
9. OCR/phone flow.

## Module 1: Normalization Artifact

Status: complete.

Goal:

- Build the first `normalization.sqlite` artifact.
- Seed it with common US/India/EU outpatient ingredients, aliases, and safety-oriented legacy aliases already present in the repo.
- Provide a deterministic builder that can be rerun at any time.

Files:

- `medlens/artifacts/build_normalization.py`
- `medlens/artifacts/common_meds.py`
- `medlens/artifacts/schema.py`

Run:

```bash
python3 -m medlens.artifacts.build_normalization --output data/artifacts/normalization.sqlite
```

Acceptance criteria:

- Creates `drug` and `drug_alias` tables.
- Stores canonical ingredients and multiple aliases per ingredient.
- Enforces deduplication on normalized aliases.
- Supports exact lookup by normalized OCR/user text.

Verification:

```bash
python3 -m unittest tests.test_normalization_artifact
```

Current output:

- `933` canonical entries
- `1,339` aliases

Seed sources and assumptions:

- US outpatient anchor: ClinCalc DrugStats Top 300 of 2023, MEPS-derived, DrugStats version 2025.08.
- EU/UK anchor: OpenPrescribing/NHS chemical-name coverage for England prescribing.
- India anchor: NLEM/Jan Aushadhi-style essential/generic medicine coverage.
- Legacy safety supplement: high-signal names from earlier repo data exploration. This is baked into the seed list and does not require Postgres/FAERS for the current MVP.
- DDI unresolved coverage supplement: high-impact unresolved names from `ddi_import_issue`.

Limitations:

- This is still a normalization seed, not a final clinical drug ontology.
- The source-derived layer has broad canonical coverage but fewer brand aliases than the curated layer.
- Some source-derived combination products are preserved as combination names until we add active-ingredient decomposition rules.
- Legacy high-signal entries improve safety coverage but are not a proxy for common outpatient use.

## Module 2: DDI Evidence Artifact

Status: complete.

Goal:

- Import regional DDI-ADE CSVs from `data/raw/DDI`.
- Normalize `drug1` and `drug2` through `normalization.sqlite`.
- Preserve raw DDI signal rows and aggregate them into pair-level `known_interaction` summaries.
- Preserve unresolved rows in an issue table for normalization review.

Files:

- `medlens/artifacts/build_evidence.py`
- `medlens/artifacts/schema.py`
- `tests/test_evidence_artifact.py`

Run:

```bash
python3 -m medlens.artifacts.build_evidence \
  --input-dir data/raw/DDI \
  --normalization-db data/artifacts/normalization.sqlite \
  --output data/artifacts/evidence.sqlite
```

Current output:

- `19,706` known interaction pair summaries
- `99,813` pair adverse-effect rows
- `157,600` raw DDI signal rows
- `16,896` unresolved import issue rows
- `4` source files imported
- artifact size: about `249 MB`

Current source-file import stats:

| Source | Rows seen | Rows imported | Rows unresolved | Unique pairs imported |
|---|---:|---:|---:|---:|
| EU/EEA | 58,567 | 50,631 | 7,936 | 8,660 |
| India expanded | 55,297 | 48,382 | 6,915 | 14,393 |
| India prioritized | 10,430 | 10,100 | 330 | 1,906 |
| USA | 33,306 | 31,591 | 1,715 | 5,753 |

Example verified import:

- `ibuprofen + warfarin`
- severity: `Major`
- row count: `25`
- regions: `eu/eea`, `india`, `india_expanded`, `us`
- top effects include gastrointestinal bleeding, melena, intracranial hemorrhage, hematuria, easy bruising, acute anemia, major bleeding.

Verification:

```bash
python3 -m unittest tests.test_evidence_artifact tests.test_normalization_artifact
python3 -m compileall medlens tests
```

## Module 3: Deterministic Local Lookup Tools

Status: complete.

Goal:

- Provide Python tools that normalize user/OCR medication names.
- Look up known/reference interactions from `evidence.sqlite`.
- Return structured data for the future Gemma agent, not generated prose.

Files:

- `medlens/tools/local_safety.py`
- `medlens/tools/__init__.py`
- `tests/test_local_safety_tools.py`

Implemented tools:

- `normalizeMedicationNames`
- `lookupKnownInteraction`
- `getKnownInteractionEffects`
- raw DDI supporting row retrieval
- unresolved-medication handling

Current Python API:

```python
from medlens.tools.local_safety import MedicationSafetyStore

store = MedicationSafetyStore()
normalized = store.normalize_medication_names(["Paracetamol", "Advil"])
interaction = store.lookup_known_interaction("Advil", "Warfarin")
```

Real artifact smoke test:

- `Paracetamol -> acetaminophen`
- `Advil -> ibuprofen`
- `Mystery Pill -> unresolved`
- `Advil + Warfarin -> ibuprofen + warfarin`
- severity: `Major`
- source regions: `eu/eea`, `india`, `india_expanded`, `us`
- raw supporting DDI rows are returned from the local CSV-derived evidence

Verification:

```bash
python3 -m unittest tests.test_local_safety_tools tests.test_evidence_artifact tests.test_normalization_artifact
python3 -m compileall medlens tests
```

## Module 4: Structured Report Synthesis

Status: complete.

Goal:

- Accept a full medication list.
- Normalize every medication.
- Deduplicate resolved canonical ingredients.
- Enumerate all resolved pairs.
- Call `lookupKnownInteraction` for each pair.
- Rank findings by severity and support.
- Return a deterministic structured report with unresolved medications and limitations.

Files:

- `medlens/tools/local_safety.py`
- `tests/test_local_safety_tools.py`

Implemented API:

```python
from medlens.tools.local_safety import MedicationSafetyStore

store = MedicationSafetyStore()
report = store.build_structured_report(["Advil", "Warfarin", "Paracetamol"])
```

Real artifact smoke test:

- input: `Advil`, `Warfarin`, `Paracetamol`, `Mystery Pill`
- checked pairs: `3`
- unresolved: `Mystery Pill`
- overall severity: `Major`
- evidence status: `verified_reference_findings_with_unresolved_inputs`
- findings:
  - `ibuprofen + warfarin`, `Major`, `25` rows
  - `acetaminophen + warfarin`, `Major`, `13` rows
  - `acetaminophen + ibuprofen`, `Major`, `5` rows

Verification:

```bash
python3 -m unittest tests.test_local_safety_tools tests.test_evidence_artifact tests.test_normalization_artifact
python3 -m compileall medlens tests
```

## Module 5: CLI Local Harness

Status: complete.

Goal:

- accept a list of medication names,
- call `build_structured_report`,
- print JSON by default,
- print a human-readable report with `--format text`,
- make it easy to test demo regimens before adding dashboard/Gemma/OCR.

Files:

- `medlens/cli.py`
- `tests/test_cli.py`
- `pyproject.toml`

Run:

```bash
python3 -m medlens.cli Advil Warfarin
python3 -m medlens.cli --format text Advil Warfarin Paracetamol "Mystery Pill"
```

Installed script entrypoint after package install:

```bash
medlens-report Advil Warfarin
```

Real artifact smoke test:

- `python3 -m medlens.cli --format text Advil Warfarin Paracetamol "Mystery Pill"`
- overall severity: `Major`
- checked pairs: `3`
- unresolved: `Mystery Pill`
- findings:
  - `ibuprofen + warfarin`
  - `acetaminophen + warfarin`
  - `acetaminophen + ibuprofen`

Verification:

```bash
python3 -m unittest tests.test_cli tests.test_local_safety_tools tests.test_evidence_artifact tests.test_normalization_artifact
python3 -m compileall medlens tests
```

## Module 6: Normalization Coverage Improvement

Status: complete.

Goal:

- Use `ddi_import_issue` to identify high-impact unresolved medication names.
- Add safe obvious canonical seeds and aliases.
- Rebuild `normalization.sqlite`.
- Rebuild full raw `evidence.sqlite`.
- Measure import coverage improvement.

Files:

- `medlens/artifacts/common_meds.py`

Changes:

- Added `DDI_UNRESOLVED_COVERAGE_SUPPLEMENT`.
- Added `DDI_ALIAS_SUPPLEMENT` for obvious aliases such as `aspirin low dose -> aspirin`, `5-fluorouracil -> fluorouracil`, `ciclosporin -> cyclosporine`, and `sodium valproate -> valproate`.

Current output after rebuild:

- `933` canonical entries, up from `785`.
- `1,339` aliases, up from `1,076`.
- `19,706` known interaction pair summaries, up from `10,448`.
- `99,813` pair adverse-effect rows, up from `53,651`.
- `157,600` raw DDI signal rows preserved.
- `16,896` unresolved import issue rows, down from `82,100`.
- artifact size: about `249 MB` for `evidence.sqlite`, `304 KB` for `normalization.sqlite`.

Current source-file import stats:

| Source | Rows seen | Rows imported | Rows unresolved | Unique pairs imported |
|---|---:|---:|---:|---:|
| EU/EEA | 58,567 | 50,631 | 7,936 | 8,660 |
| India expanded | 55,297 | 48,382 | 6,915 | 14,393 |
| India prioritized | 10,430 | 10,100 | 330 | 1,906 |
| USA | 33,306 | 31,591 | 1,715 | 5,753 |

Remaining top unresolved names include:

- `fluindione`
- `elvitegravir cobicistat`
- `mycophenolic acid`
- `ixekizumab`
- `metamizole`
- `pipamperone`
- `flupentixol`
- `dupilumab`
- `fluphenazine`
- `etizolam`

Verification:

```bash
python3 -m medlens.artifacts.build_normalization --output data/artifacts/normalization.sqlite
python3 -m medlens.artifacts.build_evidence --input-dir data/raw/DDI --normalization-db data/artifacts/normalization.sqlite --output data/artifacts/evidence.sqlite
python3 -m unittest tests.test_cli tests.test_local_safety_tools tests.test_evidence_artifact tests.test_normalization_artifact
```

Smoke test:

```bash
python3 -m medlens.cli Minocycline Isotretinoin --effect-limit 3
```

Result:

- `isotretinoin + minocycline`
- severity: `Major`
- row count: `19`

## Next Module Candidate

Module 7 should improve normalization coverage and add a small demo/evaluation
regimen set for the CSV-only evidence path.

The known-interaction path is working end-to-end. We are intentionally not
adding FAERS-derived pair priors right now because the current MVP should rely
only on the curated DDI-ADE CSVs.

## Module 7: Model-Agnostic Agent Wrapper

Status: initial implementation complete.

Goal:

- Build a CLI-first agent that uses the deterministic local report as evidence.
- Keep LLM providers swappable so API providers can be replaced by a local
  mobile model later.
- Prevent the model from becoming the authority for safety findings.

Files:

- `medlens/agent.py`
- `medlens/cli.py`
- `tests/test_agent.py`

Implemented:

- `LlmProvider` protocol.
- Offline `TemplateProvider` for deterministic development/tests.
- Gemini HTTP provider using `GOOGLE_API_KEY` and `GOOGLE_MODEL`.
- AWS Bedrock Claude provider using `AWS_REGION`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, optional `AWS_SESSION_TOKEN`, and `CLAUDE_MODEL`.
- CLI formats:
  - `--format agent`
  - `--format agent-json`
- Interactive terminal chat:
  - `--chat`

Run:

```bash
python3 -m medlens.cli --format agent --provider template Advil Warfarin
python3 -m medlens.cli --format agent --provider gemini Advil Warfarin
python3 -m medlens.cli --format agent --provider bedrock Advil Warfarin
python3 -m medlens.cli --chat --provider bedrock
```

Next hardening:

- Add deterministic response verification against the structured report.
- Add a demo/evaluation set with expected findings.
- Add a future local-model provider for mobile runtime.

Update:

- Fixed Bedrock SigV4 signing for model IDs with colons by matching Bedrock's
  expected double-encoded canonical model path while keeping the actual request
  path single-encoded.
- Added `--chat` for direct terminal sessions with `/meds`, `/report`, and
  `/quit` commands.
- Live Bedrock smoke test passed with `python3 -m medlens.cli --format agent
  --provider bedrock Hi`; the model returned an unresolved-input response
  instead of inventing medication findings.

## Module 8: Rich Terminal Chat Plan

Status: planned.

Plan document:

- `RICH_TERMINAL_CHAT_PLAN.md`

Scope:

- Improve the CLI with Rich and prompt_toolkit.
- Add a real chat module with session state, natural-language medication
  extraction, rich structured report rendering, and broader educational health
  Q&A guarded by local evidence boundaries.

## Evidence Lookup Order

Current implemented lookup order:

1. Normalize medication names through `normalization.sqlite`.
2. Search DDI reference evidence in `known_interaction`.
3. Return pair summary, top effects, and raw supporting rows from `ddi_raw_signal`.
4. If no DDI reference match exists, return `found=false`.

Rationale:

- DDI raw/reference data has mechanisms, risk flags, source URLs, and regional caveats, so it should be the first authority layer.
- For now, no FAERS fallback is planned. Missing DDI evidence should remain an explicit no-local-reference-finding result.

## Raw DDI Data Assessment

Location:

```text
data/raw/DDI/
```

Files inspected:

- `usa_prioritized_ddi_ade_signals.csv`
- `eu_eea_prioritized_ddi_ade_signals.csv`
- `india_prioritized_ddi_ade_signals.csv`
- `india_expanded_prioritized_ddi_ade_signals.csv`

Current aggregate shape:

- `157,600` DDI-ADE signal rows
- `24,332` unique unordered drug pairs
- `747` unique drug strings
- row-level adverse-effect records, not already pair-level summaries

Assessment:

- Relevant for the MVP.
- Best fit is `known_interaction` / curated regional DDI-ADE import in `evidence.sqlite`.
- It should not be treated as strict patient-specific clinical ground truth.
- It should be stored with provenance, source URLs, region focus, evidence basis, and the existing “screening only / not clinical decision” caveats.

Implementation notes for Module 2:

- Normalize `drug1` and `drug2` through `normalization.sqlite`.
- Preserve unresolved drug names for review instead of silently dropping them.
- Collapse multiple adverse-effect rows for the same pair into one pair summary with top effects.
- Map severity labels: `high -> Major`, `medium/moderate -> Moderate`, `low -> Minor`.
- Keep regional source tags: `us`, `eu/uk`, `india`, `india_expanded`.
- Use DDI CSVs as the current curated/reference evidence source.

Observed normalization coverage with current `normalization.sqlite` seed:

- India prioritized: `158/192` drugs covered, `1,472/1,978` pairs with both drugs covered.
- India expanded: `337/532` drugs covered, `8,539/16,923` pairs with both drugs covered.
- USA: `272/404` drugs covered, `3,031/6,188` pairs with both drugs covered.
- EU/EEA: `311/531` drugs covered, `4,002/10,618` pairs with both drugs covered.

Next normalization gaps include drug classes/placeholders, combination products, spelling variants, and specialty drugs such as `ciclosporin/cyclosporine`, `frusemide/furosemide`, `insulin nph`, `lopinavir ritonavir`, and `combined oral contraceptive pill`.

## Decisions

- Generated SQLite artifacts are ignored by git because they are reproducible build outputs.
- Module 1 focuses on deterministic normalization only. Pair evidence export is intentionally left for Module 2 so we can review and confirm each step.
