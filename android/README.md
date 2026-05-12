# MedLens Android

Native Android scaffold for the MedLens LiteRT-LM app.

## What is included

- `app/`: Compose UI shell, conversation persistence, first-run setup, model
  download manager, and Android entrypoints
- `core-data/`: bundled SQLite artifact copy + deterministic MedLens repository
- `core-agent/`: prompts, chat session model, tool registry, deterministic
  formatter, and Android agent orchestrator

The app bundles these artifacts from the repository root:

- `../data/artifacts/normalization.sqlite`
- `../data/artifacts/evidence.mobile.sqlite`

The target model is:

- `litert-community/gemma-4-E4B-it-litert-lm`
- file: `gemma-4-E4B-it.litertlm`

## Current state

- Deterministic SQLite-backed safety logic is scaffolded in Kotlin.
- Compose chat UI mirrors the current PWA shell structure.
- Gemma model download and checksum verification are wired through WorkManager.
- LiteRT-LM inference is not validated locally in this repo yet because the
  current WSL environment does not have Java, Gradle, Android SDK, or Android
  Studio installed.

## To start building

1. Install Android Studio latest stable.
2. Install Android SDK Platform 35, platform-tools, and one ARM64 emulator
   image.
3. Use Android Studio's bundled JDK 17, or install JDK 17 separately.
4. Open the `android/` directory as a project.
5. Let Gradle sync, then run the `app` configuration on an emulator or device.

VSCode is fine for editing, but Android Studio is still required in practice
for SDK management, emulator, Gradle sync, Logcat, and profiler support.
