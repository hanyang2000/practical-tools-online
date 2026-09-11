plugins {
    id("com.android.application")
    kotlin("android")
}

val configuredCenterUrl = providers.gradleProperty("centerUrl")
    .orElse("https://collab.wnnttzy.kdns.fr/")
    .get()
val allowCleartext = providers.gradleProperty("allowCleartextTraffic")
    .orElse("false")
    .get()

android {
    namespace = "com.practicaltools.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.practicaltools.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 18
        versionName = "0.4.6"

        buildConfigField("String", "DEFAULT_CENTER_URL", "\"${configuredCenterUrl.replace("\\", "\\\\").replace("\"", "\\\"")}\"")
        manifestPlaceholders["allowCleartextTraffic"] = allowCleartext
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

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
