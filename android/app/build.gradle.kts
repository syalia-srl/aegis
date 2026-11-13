plugins {
    id("com.android.application")
}

// The release key comes from the environment (CI secrets, release.yml): a keystore file, its password, and the key's alias.
// The key's password is the store's. Without them the release build is unsigned and only the debug build installs.
val keystoreFile = System.getenv("AEGIS_KEYSTORE_FILE")
val keystorePassword = System.getenv("AEGIS_KEYSTORE_PASSWORD")
val keyAliasName = System.getenv("AEGIS_KEY_ALIAS") ?: "aegis"

android {
    namespace = "com.syalia.aegis"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.syalia.aegis"
        minSdk = 26
        targetSdk = 35
        // The shell's version: devices.LATEST_SHELL on the server names the newest. Raise both together.
        versionCode = 1
        versionName = (project.findProperty("versionName") as String?) ?: "0.1"
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        create("release") {
            if (keystoreFile != null && keystorePassword != null) {
                storeFile = file(keystoreFile)
                storePassword = keystorePassword
                keyAlias = keyAliasName
                keyPassword = keystorePassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystoreFile != null && keystorePassword != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.activity:activity:1.13.0")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.webkit:webkit:1.18.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // The JVM tests parse JSON, and Android's org.json is a stub there.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
