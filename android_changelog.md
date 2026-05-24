# Android Changelog

## v1.1.1 - 2026-05-21

- Updated Android package metadata to `versionCode = 111` and
  `versionName = "v1.1.1"`.
- Added deterministic fallback path so typed medication checks work without
  Gemma downloaded. The app parses medication candidates, builds a structured
  safety report locally, and formats patient-facing text without the LLM.
- Wired ML Kit OCR as the first-pass text extractor for camera images. Gemma
  vision is now an optional fallback used only when ML Kit produces no useful
  candidates and the model is downloaded.
- Added 6 new repository APIs and tool schemas to match Python/web parity:
  `get_pair_effects`, `get_raw_signals`, `get_full_raw_signals`,
  `severity_consensus`, `find_pairs_by_effect`, `list_import_issues`.
- Implemented SHA-256 verification before returning model `Ready` state. A
  metadata sidecar (`.verified.json`) is written after successful verification
  so startup no longer re-hashes the 3.66 GB model file on every launch.
- Added retry path that deletes corrupt `.part` and target files before
  re-downloading, and surfaces checksum failures as `ModelState.Error`.
- Changed TTS to default to Android native `TextToSpeech`. Remote MiMo TTS is
  now opt-in via a settings toggle and requires an API key.
- Added "Requires internet (~3.66 GB)" label on the model download button.
- Added evidence status badge (flagged interaction / no local finding /
  unresolved / insufficient) before assistant message content.
- Added resolved active-ingredient chips and unresolved medicine chips to
  assistant messages so users can see what was actually checked.
- Added collapsible sources section at the bottom of assistant messages.
- Made the tool trace card debug-only (`BuildConfig.DEBUG`).
- Added `FallbackReportFormatter` with 7 unit tests for flagged pair, no
  finding, unresolved medicine, empty report, and duplicate ingredient cases.
- Added `ToolParityTest` with 15 JVM tests covering normalization (Dolo to
  acetaminophen, Clavam expansion), pair lookup (Advil plus Warfarin returns
  Major), unresolved names in limitations, evidence source listing, import
  issues, severity consensus, effect-based pair search, and session management.
- Updated Android README with bundled asset list, OCR/image path documentation,
  offline behavior, model integrity details, and Gradle build commands.
- Added debug-only secret hygiene comment in `build.gradle.kts` documenting
  that release `BuildConfig` secrets are intentionally empty.

## v1.1.0 - 2026-05-16

- Updated Android package metadata to `versionCode = 110` and
  `versionName = "v1.1.0"`.
- Reworked the Android LiteRT agent loop to avoid native tool-call parse
  failures by using a plain `CALL` / `ASK` / `ANSWER` protocol.
- Improved multi-image medicine checks so extracted names and typed medicine
  names are checked together.
- Added protection against asking for active ingredients after the structured
  report already resolved them.
- Included normalized active ingredients in compact structured-report tool
  results.
- Increased the default tool loop budget to 5 rounds and the turn timeout to 2
  minutes.
- Tuned patient-facing answers to use warmer language and light bold formatting
  such as `**Bottom line:**`, `**Why:**`, and `**Watch for:**`.
- Added a subtle Settings credit link for `Ashutosh Mishra`.
- For combinations with no flagged local finding, answers now provide cautious
  general medication guidance instead of leading with a database-style no-finding
  sentence.
- Rebuilt the debug APK from the Android Gradle project for release testing.

## v1.0.0 - 2026-05-16

- Updated Android package metadata to `versionCode = 100` and
  `versionName = "v1.0.0"` for the first release build.
- Added the MedLens logo as the Android launcher icon and packaged app icon.
- Refreshed the Android Compose UI with MedLens navy, teal, and purple
  branding, including the header, conversation list, message bubbles, composer,
  source chips, and first-run screen.
- Added the MedLens logo before the app name in the chat header, sidebar, and
  assistant messages.
- Simplified LiteRT-LM backend settings to plain `CPU` and `GPU` choices.
- Changed attached medicine images in sent chat messages to a two-column tile
  layout.
- Rebuilt the debug APK from the Android Gradle project for release testing.

## v0.1.12-pre-release - 2026-05-15

- Updated Android package metadata to `versionCode = 12` and
  `versionName = "0.1.12-pre-release"`.
- Fixed multi-turn medication checks so a complete current-turn pair such as
  `amiodarone and fluorouracil` is checked as that pair while prior chat
  context remains available for incomplete follow-ups.
- Added deterministic answer verification so model text that says no flagged
  interaction is replaced when the local structured report has a finding.
- Improved attached-image prompts so multiple images are treated as one
  combined medicine list without patient-facing image-processing preambles.
- Improved patient-facing fallback wording to lead with the practical finding
  instead of internal pair-count or local-reference phrasing.
- Added Android core-agent regression tests for contradictory model answers,
  current-turn pair precedence, incomplete follow-ups, and duplicate
  active-ingredient warnings like Crocin plus DOLO 650.
- Added a documented fresh debug APK rebuild command using repo-local Gradle
  8.7, `--rerun-tasks`, and `--max-workers=2`.

## v0.1.11 - 2026-05-14

- Added deterministic practical-risk calibration on top of reference DDI
  severity, so common short-term combinations can be explained with dose and
  patient-risk context without changing the underlying evidence severity.
- Added duplicate active-ingredient warnings, including acetaminophen /
  paracetamol duplicate-dose detection for combinations like Aldigesic-SP Forte
  plus Crocin.
- Added CSV-backed practical pair guidance for common outpatient painkiller
  patterns such as acetaminophen plus one NSAID, duplicate NSAIDs, and NSAID
  plus blood thinner/antiplatelet.
- Updated Android and Python report serialization so the agent can distinguish
  reference severity from practical interpretation while preserving existing
  SQLite lookup behavior.
- Hardened brand ingredient normalization for Android and Python so common
  phrase variants and close OCR/user typos like `aldigesic-sp`,
  `aldjgesic-sp`, `aldegesic-sp`, and `it is aldigesic-sp` resolve through the
  packaged brand ingredient map instead of falling through to an unresolved
  answer.
- Rebuilt `normalization.sqlite` with the practical guidance table included for
  Android packaging.

## v0.1.10 - 2026-05-14

- Changed image capture UX so captured photos attach to the composer first;
  users can type a question and send the text plus images together.
- Added support for up to 3 attached medicine images per message.
- Render attached image thumbnails in sent chat messages.
- Added a one-line composer disclaimer: MedLens is not a replacement for advice
  from a doctor or pharmacist.
- Warmed the Android agent prompt while keeping deterministic evidence rules.
- Suppressed patient-facing mentions of internal tool calls, normalization
  tooling, database internals, and image-extraction steps.
- Added `india_common_brand_ingredient_map.csv` as an Android-packaged asset for
  fast common brand/common-name to active-ingredient expansion before SQLite
  evidence lookup.
- Added CSV/SQLite ingredient-map support to Python artifact builds and Android
  runtime normalization.
- Preserved fallback behavior: if the CSV has no match or its ingredients do
  not resolve through `normalization.sqlite`, Android falls back to the existing
  SQLite normalization path.
- Rebuilt `normalization.sqlite` from a clean file so stale ingredient-map rows
  do not persist across artifact rebuilds.
- Audited the expanded 5,000-row brand ingredient CSV: 4,048 rows currently
  import into `medicine_ingredient_map`; 952 rows are skipped until their active
  ingredients are covered by `normalization.sqlite`.
- Verified examples including `Aldigesic SP Forte`, `Crocin`, and synthetic
  aspirin generic aliases through the deterministic Python safety path.
