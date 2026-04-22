# MedLens — Revised Plan (On-Device Gemma Agent + Compressed Evidence Store)

**Status:** We are dropping the separate ML classifier and shifting to a single-agent architecture built around **base Gemma 4 E2B** running through **LiteRT-LM**. The focus is now:

1. run the reasoning agent fully on-device,
2. compress our FAERS-derived data into small, queryable mobile artifacts,
3. let the agent use **tools** to verify its answers against our local evidence,
4. support both **phone** and **dashboard** experiences from the same grounded backend logic.

The LLM is no longer paired with a trained prediction model. Instead, the system depends on **retrieval, deterministic verification tools, and strict prompting** so that every important answer can be backed by our data.

---

## 1. Goal

Build an **offline medication safety agent** that:

- takes medication inputs from camera OCR, typed names, or dashboard entry,
- normalizes drugs to active ingredients,
- checks pairwise and regimen-level evidence from our compressed FAERS-derived artifacts,
- uses **Gemma 4 E2B** to explain findings, ask follow-up questions, and generate user-friendly guidance,
- verifies all important claims using local tools before presenting the final output.

This changes the project from **"LLM + classifier"** to **"LLM agent + tool verification + compressed evidence store."**

---

## 2. Product Direction

### User experiences

#### A. Phone app
- Capture medicine bottles with camera.
- OCR extracts brand/generic text.
- RxNorm-style normalization maps to active ingredients.
- Agent checks interactions locally.
- User receives a severity-ranked report and optional chat follow-up.

#### B. Dashboard
- User or clinician enters drugs manually or uploads a medication list.
- Same agent logic runs against the same local/packaged evidence artifacts.
- Dashboard can expose more detailed evidence views:
  - matching FAERS-derived pairs,
  - outcome frequencies,
  - top reactions,
  - uncertainty or missing-data warnings.

### Shared principle
Both surfaces must use the **same grounded tool layer** so the model cannot generate answers that are disconnected from our data.

---

## 3. Architecture Overview

```text
┌──────────────────── Client: Phone / Dashboard ────────────────────┐
│ Input: camera OCR, typed meds, uploaded list                      │
│                       ↓                                           │
│        normalizeMedicationNames() → ingredient set                │
│                       ↓                                           │
│                Gemma 4 E2B Agent (LiteRT-LM)                      │
│                       ↓                                           │
│       ┌──────────────── Tool Verification Layer ───────────────┐  │
│       │ lookupPairEvidence(a,b)                                │  │
│       │ lookupRegimenEvidence(drugs[])                         │  │
│       │ getTopReactions(drugs[] or pair)                       │  │
│       │ checkSeverityConsensus(findings[])                     │  │
│       │ buildStructuredReport(findings[])                      │  │
│       └────────────────────────────────────────────────────────┘  │
│                       ↓                                           │
│         grounded explanation + structured safety report           │
└───────────────────────────────────────────────────────────────────┘
```

### Core rule
The agent may **reason and summarize**, but it must rely on tools for:
- interaction confirmation,
- severity support,
- frequency/evidence lookup,
- final report construction.

The final structured output should always be derived from tool results, not free-form generation.

---

## 4. What We Keep From Existing Data Work

We still use the existing FAERS-derived `medlens.training_examples` and related source data, but **not for training a model**.

Instead, the dataset becomes the source for:
- compressed pairwise interaction evidence,
- compressed regimen signatures,
- severity summaries,
- common reaction summaries,
- exemplar cases for retrieval,
- verification metadata shown to the agent.

So the data pipeline remains valuable, but the output changes from **training corpus** to **evidence artifacts**.

---

## 5. Data Compression Strategy

This is now one of the most important workstreams.

### Objective
Convert large FAERS-derived tables into compact artifacts that can be shipped with the app or dashboard package while preserving enough signal for trustworthy interaction verification.

### 5.1 Artifact set

#### A. Pair Evidence Store
A compact key-value store:

- key: `(drug_a, drug_b)` normalized and order-independent
- value:
  - severity distribution,
  - total supporting cases,
  - top reported reactions,
  - representative outcomes,
  - confidence/evidence tier,
  - optional example case references

**Recommended format:** SQLite

Why:
- easy mobile support,
- indexed exact lookup,
- transparent inspection,
- works well for deterministic tool calls.

#### B. Regimen Evidence Store
A compact store for common multi-drug combinations:

- key: hashed sorted ingredient list
- value:
  - known co-occurrence counts,
  - top risky constituent pairs,
  - regimen-level observed severity patterns,
  - top reactions,
  - evidence sufficiency flag

**Recommended format:** SQLite or compact JSONL-to-SQLite build step.

#### C. Drug Normalization Map
A compressed lookup from noisy text / brand names / OCR variants to normalized ingredients.

- brand → ingredient
- common OCR mistakes → corrected token
- ingredient aliases

**Recommended format:** SQLite table with FTS or indexed lookup.

#### D. Optional Dashboard-Only Extended Evidence Pack
A larger artifact for desktop/dashboard deployments:
- more exemplar cases,
- richer statistics,
- more neighbor regimens,
- expanded evidence text.

This lets us keep the phone package lean while giving the dashboard more explainability.

### 5.2 Compression methods

#### Tiered packaging
Maintain two bundles:

1. **Mobile bundle**
   - top drugs,
   - top pairs,
   - most common regimens,
   - aggressively deduplicated reaction strings,
   - capped exemplar count.

2. **Dashboard bundle**
   - broader coverage,
   - additional evidence rows,
   - richer drill-down support.

#### Dictionary compression
- Deduplicate repeated reaction terms, outcomes, and severity labels into integer dictionaries.
- Replace repeated drug strings with vocabulary IDs.
- Store pair keys as sorted integer tuples instead of repeated text.

#### Frequency pruning
- Keep all high-risk and clinically common pairs.
- Downrank or remove extremely low-support, low-value combinations from the mobile pack.
- Preserve a fallback “insufficient evidence” behavior when a pair is absent.

#### Pre-aggregation
- Store summaries, not raw case rows, on-device.
- Examples:
  - counts by severity,
  - top 5 reactions only,
  - compressed evidence snippets,
  - representative cases rather than all cases.

#### Hash-based regimen encoding
- Sort ingredient IDs,
- encode regimen signature compactly,
- use hash keys for fast lookup and smaller storage.

### 5.3 Mobile size target

Initial target:

| Artifact | Phone target |
|---|---:|
| Gemma model bundle | determined by LiteRT-LM-compatible Gemma package |
| Pair evidence SQLite | 5–20 MB |
| Regimen evidence store | 3–10 MB |
| Normalization map | 1–5 MB |
| Optional prompt/templates/config | <1 MB |

The exact model size depends on the LiteRT-LM-compatible Gemma package we use, so evidence artifacts must remain as small as possible.

---

## 6. Agent Design

### 6.1 Agent responsibilities
The Gemma agent should:
- interpret user intent,
- ask clarifying questions when medication input is incomplete,
- decide which verification tools to call,
- synthesize tool results into user-friendly language,
- provide safe, grounded summaries,
- explicitly acknowledge uncertainty when evidence is weak.

### 6.2 Agent non-responsibilities
The agent should **not**:
- invent interactions,
- assign severity without tool support,
- claim evidence that cannot be retrieved,
- produce final structured findings without verification.

### 6.3 Tool-first reasoning pattern
For each request, the agent should follow this pattern:

1. Normalize medication names.
2. Enumerate relevant pairs from the regimen.
3. Query pair evidence for each pair.
4. Query regimen evidence if the combination has historical support.
5. Check whether evidence is sufficient.
6. Produce a grounded answer.
7. Build the final structured report from tool outputs.

---

## 7. Tooling Plan

The agent should operate through a strict local tool set.

### Required tools

#### `extractMedication(input)`
Input:
- image, OCR text, or typed medication string

Output:
- candidate medication names,
- normalized ingredients,
- confidence score,
- unresolved tokens.

#### `normalizeMedicationNames(names[])`
Output:
- canonical ingredient names,
- matched aliases,
- unresolved items.

#### `lookupPairEvidence(drugA, drugB)`
Output:
- evidence exists or not,
- severity distribution,
- top reactions,
- support count,
- confidence tier.

#### `lookupRegimenEvidence(drugs[])`
Output:
- matching regimen summary if present,
- most concerning pairs,
- observed outcome patterns,
- support count,
- evidence sufficiency.

#### `checkSeverityConsensus(pairFindings[], regimenFindings?)`
A deterministic function that combines available evidence into a final severity band.

Output:
- final severity,
- rationale fields,
- uncertainty note,
- evidence source summary.

#### `buildStructuredReport(findings)`
Creates the authoritative report object.

Output:
- severity level,
- flagged pairs,
- supporting evidence,
- top reactions,
- guidance text slots,
- “ask clinician / urgent attention” style flags where appropriate.

### Optional tools

#### `retrieveExampleCases(query)`
Returns a small number of representative FAERS-derived examples for richer explanation, especially on dashboard.

#### `verifyAnswerDraft(answer, evidence)`
Runs a final validation pass to ensure the generated response does not contradict the structured evidence.

This can be implemented as a deterministic checker first, and only later upgraded to a model-assisted verifier.

---

## 8. Prompting and Guardrails

### System behavior
The system prompt should instruct Gemma to:
- always verify interaction claims through tools,
- never claim an interaction without evidence,
- cite uncertainty clearly,
- prioritize user safety,
- separate verified findings from general educational language.

### Output contract
Every answer should internally follow this split:

1. **Verified findings** — from tools only
2. **Explanation** — natural language generated by Gemma
3. **Limitations / uncertainty** — from evidence sufficiency checks
4. **Next-step guidance** — safe user-facing recommendation language

### Safety guardrail
If tools return no evidence or unresolved drugs, the model should say so explicitly instead of inferring a result.

---

## 9. LiteRT-LM Deployment Plan

### Runtime target
Use **Gemma 4 E2B** through **LiteRT-LM** as the on-device agent runtime.

### Integration areas

#### Phone
- Kotlin / Android app
- LiteRT-LM for Gemma execution
- ML Kit OCR for label extraction
- SQLite for local evidence tools

#### Dashboard
- Shared evidence artifacts and shared tool logic
- if fully local: same SQLite-based packaged data
- if desktop/web hybrid: a local service wrapping the same verification logic

### Shared runtime rule
The tool layer and evidence logic must remain consistent across phone and dashboard so outputs match for the same medication set.

---

## 10. Evidence Ranking Logic

Since we are removing the classifier, we need deterministic ranking logic.

### Proposed severity synthesis
For each regimen:

1. Score every pair based on:
   - observed severity mix,
   - support count,
   - reaction seriousness,
   - consistency with regimen-level evidence.
2. Pick the highest-supported high-risk findings.
3. Aggregate into a regimen-level severity band.
4. Mark the result as one of:
   - verified strong evidence,
   - verified limited evidence,
   - insufficient evidence.

This deterministic synthesis replaces the learned severity head.

---

## 11. Build Phases

### Phase 1 — Evidence productization
- Audit current FAERS-derived tables and outputs.
- Define canonical schemas for pair evidence, regimen evidence, and normalization maps.
- Build export jobs that transform source tables into compressed SQLite artifacts.
- Measure artifact sizes and query speed.

### Phase 2 — Compression and packaging
- Add vocabulary encoding for drugs, reactions, and outcomes.
- Add pruning rules for mobile bundle.
- Produce separate phone and dashboard bundles.
- Validate that compressed artifacts preserve the high-value signals we care about.

### Phase 3 — Verification tool layer
- Implement local lookup functions over the compressed stores.
- Implement deterministic severity consensus logic.
- Implement structured report builder.
- Add tests for correctness and edge cases.

### Phase 4 — Gemma agent loop
- Integrate Gemma 4 E2B with LiteRT-LM.
- Define prompts and tool-calling flow.
- Make the agent ask follow-up questions for missing medications or ambiguous OCR.
- Add answer verification before display.

### Phase 5 — Phone experience
- Camera → OCR → normalize → verify → explain.
- Medication list review UI.
- Final report UI.
- Chat/follow-up screen.

### Phase 6 — Dashboard experience
- Manual medication entry.
- Expanded evidence inspection.
- Side-by-side structured findings and explanation.
- Export/share report options if needed.

### Phase 7 — Evaluation and demo
- Build a demo medication set with known interactions.
- Test consistency between phone and dashboard outputs.
- Measure latency, package size, and answer grounding quality.
- Record final demo flow.

---

## 12. Evaluation Plan

We no longer evaluate a trained classifier. We evaluate the **agentic grounded system**.

### Metrics

#### Evidence coverage
- What fraction of real demo regimens have direct pair support?
- What fraction have regimen-level support?

#### Grounding correctness
- Does the final report match the underlying tool outputs?
- Does the explanation avoid unsupported claims?

#### User-facing usefulness
- Are the most important interactions surfaced first?
- Are unresolved drugs clearly called out?
- Are follow-up questions asked when needed?

#### Runtime metrics
- mobile query latency,
- end-to-end response latency,
- artifact size,
- memory usage on target phone.

### Benchmarking idea
We can still use held-out FAERS-derived examples and curated interaction cases, but the benchmark becomes:
- retrieval quality,
- evidence synthesis quality,
- answer faithfulness to tool results.

---

## 13. Open Decisions

1. **Gemma package choice:** confirm the exact LiteRT-LM-compatible Gemma variant and quantization level.
2. **Dashboard runtime shape:** fully local desktop bundle vs local service wrapper.
3. **Regimen evidence depth:** how much regimen-level history can fit on phone after compression.
4. **Verification strictness:** whether every final answer needs a deterministic contradiction check.
5. **Bundle split:** how different the phone and dashboard evidence packs should be.

---

## 14. Immediate Next Steps

1. Freeze the new architecture: **Gemma agent + compressed evidence + tool verification**, no separate ML classifier.
2. Design the schemas for:
   - pair evidence store,
   - regimen evidence store,
   - normalization map.
3. Build the first export/compression pipeline from our existing FAERS-derived tables.
4. Measure size and lookup latency of a phone-ready SQLite bundle.
5. Implement the first deterministic tools:
   - `normalizeMedicationNames`
   - `lookupPairEvidence`
   - `lookupRegimenEvidence`
   - `checkSeverityConsensus`
   - `buildStructuredReport`
6. Wire Gemma through LiteRT-LM to call those tools and produce grounded responses.

---

## 15. Final Project Statement

**MedLens** is now best framed as a **grounded on-device medication safety agent**:

- **Gemma 4 E2B** provides reasoning, dialogue, and explanation,
- **compressed FAERS-derived evidence artifacts** provide local knowledge,
- **tool verification** ensures that important outputs are backed by data,
- **LiteRT-LM** enables deployment on phone and compatible dashboard setups,
- the system stays private, offline-capable, and explainable.
