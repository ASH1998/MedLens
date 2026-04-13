# MedLens — Full Implementation Plan

## Context
Building **MedLens**, a polypharmacy drug interaction agent for the **Gemma 4 Good Hackathon** (deadline: May 18, 2026).

**Target tracks:** LiteRT ($10K) + Unsloth ($10K) + Health & Sciences ($10K) + Main Track ($10K-$50K) = up to **$80K**

**The problem:** Adverse drug interactions kill 125,000 Americans/year. Elderly patients take 5-12 medications from multiple uncoordinated doctors. Nobody checks the full combination.

**The solution:** An on-device Android agent that photographs medication bottles, extracts drug info, reasons about multi-drug interaction risks using a fine-tuned Gemma 4 E2B, and generates severity-ranked safety reports — all offline, all private.

---

## Phase 1: Data Collection & Preparation

### 1.1 Primary Datasets

#### DrugBank 6.0 (Core interaction database)
- **What:** 4,563 FDA-approved drugs, **1,413,413 drug-drug interactions**
- **Access:** Free account at https://go.drugbank.com/releases
- **Format:** XML, JSON, CSV
- **License:** Free for research/academic use
- **What to extract:** Drug pairs → interaction type → severity → mechanism → clinical effect
- **Output:** `drugbank_interactions.jsonl` — each row is a drug pair with interaction details

#### OpenFDA Adverse Events (Real-world signal data)
- **What:** FDA Adverse Event Reporting System (FAERS) — millions of real-world reports
- **Access:** https://open.fda.gov/apis/drug/event/ (free, no API key needed)
- **Format:** JSON API + bulk download
- **License:** Public domain
- **What to extract:** Co-reported drugs → adverse outcomes → frequency
- **Output:** `openfda_adverse_events.jsonl`

#### OpenFDA Drug Labels (Structured Product Labels)
- **What:** Official FDA drug labels with interaction sections
- **Access:** https://open.fda.gov/apis/drug/label/download/
- **Format:** Zipped JSON
- **License:** Public domain
- **What to extract:** "drug_interactions" field from each label
- **Output:** `drug_labels_interactions.jsonl`

#### RxNorm (Drug name normalization)
- **What:** Standardized drug naming system from NIH
- **Access:** https://download.nlm.nih.gov/umls/kss/rxnorm/RxNorm_full_current.zip (no license needed for prescribable subset)
- **Format:** Pipe-delimited text files
- **Use:** Normalize drug names (brand → generic) so "Advil" maps to "ibuprofen"
- **Output:** `rxnorm_mapping.json` — brand name → generic name → RxNorm ID

#### DDI Corpus (ML evaluation benchmark)
- **What:** 792 DrugBank texts + 233 MedLine abstracts, 5,028 annotated DDIs
- **Access:** Published with SemEval-2013 (publicly available)
- **Format:** Annotated XML
- **4 interaction types:** Advice, Effect, Mechanism, Int
- **Use:** Evaluation/validation benchmark — NOT for training

#### Kaggle Drug Interaction Datasets (Supplementary)
- https://www.kaggle.com/datasets/mghobashy/drug-drug-interactions
- https://www.kaggle.com/datasets/montassarba/drug-drug-interactions-database-ddinter/
- **Use:** Quick prototyping, supplementary training data

### 1.2 Fine-Tuning Dataset Creation

**Goal:** Create ~5,000-10,000 instruction-tuning examples in Unsloth chat format.

**Three types of training examples:**

#### Type A: Single Interaction Query (3,000 examples)
```json
{
  "messages": [
    {"role": "user", "content": "I take ibuprofen 400mg twice daily and warfarin 5mg daily. Are there any interactions?"},
    {"role": "assistant", "content": "<|think|>Let me analyze this combination. Ibuprofen is an NSAID and warfarin is an anticoagulant. NSAIDs inhibit platelet aggregation and can displace warfarin from protein binding sites, increasing free warfarin levels. This is a well-documented major interaction.</think>\n\n⚠️ **MAJOR INTERACTION DETECTED**\n\n**Ibuprofen + Warfarin**\n- **Severity:** High\n- **Mechanism:** Ibuprofen inhibits platelet aggregation and may displace warfarin from protein binding, increasing bleeding risk\n- **Clinical effect:** Significantly increased risk of gastrointestinal bleeding and hemorrhage\n- **Recommendation:** Avoid this combination. If pain relief is needed, consider acetaminophen (Tylenol) as an alternative. Consult your doctor immediately."}
  ]
}
```

#### Type B: Multi-Drug Analysis (2,000 examples)
```json
{
  "messages": [
    {"role": "user", "content": "My medications: metformin 500mg, lisinopril 10mg, atorvastatin 20mg, aspirin 81mg, omeprazole 20mg. Check all interactions."},
    {"role": "assistant", "content": "<|think|>I need to check all pairwise combinations: 5 drugs = 10 possible pairs. Let me systematically evaluate each...\n1. metformin + lisinopril: Minor — lisinopril may enhance hypoglycemic effect\n2. metformin + atorvastatin: No significant interaction\n3. metformin + aspirin: Minor — aspirin may enhance hypoglycemic effect\n4. metformin + omeprazole: Moderate — omeprazole may reduce metformin absorption\n5. lisinopril + atorvastatin: No significant interaction\n6. lisinopril + aspirin: Moderate — aspirin may reduce antihypertensive effect\n7. lisinopril + omeprazole: No significant interaction\n8. atorvastatin + aspirin: No significant interaction\n9. atorvastatin + omeprazole: Minor — omeprazole may slightly increase atorvastatin levels\n10. aspirin + omeprazole: Beneficial — omeprazole protects against aspirin-induced GI damage</think>\n\n**Drug Interaction Report — 5 Medications**\n\n✅ **No major interactions found**\n\n⚠️ **2 Moderate Interactions:**\n1. **Omeprazole + Metformin** — Omeprazole may reduce metformin absorption. Take metformin 30 min before omeprazole.\n2. **Aspirin + Lisinopril** — Aspirin may reduce blood pressure lowering effect. Monitor BP regularly.\n\nℹ️ **2 Minor Interactions:**\n1. Lisinopril may slightly enhance blood sugar lowering of metformin — monitor glucose\n2. Omeprazole may slightly increase atorvastatin levels — usually not clinically significant\n\n✅ **1 Beneficial Combination:**\n- Omeprazole + Aspirin — omeprazole protects your stomach lining from aspirin irritation. Good pairing."}
  ]
}
```

#### Type C: Follow-up / Agentic (2,000 examples)
```json
{
  "messages": [
    {"role": "user", "content": "I take blood pressure medicine and ibuprofen for my knee."},
    {"role": "assistant", "content": "I need a bit more detail to check interactions accurately. Which blood pressure medication do you take? Common ones include:\n- Lisinopril/Enalapril (ACE inhibitors)\n- Losartan/Valsartan (ARBs)\n- Amlodipine (calcium channel blocker)\n- Metoprolol/Atenolol (beta blockers)\n- Hydrochlorothiazide (diuretic)\n\nAlso, do you take any other medications, vitamins, or supplements?"},
    {"role": "user", "content": "It's lisinopril. And I also take a baby aspirin and fish oil."},
    {"role": "assistant", "content": "<|think|>Now I have: lisinopril, ibuprofen, aspirin 81mg, fish oil. Let me check all pairs...\n- Ibuprofen + lisinopril: MAJOR — NSAIDs reduce ACE inhibitor effectiveness and increase renal risk\n- Ibuprofen + aspirin: MAJOR — ibuprofen blocks aspirin's cardioprotective effect\n- Ibuprofen + fish oil: MODERATE — both affect bleeding\n- Lisinopril + aspirin: Moderate\n- Fish oil + aspirin: Moderate — additive bleeding risk</think>\n\n🚨 **2 MAJOR INTERACTIONS DETECTED**\n\n1. **Ibuprofen + Lisinopril** (Severity: HIGH)\n   - Ibuprofen can significantly reduce lisinopril's blood pressure lowering effect\n   - Combined use increases risk of kidney damage\n   - **Action:** Talk to your doctor about switching to acetaminophen for knee pain\n\n2. **Ibuprofen + Aspirin** (Severity: HIGH)\n   - If you take ibuprofen BEFORE aspirin, it blocks aspirin's heart-protective effect\n   - **Action:** If you must use both, take aspirin at least 30 minutes before ibuprofen\n\n⚠️ **2 Moderate Interactions:**\n- Fish oil + aspirin + ibuprofen — triple bleeding risk. Monitor for unusual bruising.\n- Aspirin + lisinopril — may slightly reduce BP control.\n\n**Overall risk level: HIGH — discuss with your doctor**"}
  ]
}
```

### 1.3 Data Pipeline Script

Create `data/prepare_training_data.py`:
1. Download DrugBank CSV → parse interaction pairs with severity/mechanism
2. Download OpenFDA labels → extract drug_interactions sections
3. Generate instruction-tuning examples programmatically:
   - Sample drug combinations from DrugBank
   - Template into chat format with reasoning chains
   - Include both "no interaction" and "interaction found" examples (balanced)
4. Add multi-drug scenarios (3-8 drugs per example)
5. Add follow-up question scenarios
6. Output: `data/medlens_train.jsonl` (~7,000 examples) + `data/medlens_eval.jsonl` (~1,000 examples)

### 1.4 Local Drug Interaction Database

Create `data/interaction_db.json` — a compact on-device lookup:
```json
{
  "ibuprofen": {
    "class": "NSAID",
    "interactions": {
      "warfarin": {"severity": "major", "mechanism": "increased bleeding risk", "advice": "avoid combination"},
      "lisinopril": {"severity": "major", "mechanism": "reduced antihypertensive effect + renal risk", "advice": "use acetaminophen instead"},
      "aspirin": {"severity": "major", "mechanism": "blocks cardioprotective effect", "advice": "take aspirin 30min before"}
    }
  }
}
```
- Cover top 200 most commonly prescribed drugs
- ~5,000 interaction pairs with severity + mechanism + advice
- Size: ~2-5MB JSON (fits easily on any phone)
- Source: DrugBank + OpenFDA cross-referenced

---

## Phase 2: Fine-Tuning with Unsloth

### 2.1 Setup (Google Colab T4 — free tier)

```python
# Install
!pip install unsloth

# Load base model
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="google/gemma-4-E2B-it",
    max_seq_length=4096,
    load_in_4bit=True,
)

# Add LoRA adapters
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                     "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0,
    bias="none",
)
```

### 2.2 Training Config

```python
from trl import SFTTrainer
from transformers import TrainingArguments

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=train_dataset,      # medlens_train.jsonl
    eval_dataset=eval_dataset,         # medlens_eval.jsonl
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=50,
        num_train_epochs=3,
        learning_rate=2e-4,
        fp16=True,
        logging_steps=10,
        output_dir="medlens-gemma4-e2b",
        eval_strategy="steps",
        eval_steps=100,
    ),
)
trainer.train()
```

### 2.3 Export & Benchmark

```python
# Save merged model
model.save_pretrained_merged("medlens-merged", tokenizer)

# Push to Hugging Face (for writeup)
model.push_to_hub("your-username/medlens-gemma4-e2b", tokenizer)

# Export GGUF for LiteRT conversion
model.save_pretrained_gguf("medlens-gguf", tokenizer, quantization_method="q4_k_m")
```

**Benchmarks to show in writeup:**
- Base Gemma 4 E2B vs. fine-tuned on DDI Corpus eval set
- Metrics: interaction detection accuracy, severity classification F1, false negative rate
- Target: base model ~60% accuracy → fine-tuned ~85-90%

---

## Phase 3: Android App (LiteRT-LM)

### 3.1 Architecture

```
┌─────────────────────────────────────────────────────┐
│                    MedLens App                       │
├─────────────────────────────────────────────────────┤
│  UI Layer (Jetpack Compose)                         │
│  ├── CameraScreen (photograph bottles)              │
│  ├── MedicationListScreen (extracted meds)          │
│  ├── InteractionReportScreen (results)              │
│  └── ChatScreen (agentic follow-ups)                │
├─────────────────────────────────────────────────────┤
│  Agent Layer                                         │
│  ├── ConversationManager (multi-turn state)         │
│  ├── ToolExecutor (function calling dispatcher)     │
│  └── ReasoningEngine (think mode controller)        │
├─────────────────────────────────────────────────────┤
│  Tool Layer (Function Calling)                       │
│  ├── extractMedication(image) → drug name, dose     │
│  ├── checkInteractions(drugList) → interaction map  │
│  ├── getContraindications(drug) → warnings          │
│  └── generateReport(interactions) → formatted PDF   │
├─────────────────────────────────────────────────────┤
│  Data Layer                                          │
│  ├── InteractionDB (interaction_db.json — 5K pairs) │
│  ├── RxNormMapping (brand → generic)                │
│  ├── MedicationHistory (Room DB — user's meds)      │
│  └── ReportStorage (generated reports)              │
├─────────────────────────────────────────────────────┤
│  ML Layer                                            │
│  ├── LiteRT-LM Engine (Gemma 4 E2B fine-tuned)     │
│  └── ML Kit Text Recognition (OCR assist)           │
└─────────────────────────────────────────────────────┘
```

### 3.2 Key Implementation

**LiteRT-LM Engine Setup (Kotlin):**
```kotlin
// Initialize engine with fine-tuned model
val engine = Engine(modelPath = "/path/to/medlens-e2b.task")
engine.initialize()  // background thread

// Create conversation
val conversation = engine.createConversation(ConversationConfig())

// Send message with streaming
conversation.sendMessageAsync(userMessage, toolContext).collect { response ->
    updateUI(response)
}
```

**Function Calling (Kotlin ToolSet):**
```kotlin
class MedLensTools : ToolSet {
    @Tool(description = "Extract medication name and dosage from image")
    fun extractMedication(
        @ToolParam(description = "Extracted text from medication label") labelText: String
    ): String {
        val normalized = rxNormMapping.normalize(labelText)
        return """{"drug": "${normalized.generic}", "brand": "${normalized.brand}", "dose": "${normalized.dose}"}"""
    }

    @Tool(description = "Check drug-drug interactions for a list of medications")
    fun checkInteractions(
        @ToolParam(description = "Comma-separated list of drug names") drugs: String
    ): String {
        val drugList = drugs.split(",").map { it.trim() }
        val interactions = interactionDB.findAll(drugList)
        return interactions.toJson()
    }

    @Tool(description = "Get contraindications for a specific drug")
    fun getContraindications(
        @ToolParam(description = "Drug name") drug: String
    ): String {
        return interactionDB.getContraindications(drug).toJson()
    }

    @Tool(description = "Generate a formatted safety report")
    fun generateReport(
        @ToolParam(description = "JSON of all interactions found") interactions: String
    ): String {
        return reportGenerator.create(interactions)
    }
}
```

**Hybrid OCR Pipeline:**
1. ML Kit Text Recognition extracts raw text from camera frame
2. Text passed to Gemma 4 for semantic understanding (drug name extraction, dosage parsing)
3. RxNorm mapping normalizes brand → generic
4. Function calling checks interactions against local DB
5. Reasoning mode synthesizes full report

### 3.3 Model Conversion Pipeline

```bash
# Step 1: Fine-tune with Unsloth → export safetensors
# Step 2: Convert to LiteRT .task format
python -m litert.convert \
  --source_model medlens-merged/ \
  --output medlens-e2b.task \
  --quantization int4

# Step 3: Bundle into Android app assets or download on first launch
```

### 3.4 App Project Structure

```
medlens/
├── app/
│   ├── src/main/
│   │   ├── java/com/medlens/
│   │   │   ├── MainActivity.kt
│   │   │   ├── ui/
│   │   │   │   ├── CameraScreen.kt
│   │   │   │   ├── MedicationListScreen.kt
│   │   │   │   ├── InteractionReportScreen.kt
│   │   │   │   └── ChatScreen.kt
│   │   │   ├── agent/
│   │   │   │   ├── ConversationManager.kt
│   │   │   │   ├── MedLensTools.kt
│   │   │   │   └── ReasoningEngine.kt
│   │   │   ├── data/
│   │   │   │   ├── InteractionDB.kt
│   │   │   │   ├── RxNormMapping.kt
│   │   │   │   ├── MedicationDao.kt
│   │   │   │   └── AppDatabase.kt
│   │   │   └── ml/
│   │   │       ├── LiteRTEngine.kt
│   │   │       └── OCRProcessor.kt
│   │   ├── assets/
│   │   │   ├── interaction_db.json
│   │   │   └── rxnorm_mapping.json
│   │   └── res/
│   └── build.gradle.kts
├── data/
│   ├── prepare_training_data.py
│   ├── download_drugbank.py
│   ├── download_openfda.py
│   ├── generate_instruction_data.py
│   └── build_interaction_db.py
├── training/
│   ├── train_unsloth.py
│   ├── evaluate.py
│   └── export_litert.py
└── docs/
    ├── overview.txt
    └── rules.txt
```

---

## Phase 4: Demo & Video Strategy

### 4.1 Demo Flow (3 minutes)

**Scene 1 (0:00-0:30) — The Problem:**
- Stat cards: "125,000 Americans die from drug interactions each year"
- Show a kitchen counter with 6-8 medicine bottles — a realistic elderly person's daily meds
- "Your grandmother's cardiologist, rheumatologist, and GP don't talk to each other."

**Scene 2 (0:30-1:30) — The App in Action:**
- Open MedLens on a real Android phone
- Photograph each bottle one by one — app extracts drug name + dose
- Show the medication list building in real-time
- Tap "Check Interactions" — reasoning animation plays
- Report appears: 2 MAJOR interactions, 1 moderate, with clear explanations

**Scene 3 (1:30-2:15) — Agentic Follow-up:**
- App asks: "I noticed ibuprofen and aspirin. Do you also take any blood thinners?"
- User responds via text
- Agent updates the report with new findings
- Show the reasoning trace (think mode visible)

**Scene 4 (2:15-2:45) — Technical Depth:**
- Quick overlay: "Fine-tuned Gemma 4 E2B with Unsloth"
- Benchmark comparison: base model vs fine-tuned (accuracy chart)
- "Running 100% on-device via LiteRT-LM — no internet required"
- Show airplane mode enabled

**Scene 5 (2:45-3:00) — Impact:**
- "MedLens puts a pharmacist's knowledge in every pocket."
- "Your medical data never leaves your phone."
- Logo + track badges (LiteRT + Unsloth + Health & Sciences)

### 4.2 Demo Medications (Real OTC drugs to use)

Use these real, purchasable medicines for the demo:
1. **Ibuprofen** (Advil) 200mg
2. **Aspirin** (Bayer) 81mg
3. **Acetaminophen** (Tylenol) 500mg
4. **Omeprazole** (Prilosec) 20mg
5. **Diphenhydramine** (Benadryl) 25mg
6. **Naproxen** (Aleve) 220mg

**Known interactions this demo catches:**
- Ibuprofen + Aspirin → MAJOR (blocks cardioprotective effect)
- Ibuprofen + Naproxen → MAJOR (duplicate NSAIDs, doubled GI bleeding risk)
- Aspirin + Naproxen → MAJOR (same mechanism as ibuprofen)
- Diphenhydramine + Omeprazole → MODERATE (anticholinergic load in elderly)

---

## Phase 5: Submission Deliverables

### 5.1 Kaggle Writeup (≤1,500 words)
Structure:
1. **Problem** (200 words) — the polypharmacy crisis with statistics
2. **Solution** (300 words) — MedLens architecture and user flow
3. **Technical Implementation** (500 words) — fine-tuning pipeline, LiteRT deployment, function calling, reasoning mode
4. **Benchmarks** (200 words) — base vs fine-tuned comparison on DDI eval
5. **Impact & Future** (200 words) — who this helps, what's next

### 5.2 Public Code Repository
GitHub repo with:
- Android app source code
- Data preparation scripts
- Training notebook (Colab-ready)
- Fine-tuned model weights on Hugging Face
- interaction_db.json
- README with reproduction instructions

### 5.3 Live Demo
- APK download link (sideloadable)
- OR: screen recording video showing real-time usage

---

## 5-Week Timeline

### Week 1: Data & Foundation
- [ ] Create DrugBank account, download interaction data
- [ ] Download OpenFDA labels + adverse events
- [ ] Build RxNorm brand→generic mapping
- [ ] Write `prepare_training_data.py` — generate 7K instruction examples
- [ ] Build `interaction_db.json` (top 200 drugs, 5K pairs)
- [ ] Set up Android project skeleton (Jetpack Compose)

### Week 2: Fine-Tuning
- [ ] Upload training data to Colab
- [ ] Fine-tune Gemma 4 E2B with Unsloth (LoRA, 3 epochs)
- [ ] Evaluate on DDI Corpus benchmark
- [ ] Generate base vs fine-tuned comparison metrics
- [ ] Export model: safetensors → GGUF → LiteRT .task format
- [ ] Push fine-tuned model to Hugging Face

### Week 3: Android App Core
- [ ] Integrate LiteRT-LM engine with fine-tuned model
- [ ] Implement ML Kit OCR for label reading
- [ ] Build function calling tools (4 tools)
- [ ] Implement multi-turn conversation manager
- [ ] Build CameraScreen + MedicationListScreen
- [ ] Wire up interaction_db.json as local lookup

### Week 4: Agentic Loop & Polish
- [ ] Implement reasoning mode integration
- [ ] Build follow-up question logic (agent asks for missing info)
- [ ] Build InteractionReportScreen with severity visualization
- [ ] Add medication history (Room DB)
- [ ] End-to-end testing with real OTC medications
- [ ] Edge case handling (blurry photos, unknown drugs, partial labels)

### Week 5: Video & Submission
- [ ] Film demo video (≤3 min) on real Android phone
- [ ] Edit video with problem/solution/technical/impact structure
- [ ] Upload to YouTube
- [ ] Write Kaggle writeup (≤1,500 words)
- [ ] Clean up GitHub repo + README
- [ ] Prepare live demo (APK or recording)
- [ ] Submit on Kaggle before May 18 11:59 PM UTC

---

## Verification

### How to test end-to-end:
1. **Data pipeline:** Run `prepare_training_data.py` → verify 7K+ examples in correct chat format
2. **Fine-tuning:** Run Colab notebook → verify loss decreasing, eval metrics improving
3. **Model export:** Convert to .task → verify file loads in LiteRT-LM without errors
4. **OCR:** Point camera at medicine bottle → verify drug name extracted correctly
5. **Function calling:** Send drug list to `checkInteractions()` → verify correct interactions returned from local DB
6. **Agentic loop:** Start with partial info → verify agent asks follow-up → verify updated report after response
7. **Reasoning:** Verify `<|think|>` traces appear in model output for complex multi-drug queries
8. **Offline:** Enable airplane mode → verify full pipeline works without internet
9. **Demo medications:** Test with the 6 OTC drugs listed above → verify all 4 known interactions detected
