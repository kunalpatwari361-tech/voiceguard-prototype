@echo off
REM Rebuilds the Android app (APK ends up in android\app\build\outputs\apk\debug\).
REM Needs JDK 17: uses JAVA_HOME if set, otherwise the copy in K:\vgtools.
cd /d "%~dp0android"
if not defined JAVA_HOME if exist "K:\vgtools\jdk-17.0.20.1+1" set "JAVA_HOME=K:\vgtools\jdk-17.0.20.1+1"
if not exist local.properties (
  if defined ANDROID_HOME (echo sdk.dir=%ANDROID_HOME:\=/%> local.properties) else if exist "K:\vgtools\android-sdk" (echo sdk.dir=K\:/vgtools/android-sdk> local.properties)
)
call gradlew.bat assembleDebug
