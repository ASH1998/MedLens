# MedLens Android

Native Android client for the MedLens medication-safety app.

## Modules

- `app/`: Compose UI, camera capture, conversation persistence, model download
  manager, TTS, and Android entry points.
- `core-data/`: bundled SQLite artifact copy + deterministic MedLens safety
  repository.
- `core-agent/`: prompts, tool registry, agent orchestrator, and fallback
  provider.

## Bundled Assets

The app copies these from the repository root at build time:

| Asset | Source path | Purpose |
| --- | --- | --- |
| `normalization.sqlite` | `data/artifacts/normalization.sqlite` | Canonical drug names, aliases, India common-medicine metadata |
| `evidence.mobile.sqlite` | `data/artifacts/evidence.mobile.sqlite` | DDI pairs, effects, raw signals, import issues |
| `india_common_brand_ingredient_map.csv` | `data/raw/DDI/india_common_brand_ingredient_map.csv` | Brand-to-ingredient mapping for India medicines |

Build the artifacts first (from repo root):

```bash
python3 -m medlens.artifacts.build_normalization \
  --output data/artifacts/normalization.sqlite \
  --common-medicines-csv data/raw/DDI/common_medicines_india_dataset_5000.csv

python3 -m medlens.artifacts.build_evidence \
  --input-dir data/raw/DDI \
  --normalization-db data/artifacts/normalization.sqlite \
  --output data/artifacts/evidence.sqlite

.venv/bin/python -m medlens.artifacts.build_evidence \
  --compact-from data/artifacts/evidence.sqlite \
  --output data/artifacts/evidence.mobile.sqlite
```

## OCR and Image Path

1. CameraX captures a photo.
2. **ML Kit OCR** runs first to extract text from the image.
3. Extracted candidate names are normalized through `SafetyRepository`.
4. If ML Kit produces no useful candidates, **Gemma vision** (LiteRT multimodal)
   can optionally enhance extraction when the model is downloaded.
5. Normalized candidates go through the deterministic safety report path.

The deterministic OCR+SQLite path works without Gemma downloaded.

## Model

The target model is `litert-community/gemma-4-E4B-it-litert-lm` (~3.66 GB).

- Downloaded via WorkManager with SHA-256 verification.
- A metadata sidecar (`.verified.json`) is written after successful
  verification so startup does not need to re-hash 3.66 GB.
- Model readiness is SHA-verified, not just size-checked.
- Selectable CPU or GPU backend at runtime.

## Offline Behavior

- Typed medication checks work without the model downloaded.
- Camera OCR works without the model downloaded (ML Kit is local).
- All safety data comes from bundled SQLite — no network required.
- TTS defaults to Android native `TextToSpeech`.
- Remote TTS (MiMo API) is opt-in via settings and requires an API key.
- Model download is the only required network operation.

## Gradle

The Gradle root is `android/`, not the repository root.

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

Run all unit tests:

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

## To start building

1. Install Android Studio latest stable.
2. Install Android SDK Platform 35, platform-tools, and one ARM64 emulator
   image.
3. Use Android Studio's bundled JDK 17, or install JDK 17 separately.
4. Open the `android/` directory as a project.
5. Let Gradle sync, then run the `app` configuration on an emulator or device.

VSCode is fine for editing, but Android Studio is still required in practice
for SDK management, emulator, Gradle sync, Logcat, and profiler support.
