plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

val medlensAssetDir = layout.buildDirectory.dir("generated/medlens-assets")
val enableDebugHfToken = true
val hfAccessToken = providers.environmentVariable("HF_ACCESS_TOKEN")
    .orElse(providers.provider {
        val envFile = rootProject.file("../.env")
        if (!envFile.exists()) {
            ""
        } else {
            envFile.readLines()
                .firstOrNull { it.startsWith("HF_ACCESS_TOKEN=") }
                ?.substringAfter("=")
                ?.trim()
                ?.removeSurrounding("\"")
                ?: ""
        }
    })
    .get()

fun gradleStringLiteral(value: String): String = "\"${value.replace("\\", "\\\\").replace("\"", "\\\"")}\""

val syncMedlensAssets by tasks.registering(Copy::class) {
    from("../../data/artifacts/normalization.sqlite")
    from("../../data/artifacts/evidence.mobile.sqlite")
    from("../../data/raw/DDI/india_common_brand_ingredient_map.csv")
    into(medlensAssetDir)
}

android {
    namespace = "com.medlens.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.medlens.android"
        minSdk = 31
        targetSdk = 35
        versionCode = 11
        versionName = "0.1.11"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables {
            useSupportLibrary = true
        }
    }

    buildTypes {
        debug {
            buildConfigField(
                "String",
                "HF_ACCESS_TOKEN",
                gradleStringLiteral(if (enableDebugHfToken) hfAccessToken else ""),
            )
        }
        release {
            isMinifyEnabled = false
            buildConfigField("String", "HF_ACCESS_TOKEN", "\"\"")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }


    buildFeatures {
        buildConfig = true
        compose = true
    }

    androidResources {
        noCompress += listOf("sqlite")
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    sourceSets["main"].assets.srcDir(medlensAssetDir)
}

tasks.named("preBuild") {
    dependsOn(syncMedlensAssets)
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(project(":core-data"))
    implementation(project(":core-agent"))

    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.navigation:navigation-compose:2.8.3")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("androidx.datastore:datastore-preferences:1.1.1")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.camera:camera-core:1.4.0")
    implementation("androidx.camera:camera-camera2:1.4.0")
    implementation("androidx.camera:camera-lifecycle:1.4.0")
    implementation("androidx.camera:camera-view:1.4.0")
    implementation("com.google.mlkit:text-recognition:16.0.0")
    implementation("com.google.ai.edge.litertlm:litertlm-android:0.11.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    testImplementation("junit:junit:4.13.2")

    androidTestImplementation(platform("androidx.compose:compose-bom:2024.10.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
