# Build and install the SensorSentry app with map feature
# Run this from the sensorsentry root directory
# Prerequisites: JDK 17, Android SDK, adb connected phone

$ErrorActionPreference = "Stop"

# Set up JAVA_HOME if needed
if (-not $env:JAVA_HOME) {
    $jdk = "C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot"
    if (Test-Path $jdk) { $env:JAVA_HOME = $jdk }
}

Write-Host "Using JAVA_HOME: $env:JAVA_HOME" -ForegroundColor Cyan

# Build
Push-Location android
try {
    if (Test-Path ".\gradlew.bat") {
        Write-Host "Building with gradlew..." -ForegroundColor Cyan
        .\gradlew.bat assembleDebug
        $apk = "app\build\outputs\apk\debug\app-debug.apk"
    } else {
        Write-Host "Building with gradle..." -ForegroundColor Cyan
        gradle assembleDebug
        $apk = "app\build\outputs\apk\debug\app-debug.apk"
    }
    if (-not (Test-Path $apk)) { throw "Build failed - APK not found" }
    Write-Host "Build succeeded: $apk" -ForegroundColor Green
    
    # Copy built APK to the tracked location
    Copy-Item $apk "SensorSentry-debug.apk" -Force
    Write-Host "Copied to android\SensorSentry-debug.apk" -ForegroundColor Green
} finally {
    Pop-Location
}

# Install
Write-Host "Installing on connected device..." -ForegroundColor Cyan
adb install -r "android\SensorSentry-debug.apk"
Write-Host "Done! App installed with map view." -ForegroundColor Green
