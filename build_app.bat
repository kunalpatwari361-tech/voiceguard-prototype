@echo off
REM Rebuilds the Android app (APK ends up in android\app\build\outputs\apk\debug\).
cd /d "%~dp0android"
set JAVA_HOME=K:\vgtools\jdk-17.0.20.1+1
call gradlew.bat assembleDebug
