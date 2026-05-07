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
medlens.cli Advil Warfarin
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

Status: first terminal-chat slice implemented.

Plan document:

- `RICH_TERMINAL_CHAT_PLAN.md`

Scope:

- Improve the CLI with Rich and prompt_toolkit.
- Add a real chat module with session state, natural-language medication
  extraction, rich structured report rendering, and broader educational health
  Q&A guarded by local evidence boundaries.

Implemented first slice:

- Added provider-neutral deterministic tool registry and JSON-safe dispatch in
  `medlens/tools/registry.py`.
- Added reusable chat session state in `medlens/chat/session.py`.
- Added slash command parsing in `medlens/chat/commands.py`.
- Added Rich-capable rendering with plain-terminal fallback in
  `medlens/chat/renderer.py`.
- Added interactive app wiring in `medlens/chat/app.py`.
- Rewired `python3 -m medlens.cli --chat` through the new chat app.
- Added `search_drug_aliases` and `known_alias_terms` to
  `MedicationSafetyStore` for alias search and lightweight natural-language
  medication extraction.
- Added `rich` and `prompt_toolkit` as project dependencies; the code still
  falls back cleanly when they are absent in a lean interpreter.

Current chat capabilities:

- Boots without requiring an initial medication list.
- Supports `/meds`, `/add`, `/remove`, `/report`, `/why`, `/sources`, `/trace`,
  `/clear`, `/provider`, `/help`, and `/quit`.
- Detects known medication aliases in short natural-language messages and adds
  them to session state before running the structured report.
- Captures deterministic tool traces for `/trace`.
- Keeps existing `--format json`, `text`, `agent`, and `agent-json` paths
  working.

Verification:

```bash
python3 -m unittest tests.test_chat_commands tests.test_tool_registry tests.test_cli tests.test_agent tests.test_local_safety_tools tests.test_evidence_artifact tests.test_normalization_artifact
python3 -m compileall medlens tests
python3 -m medlens.cli --format text Advil Warfarin
```

Next hardening:

- Add verifier v2 before displaying cloud-provider model output.
- Add the eval harness and richer Rich table snapshots.

Update:

- Added `medlens/agent_loop.py`, a native tool-calling turn loop over the
  deterministic SQLite tool registry.
- Added provider-neutral `ToolCall` and `ToolModelResponse` types in
  `medlens/agent.py`.
- Added `generate_with_tools(...)` implementations for:
  - `TemplateProvider`: deterministic scripted tool calls for offline tests.
  - `BedrockProvider`: Bedrock Claude native `tools` / `tool_use` /
    `tool_result` message shape.
  - `GeminiProvider`: Gemini `functionDeclarations` / `functionCall` /
    `functionResponse` message shape.
- Rewired chat turns to use `run_agent_turn(...)` instead of the old
  single-shot report explanation path.
- Rewired one-shot `--format agent` and `--format agent-json` to use the
  native loop. `agent-json` now includes `used_tools` and `fallback_used`.
- Tool-call rounds are capped at `6`, tool calls per round at `4`, and each
  turn has a `30s` budget before deterministic report fallback.
- Provider context is currently turn-local so every displayed answer is tied
  to tool results from the current turn, not stale earlier turns.

Current native-tool status:

- SQLite tools are native and deterministic through `tools/registry.py`.
- Template provider exercises the same native dispatch path locally.
- Bedrock/Gemini now have native tool-call adapters, but live cloud-provider
  verification is still pending until verifier v2 lands.

Additional verification:

```bash
python3 -m unittest tests.test_agent_loop tests.test_chat_commands tests.test_tool_registry tests.test_cli tests.test_agent
python3 -m medlens.cli --format agent-json --provider template Advil Warfarin
python3 -m compileall medlens tests
```

Clarification update:

- Added a deterministic clarification gate before native tool dispatch in chat.
- If a message looks like a medication-list statement but no known aliases are
  confidently extracted, MedLens asks one focused clarification question instead
  of running an empty report.
- Clear aliases still proceed directly into native tools; for example
  `I am taking dolo 650 along with ondansetron` resolves to
  `acetaminophen + ondansetron` and runs the report.
- Unclear text such as `I am taking dolo with ondasetron` now asks the user to
  confirm exact brand/generic names and strength.
- Added a prompt rule to `TOOL_LOOP_SYSTEM_PROMPT`: ask one focused
  clarification question before checking unclear medication names.

Verification:

```bash
python3 -m unittest tests.test_chat_commands tests.test_agent_loop tests.test_cli tests.test_agent
python3 -c "from unittest.mock import patch; from medlens.cli import main; import builtins; inputs=iter(['I am taking dolo with ondasetron', '/quit']);\nwith patch.object(builtins, 'input', lambda prompt='': next(inputs)):\n    raise SystemExit(main(['--chat','--provider','template']))"
python3 -c "from unittest.mock import patch; from medlens.cli import main; import builtins; inputs=iter(['I am taking dolo 650 along with ondansetron', '/quit']);\nwith patch.object(builtins, 'input', lambda prompt='': next(inputs)):\n    raise SystemExit(main(['--chat','--provider','template']))"
```

Debug trace update:

- Added `--debug-trace <path>` to write native tool traces as JSONL.
- One-shot `--format agent` / `agent-json` appends one JSON object per run.
- Chat appends one JSON object per answered tool-loop turn.
- Trace payloads include provider, fallback status, used tool names, per-tool
  args/result/error/duration, final report, and final text.

Verification:

```bash
python3 -m unittest tests.test_agent
python3 -m medlens.cli --format agent --provider template --debug-trace /tmp/medlens-trace.jsonl Advil Warfarin
```

Clarification regression fix:

- Fixed partial medication extraction. A message such as
  `i am taking dolo6 and ondansetron` previously allowed the model path to run
  because `ondansetron` matched, even though `dolo6` was unclear.
- Chat now blocks the tool loop when any medication-looking phrase in a list
  statement is unclear.
- The session stores pending unclear medication names and recognized names, so
  a follow-up like `its a brand name` stays in clarification mode instead of
  falling back to general education.
- Clarification prompts no longer render the generic grounding footer.

Verification:

```bash
python3 -m unittest tests.test_chat_commands tests.test_agent
python3 -c "from unittest.mock import patch; from medlens.cli import main; import builtins; inputs=iter(['Hello!', 'i am taking dolo6 and ondansetron', 'its a brand name', '/quit']);\nwith patch.object(builtins, 'input', lambda prompt='': next(inputs)):\n    raise SystemExit(main(['--chat','--provider','template']))"
```

Agent-owned tool flow update:

- Removed chat-side medication extraction and clarification routing for
  non-slash messages.
- Chat now sends raw user text directly into `run_agent_turn(...)`; the
  provider decides whether to call tools, ask a clarification question, or
  answer with an educational fallback.
- `TOOL_LOOP_SYSTEM_PROMPT` now explicitly instructs the provider to:
  - extract medication names itself,
  - call `normalize_medications` first for medication-list statements,
  - call `search_drug_aliases` for unresolved names,
  - avoid checking only the recognized subset unless the user asks for that,
  - call `add_medications` only after names are clear enough.
- `TemplateProvider` now mirrors that architecture for offline tests:
  - raw user text -> `normalize_medications`,
  - unresolved text -> `search_drug_aliases`,
  - clear names -> `add_medications` + `build_structured_report`,
  - unclear names -> clarification text.
- Follow-up clarification context is handled from the chat transcript inside
  the provider path. Example:
  - `i am taking dolo6 and ondansetron` asks about `dolo6` and recognizes
    `ondansetron`.
  - `its a brand name` keeps asking for the exact brand/strength.
  - `its Dolo 650` merges the clarified medicine with the previously
    recognized `ondansetron` and runs the report.

Verification:

```bash
python3 -m unittest tests.test_agent_loop tests.test_agent
python3 -c "from unittest.mock import patch; from medlens.cli import main; import builtins; inputs=iter(['Hello!', 'i am taking dolo6 and ondansetron', 'its a brand name', 'its Dolo 650', '/quit']);\nwith patch.object(builtins, 'input', lambda prompt='': next(inputs)):\n    raise SystemExit(main(['--chat','--provider','template']))"
```

Source/provenance update:

- Fixed report propagation for provider calls that pass explicit
  `medication_names` to `build_structured_report`. The loop no longer
  overwrites that report with an empty session report.
- Matched findings now include compact source/provenance in deterministic
  agent output:
  - source regions,
  - source bases,
  - up to two source URLs.
- Structured report rendering also shows source basis/URLs for matched
  findings.
- No-match cases now say no local DDI reference signal was found and that no
  pair-specific source is available because no local finding matched.
- Prompt rules now explicitly prohibit saying a no-match pair is safe.

Verification:

```bash
python3 -m unittest tests.test_agent_loop tests.test_agent tests.test_cli
python3 -c "from unittest.mock import patch; from medlens.cli import main; import builtins; inputs=iter(['I am taking Dolo 650 and ondansetron', '/quit']);\nwith patch.object(builtins, 'input', lambda prompt='': next(inputs)):\n    raise SystemExit(main(['--chat','--provider','template']))"
./medlens.cli --format agent --provider template Advil Warfarin
```

Default response style update:

- Tightened `AGENT_SYSTEM_PROMPT` and `TOOL_LOOP_SYSTEM_PROMPT` for a concise,
  professional default tone.
- Default answers should now be 2-5 short bullets/lines, show at most the top
  three findings, and avoid tables, emoji, long separators, dramatic headings,
  mechanisms, risk-factor lists, monitoring plans, or alternatives unless the
  user asks for details.
- Matched finding source/provenance is now inline and compact.
- No-match wording remains conservative: no local DDI reference signal found,
  not "safe".

Verification:

```bash
python3 -m unittest tests.test_agent_loop tests.test_agent
python3 - <<'PY'
from medlens.agent import TemplateProvider
from medlens.agent_loop import run_agent_turn
from medlens.chat.session import ChatSession
from medlens.tools.local_safety import MedicationSafetyStore
result = run_agent_turn(
    provider=TemplateProvider(),
    session=ChatSession(provider_name="template"),
    store=MedicationSafetyStore(),
    user_message="am taking acetaminophen ondansetron fluorouracil azithromycin",
)
print(result.final_text)
PY
```

Entrypoint update:

- Added an exact console-script alias named `medlens.cli`.
- Added a repo-local executable wrapper at `./medlens.cli`.
- After `uv sync` or an editable install, the CLI can be invoked as:

```bash
medlens.cli Advil Warfarin
medlens.cli --chat --provider template
```

- From the repo root without installing scripts, invoke:

```bash
./medlens.cli Advil Warfarin
./medlens.cli --chat --provider template
```

- `medlens-report` and `medlens-agent` remain available aliases.
- `python3 -m medlens.cli ...` remains the no-install development fallback.

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

Expert-tone relaxation update:

- Rewrote `AGENT_SYSTEM_PROMPT` and `TOOL_LOOP_SYSTEM_PROMPT` to direct the model
  to behave like an expert clinical pharmacist talking with a patient, not a
  legal-disclaimer bot.
- Removed the "2-5 short bullets / no mechanisms / no risk factors / top-3 cap"
  restrictions. The model is now allowed (and encouraged) to use mechanism,
  top effects, region, source basis, and source URLs from the tool result
  directly in the answer.
- Stopped stacking disclaimers. One natural closing nudge is allowed for Major
  findings; closing nudges are discouraged for Moderate/Minor/no-finding cases.
- Tool-loop guidance now frames the SQLite tools as the source of truth and
  pushes the model to actually run them: `normalize_medications`,
  `search_drug_aliases`, `add_medications`, `build_structured_report`,
  `lookup_pair`, `get_pair_effects`, `severity_consensus`, `get_raw_signals`.
- Bedrock and Gemini providers now use `max_tokens`/`maxOutputTokens` of `1500`
  and temperature `0.4` so the relaxed prompt has room to produce a
  substantive expert answer.
- `_response_from_report` and `_deterministic_text_from_report` were softened
  to a warmer voice. Test markers (`Source:`, `Ask for details`, `Overall
  local evidence severity:`, `local screening output`,
  `gastrointestinal bleeding`) were preserved.
- `_educational_fallback_text` now reads as a conversational redirect rather
  than a stacked list of disclaimers.
- Chat welcome panel now opens with a one-line introduction and lists the
  privacy mode + local artifact sizes without alarming "leaves device" copy.
- The chat grounding-cue footer is suppressed when findings are present and
  reduced to a quiet `Couldn't match locally: ...` line only when there are
  unresolved medication names.

Files changed:

- `medlens/agent.py`
- `medlens/agent_loop.py`
- `medlens/chat/renderer.py`
- `tests/test_agent.py` (loosened a brittle prompt-wording assertion)

Citation follow-up:

- The cloud model was happy with the relaxed tone but kept dropping the
  source URLs the tool returned. Both `AGENT_SYSTEM_PROMPT` and
  `TOOL_LOOP_SYSTEM_PROMPT` now require a "Sources" section with every
  `source_urls` entry from the tool result (one URL per line, plus
  regions/bases when present). Silent omission of URLs is explicitly forbidden.
- Deterministic `_source_line_from_finding` (in both `medlens/agent.py` and
  `medlens/agent_loop.py`) now surfaces up to 4 URLs and 3 source bases per
  finding instead of 1, so the deterministic fallback also shows the same
  citations the cloud answer is asked to render.

Verification:

```bash
python3 -m unittest tests.test_agent_loop tests.test_agent tests.test_cli tests.test_chat_commands tests.test_tool_registry tests.test_local_safety_tools tests.test_normalization_artifact tests.test_evidence_artifact
python3 -m compileall medlens tests
python3 -m medlens.cli --format agent --provider template Advil Warfarin
```

Patient-facing chat accessibility update, 2026-05-06:

- Ran the terminal chat and found two issues affecting patient usability:
  - Natural wording such as `I take Advil and Warfarin. Is that okay?`
    was parsed too rigidly, treating trailing question text as part of the
    medicine name.
  - Broad questions such as `what medicines cant be taken with captopril`
    could not be answered because the tool layer only supported specific
    pair/list checks.
- Relaxed the offline template medication extraction so natural questions after
  medication names do not force unnecessary clarification.
- Reworked deterministic/template response text to lead with practical meaning:
  - avoids row-count-first phrasing,
  - uses warmer pharmacist-style language,
  - explains common medical terms such as gastrointestinal bleeding, QT
    prolongation, intracranial hemorrhage, torsades de pointes, and acute
    anemia in plain language,
  - keeps citations available without overwhelming the first answer.
- Added `MedicationSafetyStore.list_interactions_for_drug(...)`, which queries
  `known_interaction` for all local DDI reference pairs involving one resolved
  medication and ranks them by severity/evidence count.
- Added `list_interactions_for_drug` to `medlens/tools/registry.py` so cloud
  and template agents can answer broad "what interacts with X" questions using
  the same deterministic SQLite authority.
- Updated `TOOL_LOOP_SYSTEM_PROMPT` so provider-native tool loops know to call
  `list_interactions_for_drug` for questions such as `what medicines interact
  with X` or `what can't be taken with X`.
- Updated `TemplateProvider` to detect the same broad single-drug interaction
  intent offline and render a ranked local list.
- Live template chat smoke test:

```text
medlens> what medicines cant be taken with captopril
```

Returned ranked locally flagged matches including:

- `amiloride`
- `diclofenac`
- `eplerenone`
- `ibuprofen`
- `indomethacin`
- `ketorolac`
- `naproxen`
- `spironolactone`

The response now explicitly says this is not a universal do-not-take list; it
is a local reference list of combinations worth checking with a pharmacist or
prescriber.

- Fixed `medlens/chat/app.py` so non-TTY test/piped sessions fall back to
  `input(...)` instead of prompt_toolkit. This restored interactive CLI tests
  under `redirect_stdout` / mocked `input`.

Files changed in this slice:

- `medlens/agent.py`
- `medlens/agent_loop.py`
- `medlens/chat/app.py`
- `medlens/tools/local_safety.py`
- `medlens/tools/registry.py`
- `tests/test_agent.py`
- `tests/test_agent_loop.py`
- `tests/test_local_safety_tools.py`
- `tests/test_tool_registry.py`

Verification:

```bash
python3 -m unittest tests.test_normalization_artifact tests.test_evidence_artifact tests.test_local_safety_tools tests.test_cli tests.test_agent tests.test_tool_registry tests.test_agent_loop tests.test_chat_commands
python3 -m compileall medlens tests
```

Latest result:

```text
Ran 41 tests in 151.046s

OK
```

India common-medicine and generic DDI import, 2026-05-06:

- Reviewed the two new datasets:
  - `common_medicines_india_dataset_5000.csv`: 5,000 medicine records, 189
    unique generic/common names, 5,000 unique medicine IDs, no empty names,
    brands, or source URLs. This is good normalization/OCR support data rather
    than DDI evidence.
  - `india_common_generic_ddi_5000.csv`: 5,000 DDI rows, 3,521 unique unordered
    input pairs before normalization, no empty drugs/effects/source URLs, and a
    mix of `Major`, `Moderate`, and `Low-Moderate` rows. It is useful as
    screening evidence, but some rows are broad medication-safety signals
    (`duplicate therapy`, `polypharmacy monitoring burden`) rather than hard
    contraindication facts.
- Added `india_common_medicine` to `normalization.sqlite` so the full 5,000-row
  India common-medicine catalogue is queryable in SQLite.
- Extended `build_normalization.py` to import India common medicines into:
  - `india_common_medicine` for metadata such as strength/form, brands, local
    use context, risk flags, and source URLs;
  - `drug_alias` for OCR/user-input recovery using common generic names,
    synonym-style slash names, selected salt variants, and brand examples.
- Kept brand aliasing conservative: examples containing `component` are not
  mapped to a single ingredient, to avoid unsafe combo-product normalization.
- Added new DDI normalization coverage for remaining India generic DDI terms:
  `cilnidipine`, `febuxostat`, `formoterol`, `methylcobalamin`,
  `pitavastatin`, `teneligliptin`, `aluminium magnesium hydroxide`,
  `calcium citrate`, plus aliases such as `aspirin high dose`,
  `insulin regular`, and `vitamin d3`.
- Added `india_common_generic_ddi_5000.csv` to `build_evidence.py` as
  `india_common_generic`, including `Low-Moderate` severity normalization.
- Rebuilt artifacts:
  - `normalization.sqlite`: 981 drugs, 1,669 aliases, 5,000 India common
    medicine rows.
  - `evidence.sqlite`: 21,810 known interactions, 105,460 interaction effects,
    162,600 raw DDI signals, 15,696 unresolved import issues, 5 source files.
  - The new India generic DDI file imported completely after normalization
    coverage: 5,000 seen, 5,000 imported, 0 unresolved, 3,471 unique pairs.
- Smoke-tested local normalization:
  - `Dolo` -> `acetaminophen`
  - `Clavam` -> `amoxicillin clavulanate`
  - `Cilnidipine` -> `cilnidipine`
  - `Aspirin high dose` -> `aspirin`
  - `Insulin regular` -> `insulin`
- Smoke-tested terminal chat:

```text
medlens> what medicines cant be taken with captopril
```

Returned locally flagged captopril partners including `diclofenac`,
`ibuprofen`, `naproxen`, `amiloride`, `eplerenone`, `ketorolac`,
`spironolactone`, and `celecoxib`, with the appropriate caveat that this is not
a universal do-not-take list.

Also checked:

```text
medlens> I take Dolo and Clavam. what are these?
```

The chat resolved `Dolo` to `acetaminophen` and `Clavam` to
`amoxicillin clavulanate` through the rebuilt SQLite aliases.

Files changed in this slice:

- `medlens/artifacts/schema.py`
- `medlens/artifacts/build_normalization.py`
- `medlens/artifacts/build_evidence.py`
- `medlens/artifacts/common_meds.py`
- `tests/test_normalization_artifact.py`
- `tests/test_evidence_artifact.py`
- fixture call updates in tests that build temporary normalization DBs
- `CLAUDE.md`
- `BUILD_PROGRESS.md`

Verification:

```bash
python3 -m unittest tests.test_normalization_artifact tests.test_evidence_artifact tests.test_local_safety_tools tests.test_cli tests.test_agent tests.test_tool_registry tests.test_agent_loop tests.test_chat_commands
python3 -m compileall medlens tests
```

Latest result:

```text
Ran 43 tests in 181.666s

OK
```

SQLite runtime exploration tools, 2026-05-06:

- Added runtime tools for the SQLite tables that were previously only used
  indirectly or for build/debug:
  - `get_common_medicine_profile`
    reads `normalization.sqlite` tables `drug`, `drug_alias`, and
    `india_common_medicine` to explain a brand/common/generic medicine,
    including forms/strengths, India common-use context, brand examples,
    OTC/Rx context, risk flags, and source URLs.
  - `search_common_medicines`
    searches `india_common_medicine` by common name, brand examples, daily-life
    use, or therapeutic category.
  - `list_evidence_sources`
    reads `evidence_import_file` so the agent can show which DDI CSVs were
    loaded and how many rows/pairs resolved.
  - `list_import_issues`
    reads `ddi_import_issue` for unresolved import/debug review.
  - `get_full_raw_signals`
    returns full `ddi_raw_signal` support rows for pair-level auditing, while
    keeping the existing simplified `get_raw_signals` tool intact.
- Updated `TOOL_SCHEMAS` and dispatch in `medlens/tools/registry.py` so the new
  tools are available to Bedrock/Gemini/template agent loops.
- Updated `AGENT_SYSTEM_PROMPT` and `TOOL_LOOP_SYSTEM_PROMPT`:
  - normalization/OCR/brand/common-medicine questions should use
    normalization.sqlite-backed tools;
  - interaction/effect/severity/source/raw-row questions should use
    evidence.sqlite-backed tools;
  - dataset coverage questions should call `list_evidence_sources`;
  - unresolved/import/debug questions should call `list_import_issues`;
  - raw-row/audit questions should call `get_full_raw_signals`.
- Extended the offline template provider so local chat can use these tools
  without a cloud model:
  - `I take Dolo. what is this used for?` calls
    `get_common_medicine_profile`.
  - `what evidence sources are loaded?` calls `list_evidence_sources`.
  - `show import issues for unknown` calls `list_import_issues`.
- Added regression tests in:
  - `tests/test_local_safety_tools.py`
  - `tests/test_tool_registry.py`
  - `tests/test_agent_loop.py`

Current runtime table coverage:

| SQLite DB | Table | Runtime tool coverage |
| --- | --- | --- |
| `normalization.sqlite` | `drug` | normalization, alias search, common profile/search, pair/report tools through normalization |
| `normalization.sqlite` | `drug_alias` | normalization, alias search, session add/remove, pair/list/report tools |
| `normalization.sqlite` | `india_common_medicine` | `get_common_medicine_profile`, `search_common_medicines` |
| `evidence.sqlite` | `known_interaction` | pair lookup, single-drug interaction listing, report, effects, raw-signal tools, severity consensus, effect search |
| `evidence.sqlite` | `known_interaction_effect` | pair lookup, single-drug interaction listing, report, effects, effect search |
| `evidence.sqlite` | `ddi_raw_signal` | report/pair lookup raw support, `get_raw_signals`, `get_full_raw_signals`, severity consensus |
| `evidence.sqlite` | `evidence_import_file` | `list_evidence_sources` |
| `evidence.sqlite` | `ddi_import_issue` | `list_import_issues` |

Verification:

```bash
python3 -m unittest tests.test_normalization_artifact tests.test_evidence_artifact tests.test_local_safety_tools tests.test_cli tests.test_agent tests.test_tool_registry tests.test_agent_loop tests.test_chat_commands
python3 -m compileall medlens tests
```

Latest result:

```text
Ran 48 tests in 229.886s

OK
```

## Decisions

- Generated SQLite artifacts are ignored by git because they are reproducible build outputs.
- Module 1 focuses on deterministic normalization only. Pair evidence export is intentionally left for Module 2 so we can review and confirm each step.

## Mobile Evidence Artifact

Status: implemented and generated.

Goal:

- Keep the full raw DDI evidence available for phone/PWA runtimes while keeping
  the queryable SQLite artifact below the 100 MB mobile budget.
- Preserve source URLs because they are needed for evidence links in the app.
- Avoid dropping `ddi_raw_signal`; the raw rows contain useful mechanism,
  rationale, source, regional relevance, risk-flag, and caveat data.

Implementation:

- `medlens/artifacts/build_evidence.py` now supports `--compact-from`.
- The compact artifact keeps `known_interaction`,
  `known_interaction_effect`, `ddi_import_issue`, and `evidence_import_file`.
- Repeated raw-signal text is stored once in `raw_text_value`.
- Raw rows are stored in `ddi_raw_signal_compact` with integer references into
  `raw_text_value`.
- `ddi_raw_signal` remains available as a read-only SQLite view with the same
  columns as the full artifact, so existing read queries can continue to use
  `SELECT ... FROM ddi_raw_signal`.

Build:

```bash
.venv/bin/python -m medlens.artifacts.build_evidence \
  --compact-from data/artifacts/evidence.sqlite \
  --output data/artifacts/evidence.mobile.sqlite
```

Current output:

- `data/artifacts/evidence.sqlite`: about 260 MB.
- `data/artifacts/evidence.mobile.sqlite`: about 73 MB.
- `ddi_raw_signal`: 162,600 readable rows preserved.
- `ddi_import_issue`: 15,696 rows preserved.

Important runtime note:

- In `evidence.mobile.sqlite`, `ddi_raw_signal` is a view, not the physical
  storage table. Reads work. Writes to `ddi_raw_signal` do not. This is
  acceptable for the shipped mobile/PWA artifact, which should be read-only.

## PWA Phase 0 — Artifact Access + Storage Preflight

Status: complete (2026-05-07).

Reference: `docs/pwa_plan.md` Phase 0.

Anonymous HEAD against both pinned `resolve/main` URLs from a non-Hugging-Face
Origin returned 200 after redirect with full CORS support. The PWA download
path is unblocked.

Captured headers (2026-05-07, dataset commit `b3933aca1510fc8f12fd47a5280da7a0b8c3a88a`):

| Artifact | x-linked-size | Accept-Ranges | x-linked-etag (LFS SHA256) |
| --- | ---: | --- | --- |
| `normalization.sqlite` | 7,409,664 (≈7.4 MB) | bytes | `50356a54fb3d6ec131044ddc6b72bad02eea4a7c0682284174de6674e5515d92` |
| `evidence.mobile.sqlite` | 76,492,800 (≈73 MB) | bytes | `1883368fd0f40906baae189d35bd310a43d651adfe595dcb295ee92fdcdb15aa` |

CORS check: `curl -H "Origin: https://medlens.example.com"` against both URLs
mirrored the arbitrary Origin back in `Access-Control-Allow-Origin` on both the
HF redirect and the `cas-bridge.xethub.hf.co` CDN target, with
`Access-Control-Expose-Headers` including `X-Repo-Commit`, `ETag`,
`Accept-Ranges`, `Content-Range`. Cross-origin `fetch` from a deployed PWA
origin will work.

Versioning anchors confirmed available:

- `x-repo-commit` — dataset-change anchor.
- `x-linked-etag` — LFS object SHA256, used as the content/integrity anchor.

Outstanding Phase 0 items deferred (non-blocking for Phase 1 scaffolding but
required before Phase 6 UI / Phase 8 release):

- `navigator.storage.persist()` lock-in lands with `FirstRunSetup.tsx` in
  Phase 6.
- Play Store medical-app review spike scheduled before Phase 6 UI work.

## PWA Phase 1 — Scaffolding

Status: complete (2026-05-07).

Reference: `docs/pwa_plan.md` Phase 1.

New code lives entirely under `web/`. Nothing in the Python `medlens/` package,
its tests, or the existing CLI was changed.

Files added:

- `web/package.json` — Vite 6 + React 19 + TypeScript 5 + Vitest 2 + ESLint 9
  flat config + Prettier 3 + `tsx` runner. pnpm 10 is the package manager.
- `web/tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json` — split
  app/node TS projects with strict mode + `verbatimModuleSyntax`.
- `web/vite.config.ts`, `web/vitest.config.ts` — minimal Vite + Vitest configs;
  Vitest globals enabled.
- `web/eslint.config.js`, `web/.prettierrc`, `web/.prettierignore` —
  typescript-eslint + react-hooks + react-refresh.
- `web/index.html`, `web/public/manifest.webmanifest` — PWA shell entry +
  manifest stub (icons added in Phase 7).
- `web/src/main.tsx`, `web/src/ui/App.tsx`, `web/src/ui/index.css` — debug
  shell that runs the Phase 1 acceptance slice (HEAD both HF artifacts on load
  and render size + ETag + repo commit).
- `web/src/db/hf-fetch.ts` — pinned `ARTIFACT_URLS` constants and a `headArtifact`
  helper. **The two artifact URLs are constants; the file is the single source
  of truth that no other HF resource is ever fetched.**
- `web/src/db/__tests__/hf-fetch.test.ts` — Vitest coverage for URL pinning,
  HEAD parsing (`x-linked-size`, `x-linked-etag`, `x-repo-commit`,
  `accept-ranges`), and 4xx error propagation.
- `web/scripts/publish-hf.ts` — publish stub (real `@huggingface/hub` upload
  wired in Phase 2). Reads `data/artifacts/normalization.sqlite` and
  `data/artifacts/evidence.mobile.sqlite`.
- `web/README.md`, `web/.gitignore`, `web/src/vite-env.d.ts`.

Phase 1 acceptance slice (per plan):

> blank app loads; running `pnpm publish:hf` pushes both SQLite files to HF;
> the deployed app issues HEAD requests against the two `resolve/main/<file>`
> URLs and renders sizes + ETags on a debug page.

Implemented as `web/src/ui/App.tsx` driven by `headArtifact()` from
`web/src/db/hf-fetch.ts`. The publish path is stubbed; real upload lands in
Phase 2 alongside the streamed download.

Local verification:

```bash
cd web
pnpm install
pnpm exec tsc -b   # passes with no errors
pnpm lint          # passes
pnpm test          # 3/3 vitest cases pass
pnpm build         # tsc -b && vite build → dist/ ~62 KB gzip JS
```

Latest local results (2026-05-07):

- `tsc -b`: clean.
- `eslint .`: clean.
- `vitest run`: `3 passed` in `src/db/__tests__/hf-fetch.test.ts`.
- `vite build`: `dist/index.html 0.58 kB`, `dist/assets/index-*.js 197.25 kB
  (gzip 61.96 kB)`, `dist/assets/index-*.css 0.23 kB`.

Pinned URLs (only these two are ever fetched by the PWA):

- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/normalization.sqlite`
- `https://huggingface.co/datasets/ASHu2/medlens/resolve/main/evidence.mobile.sqlite`

Next: Phase 2 — streamed download with progress, OPFS persistence, ETag-based
freshness, and lazy `evidence.mobile.sqlite` open via sql.js.

## PWA Phase 2 — SQLite Delivery + Browser Storage

Status: complete (2026-05-07).

Reference: `docs/pwa_plan.md` Phase 2.

The PWA can now: detect OPFS, request storage persistence, stream both pinned
HF artifacts into OPFS with progress + Range-based resume, persist
ETag/X-Repo-Commit metadata, open `normalization.sqlite` eagerly via sql.js,
open `evidence.mobile.sqlite` lazily on first access, and check upstream
freshness without re-downloading.

Files added:

- `web/src/db/types.ts` — TypeScript row interfaces mirroring the Python
  dataclasses in `medlens/tools/local_safety.py`
  (`NormalizedMedication`, `KnownInteraction`, `InteractionEffect`,
  `RawDdiSignal`, `CommonMedicineRow`, `EvidenceImportFile`).
- `web/src/db/opfs.ts` — `BlobStore` interface plus an `OpfsBlobStore` backed
  by `navigator.storage.getDirectory()` + `FileSystemWritableFileStream` and a
  `MemoryBlobStore` for tests. Exposes `hasOpfs()` and `requestPersistence()`
  helpers so the first-run flow can lock in `navigator.storage.persist()`
  before download begins (per the Phase 0 lock-in).
- `web/src/db/meta.ts` — `MetaStore` interface + `LocalStorageMetaStore` and
  `MemoryMetaStore`. Persists `{ etag, repoCommit, contentLength, updatedAt }`
  per artifact so subsequent launches can decide between resume / refresh /
  short-circuit.
- `web/src/db/sqlite.ts` — `loadSqlJs()` and `openDatabase(Uint8Array)` using
  `sql.js@1.14.1`. WASM URL is resolved through Vite's
  `?url` import so the bundle is self-contained. Both DBs are opened from
  in-memory `Uint8Array`s per the v1 lock-in. wa-sqlite + OPFS-SAH remains the
  documented escape hatch if `evidence.mobile.sqlite` ever grows past ~150 MB.
- `web/src/db/stores.ts` — `openDbHandles(store)` returns `{ normalization,
  evidence(), close }`. `evidence` is a memoized async getter that opens the
  73 MB DB only on first call.
- `web/src/db/version.ts` — `checkForUpdate(filename, url, meta)` and
  `checkAllForUpdates(meta)` HEAD the pinned URLs and report
  `fresh | etag-changed | repo-commit-changed | no-local-copy`.

Files extended:

- `web/src/db/hf-fetch.ts` now exports `ARTIFACTS` (the canonical
  `{ key, filename, url }` list), `headArtifact()` (Phase 1), and a new
  `downloadArtifact()` that streams the body via `Response.body.getReader()`,
  writes through the `BlobStore` sink, calls `onProgress` at a configurable
  byte cadence, and resumes via `Range: bytes=<offset>-` when a partial OPFS
  file matches the persisted ETag. ETag mismatch on resume restarts the
  download fresh; full content on disk with matching ETag short-circuits.
  The two pinned URLs remain the only fetch surface.
- `web/src/ui/App.tsx` is now a Phase 2 first-run shell:
  - Detects OPFS, requests persistence, picks a `BlobStore`.
  - If both files are present and their sizes match the persisted
    `contentLength`, skips download and opens directly.
  - Otherwise renders per-artifact progress bars driven by `downloadArtifact`.
  - On completion, opens both DBs and runs the Phase 2 acceptance slice:
    `SELECT COUNT(*)` against `drug`, `drug_alias`, `known_interaction`, and
    `ddi_raw_signal`.

Tests added:

- `web/src/db/__tests__/opfs.test.ts` — `MemoryBlobStore` write-from-zero,
  resume-from-offset, mismatched-offset rejection, delete.
- `web/src/db/__tests__/meta.test.ts` — round-trip + per-filename isolation.
- `web/src/db/__tests__/version.test.ts` — all four freshness reasons.
- `web/src/db/__tests__/hf-fetch.test.ts` — extended with three new
  `downloadArtifact` cases:
  1. fresh download persists meta and reports progress;
  2. partial-on-disk + matching ETag resumes via `Range: bytes=5-`;
  3. ETag mismatch discards partial bytes and refetches.

Phase 2 acceptance slice (per plan):

> first launch shows a download dialog, persists files to OPFS, opens the DB,
> and a debug page runs `SELECT COUNT(*) FROM known_interaction` after reload
> with no network.

Implemented end-to-end. Verified locally:

```bash
cd web
pnpm exec tsc -b   # clean
pnpm lint          # clean
pnpm test          # 16/16 pass across 4 suites
pnpm build         # dist/ emits sql-wasm.wasm (323 KB gzip) + index JS (79 KB gzip)
```

Verification of the no-network reload + `SELECT COUNT(*)` slice itself
requires a real browser (OPFS isn't in vitest's node env); that manual check
is the next session's first action — load `pnpm dev`, complete the download,
DevTools → offline, reload, confirm rows render from OPFS.

Known v1 limitations (deferred):

- IndexedDB blob fallback for browsers without OPFS write — currently throws
  with a clear message. Fallback ships before TWA submission.
- `sql.js-httpvfs` desktop "no-download" mode — explicitly out of v1, not
  implemented; download-to-OPFS remains the default path per the plan.
- Real-browser cancel/resume UX — the underlying `downloadArtifact` supports
  `AbortSignal` and resumes via `Range`, but the cancel button lands with
  `FirstRunSetup.tsx` polish in Phase 6.

Next: Phase 3 — TS port of `MedicationSafetyStore` (verbatim SQL + ranking +
report builder) plus `TOOL_SCHEMAS` / `dispatch` from
`medlens/tools/registry.py`. Audit `local_safety.py` for `REGEXP` /
`create_function` / collation usage before porting (preflight grep already
clean).
