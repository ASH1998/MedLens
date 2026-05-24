# MedLens Android Gaps Plan

This plan converts the Android review into concrete pushable work. The target
product remains:

```text
camera OCR or typed medication names
    -> deterministic normalization
    -> deterministic local DDI lookup
    -> structured safety report
    -> Gemma explains the structured report
```

The deterministic tools remain authoritative. The model can explain, format,
and ask follow-up questions, but must not invent interactions, adverse effects,
evidence, severity, or source claims.

## Current Android Baseline

- Gradle modules: `android/app`, `android/core-data`, `android/core-agent`.
- Local data: bundled `normalization.sqlite`, `evidence.mobile.sqlite`, and
  `india_common_brand_ingredient_map.csv`.
- UI: Compose chat, conversations, camera capture, image attachments, settings,
  LiteRT backend selector, and TTS.
- Agent path: provider-native text protocol using `CALL:`, `ASK:`, `ANSWER:`.
- Model path: `litert-community/gemma-4-E4B-it-litert-lm`.
- Verified check: `:core-agent:testDebugUnitTest` passes when run from
  `android/`.

## Push 1: Documentation and Build Hygiene

Goal: make the current Android state reproducible and remove doc drift.

Push:

- `gaps.md`
- `android/README.md`
- `AGENTS.md` only if repository-wide Android guidance needs to be updated

Add/update:

- Document that Android bundles three local assets:
  - `data/artifacts/normalization.sqlite`
  - `data/artifacts/evidence.mobile.sqlite`
  - `data/raw/DDI/india_common_brand_ingredient_map.csv`
- Document the actual image path:
  - CameraX captures images.
  - Current extraction uses LiteRT vision.
  - ML Kit OCR exists but is not wired as the first pass yet.
- Document Gradle root as `android/`, not repo root.
- Document the known local Gradle command:

```bash
cd android
ANDROID_SDK_ROOT=$HOME/Android/Sdk \
GRADLE_USER_HOME=/tmp/medlens-gradle-home \
/home/ashu/github/MedLens/.gradle-dist/gradle-8.7/bin/gradle \
  --no-daemon \
  -Djava.io.tmpdir=/tmp/medlens-gradle-tmp \
  --max-workers=2 \
  :core-agent:testDebugUnitTest
```

Acceptance:

- New contributors can tell what is shipped on Android and what is still
  scaffolded.
- No product behavior changes.

## Push 2: Deterministic Android Fallback

Goal: typed medication checks should work without Gemma downloaded.

Push:

- `android/core-agent/src/main/java/com/medlens/core/agent/`
- `android/app/src/main/java/com/medlens/android/ui/MedLensViewModel.kt`
- `android/core-agent/src/test/java/com/medlens/core/agent/AgentOrchestratorTest.kt`

Add:

- A small Android fallback provider or formatter that can turn
  `MedicationSafetyReport` into patient-facing text when LiteRT is unavailable.
- ViewModel routing:
  - If repository is ready but model is not ready, allow typed text checks that
    can be parsed into medication names.
  - Use deterministic `buildStructuredReport`.
  - Return fallback text instead of blocking chat behind model readiness.
- Keep Gemma as explanation layer when ready.

Implementation notes:

- Keep the fallback conservative. It should only summarize structured report
  fields.
- Do not add broad clinical knowledge in fallback mode unless it already comes
  from deterministic output.
- Avoid coupling fallback to Compose UI.

Acceptance:

- User can type `Advil and Warfarin` before model download and receive a local
  structured safety answer.
- Existing `core-agent` tests continue to pass.
- Add unit tests for:
  - flagged pair fallback answer
  - no local finding fallback answer
  - unresolved medicine fallback answer

## Push 3: ML Kit OCR First, Gemma Vision Second

Goal: camera flow should follow the intended deterministic product path and not
require multimodal Gemma for basic text extraction.

Push:

- `android/app/src/main/java/com/medlens/android/ocr/MlKitOcrManager.kt`
- `android/app/src/main/java/com/medlens/android/ui/MedLensViewModel.kt`
- `android/app/src/main/java/com/medlens/android/ui/MedLensApp.kt` only if UI
  copy/state needs to change
- Android tests or JVM tests where feasible

Add:

- Convert captured image file to `InputImage`.
- Run ML Kit OCR first.
- Extract candidate names with the existing candidate cleaner.
- Normalize OCR candidates through `SafetyRepository.normalizeMedications`.
- Use Gemma vision only as an optional enhancement when:
  - OCR produces no useful candidates, or
  - user explicitly asks about unclear image contents, or
  - a later setting enables model-assisted image reading.

Change:

- `sendImageMessage` should not call
  `LiteRtLmProvider.extractMedicineCandidatesFromImage` as the default path.
- The agent message should be based on normalized candidates and unresolved raw
  candidates, not raw model prose.

Acceptance:

- Camera safety checks can run with local OCR and SQLite even before Gemma is
  downloaded.
- If OCR fails, app asks for clearer image or typed names.
- The model is not the authority for extracting or inventing medicines.

## Push 4: Android Tool Parity With Python/Web

Goal: Android agent can answer audit, source, severity, and data-quality
questions with the same deterministic coverage as Python/web.

Push:

- `android/core-data/src/main/java/com/medlens/core/data/SafetyRepository.kt`
- `android/core-data/src/main/java/com/medlens/core/data/SqliteSafetyRepository.kt`
- `android/core-data/src/main/java/com/medlens/core/data/model/Models.kt`
- `android/core-agent/src/main/java/com/medlens/core/agent/ToolRegistry.kt`
- `android/core-agent/src/main/java/com/medlens/core/agent/Prompts.kt`
- `android/core-agent/src/test/java/com/medlens/core/agent/AgentOrchestratorTest.kt`

Add repository APIs:

- `getPairEffects(drugA, drugB, limit)`
- `getRawSignals(drugA, drugB, limit)`
- `getFullRawSignals(drugA, drugB, limit)`
- `severityConsensus(drugA, drugB)`
- `findPairsByEffect(effect, limit)`
- `listImportIssues(sourceFile, query, limit)`

Add tool schemas/dispatch:

- `get_pair_effects`
- `get_raw_signals`
- `get_full_raw_signals`
- `severity_consensus`
- `find_pairs_by_effect`
- `list_import_issues`

Update prompt:

- Match Python/web guidance:
  - use pair detail tools for specific pair audits
  - use raw signal tools only when user asks for source rows/audit detail
  - use import issue tools for data-quality questions

Acceptance:

- Android tool names match Python/web names.
- Tool outputs use the same field names where practical.
- Unit tests cover at least:
  - dispatch for `severity_consensus`
  - dispatch for `list_import_issues`
  - dispatch for `get_raw_signals`

## Push 5: Model Integrity and Download Robustness

Goal: model readiness must mean the file is actually usable and verified.

Push:

- `android/app/src/main/java/com/medlens/android/model/GemmaModelManager.kt`
- `android/app/src/main/java/com/medlens/android/model/GemmaModelDownloadWorker.kt`
- `android/app/src/main/java/com/medlens/android/model/GemmaModel.kt`

Add/change:

- Verify SHA before returning `ModelState.Ready`, not only size.
- Surface checksum failure as `ModelState.Error`.
- Provide a retry path that deletes corrupt partial/target files.
- Consider a small metadata sidecar after successful verification to avoid
  hashing 3.66 GB on every startup.

Acceptance:

- Same-size corrupt model file is not treated as ready.
- Retry can recover from corrupt `.part` and corrupt target file.
- Download failure messages remain user-readable.

## Push 6: Offline and Privacy Settings

Goal: keep the medication-safety core offline and make network features
explicit.

Push:

- `android/app/src/main/java/com/medlens/android/ui/MedLensApp.kt`
- `android/app/src/main/java/com/medlens/android/data/ConversationStore.kt`
- `android/app/build.gradle.kts`
- `android/README.md`

Add/change:

- Add an explicit setting for remote TTS.
- Default TTS to Android native `TextToSpeech`.
- Only call MiMo TTS when the setting is enabled and an API key is present.
- Label model download as network-required.
- Keep local safety checks usable without network after DB assets are bundled.
- Reduce risk around debug secrets:
  - keep release `BuildConfig` secrets empty
  - document debug-only behavior
  - avoid logging secret-dependent details

Acceptance:

- A default install does not send medication text to remote TTS.
- User can still use local checks and native TTS offline.
- Settings make network behavior explicit.

## Push 7: Artifact and Repository Parity Tests

Goal: catch drift between Python/web behavior and Android SQLite behavior.

Push:

- `android/core-data/src/test/`
- `android/core-agent/src/test/`
- Test fixtures or fixture-copy scripts if needed

Add:

- JVM tests using small fixture SQLite DBs, not full production artifacts.
- Cases mirroring Python tests:
  - `Dolo` normalizes to `acetaminophen`
  - `Clavam` expands to `amoxicillin clavulanate` ingredients
  - `Advil` and `Warfarin` returns a flagged finding
  - unresolved names appear in report limitations
  - duplicate ingredient warnings work for multi-ingredient/brand cases
  - evidence source listing returns imported source files

Acceptance:

- Tests run through:

```bash
cd android
ANDROID_SDK_ROOT=$HOME/Android/Sdk \
GRADLE_USER_HOME=/tmp/medlens-gradle-home \
/home/ashu/github/MedLens/.gradle-dist/gradle-8.7/bin/gradle \
  --no-daemon \
  -Djava.io.tmpdir=/tmp/medlens-gradle-tmp \
  --max-workers=2 \
  :core-data:testDebugUnitTest \
  :core-agent:testDebugUnitTest
```

## Push 8: Android UI Safety Polish

Goal: improve the patient-facing experience after deterministic behavior is
stable.

Push:

- `android/app/src/main/java/com/medlens/android/ui/MedLensApp.kt`
- `android/app/src/main/java/com/medlens/android/ui/MedLensViewModel.kt`

Add/change:

- Show unresolved medicine chips after OCR/text checks.
- Show resolved active ingredients before the final answer when useful.
- Add a clearer local-evidence status for:
  - flagged interaction
  - no local flagged finding
  - unresolved medicine
  - insufficient medicines
- Keep sources accessible without overwhelming default answers.
- Ensure trace card is debug-only or clearly secondary.

Acceptance:

- Patient can see what names were actually checked.
- Unresolved image/text names are not silently ignored.
- UI does not imply "safe" when evidence only says no local flagged finding.

## Suggested Branch/PR Order

1. `android-docs-gap-plan`
   - Push 1.
2. `android-deterministic-fallback`
   - Push 2.
3. `android-ocr-first`
   - Push 3.
4. `android-tool-parity`
   - Push 4.
5. `android-model-integrity`
   - Push 5.
6. `android-offline-privacy-settings`
   - Push 6.
7. `android-parity-tests`
   - Push 7.
8. `android-ui-safety-polish`
   - Push 8.

This order keeps the app usable earlier, reduces model dependence, then expands
tool coverage and hardens privacy/reliability.

## Definition of Done

- Android typed checks work without model download.
- Android camera checks use deterministic OCR first.
- Android tool registry covers the same safety/debug tools as Python/web.
- Model readiness is SHA-verified.
- Remote TTS is opt-in.
- Fixture tests cover normalization, pair lookup, structured reports, source
  listing, and key agent dispatch paths.
- Android README reflects the real build, asset, OCR, model, and offline
  behavior.
