# Gradle Commands

This file records the Android and Gradle commands used while bringing up the
native MedLens app in this repo.

## Environment

These are the Android SDK environment variables expected in the shell:

```bash
export ANDROID_HOME=$HOME/Android/Sdk
export ANDROID_SDK_ROOT=$ANDROID_HOME
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin
export PATH=$PATH:$ANDROID_HOME/platform-tools
export PATH=$PATH:$ANDROID_HOME/emulator
```

## Current local constraints

- System `gradle -v` reports `4.4.1`, which is too old for Android Gradle
  Plugin `8.5.2`.
- The repo currently uses a downloaded Gradle `8.7` distribution from inside
  the workspace instead of the system Gradle.

## Commands used

Download Gradle 8.7:

```bash
curl -L -o /tmp/gradle-8.7-bin.zip \
  https://services.gradle.org/distributions/gradle-8.7-bin.zip
```

Extract Gradle 8.7 into the repo:

```bash
mkdir -p /home/ashu/github/MedLens/.gradle-dist
unzip -q -o /tmp/gradle-8.7-bin.zip \
  -d /home/ashu/github/MedLens/.gradle-dist
```

Create writable Gradle temp locations:

```bash
mkdir -p /tmp/medlens-gradle-home /tmp/medlens-gradle-tmp
```

Run the Android debug build:

```bash
ANDROID_SDK_ROOT=$HOME/Android/Sdk \
GRADLE_USER_HOME=/tmp/medlens-gradle-home \
/home/ashu/github/MedLens/.gradle-dist/gradle-8.7/bin/gradle \
  --no-daemon \
  -Djava.io.tmpdir=/tmp/medlens-gradle-tmp \
  :app:assembleDebug
```

Generate the standard Gradle wrapper:

```bash
ANDROID_SDK_ROOT=$HOME/Android/Sdk \
GRADLE_USER_HOME=/tmp/medlens-gradle-home \
/home/ashu/github/MedLens/.gradle-dist/gradle-8.7/bin/gradle \
  --no-daemon \
  -Djava.io.tmpdir=/tmp/medlens-gradle-tmp \
  wrapper \
  --gradle-version 8.7
```

Build through the generated wrapper from `android/`:

```bash
ANDROID_SDK_ROOT=$HOME/Android/Sdk \
GRADLE_USER_HOME=/tmp/medlens-gradle-home \
./gradlew \
  --no-daemon \
  -Djava.io.tmpdir=/tmp/medlens-gradle-tmp \
  :app:assembleDebug
```

Successful debug APK output:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

If you want a full stack trace during bring-up:

```bash
ANDROID_SDK_ROOT=$HOME/Android/Sdk \
GRADLE_USER_HOME=/tmp/medlens-gradle-home \
/home/ashu/github/MedLens/.gradle-dist/gradle-8.7/bin/gradle \
  --no-daemon \
  -Djava.io.tmpdir=/tmp/medlens-gradle-tmp \
  :app:assembleDebug \
  --stacktrace
```
