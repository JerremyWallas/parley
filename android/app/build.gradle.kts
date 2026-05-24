plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.antigravity.speechtotext"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.antigravity.speechtotext"
        // minSdk 29 (Android 10): MediaRecorder.OutputFormat.OGG +
        // AudioEncoder.OPUS sowie FOREGROUND_SERVICE_TYPE_MICROPHONE
        // brauchen API 29. Kostet uns Android 8/9 Support (<3% Markt 2026).
        minSdk = 29
        targetSdk = 35
        versionCode = 2
        versionName = "1.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }

    // BuildConfig.DEBUG wird in WebSettingsActivity referenziert, um WebView-
    // DevTools nur im Debug-Build zu aktivieren. Ab AGP 8 muss buildConfig
    // explizit eingeschaltet werden.
    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
