@echo off
REM Installs the VoiceGuard APK on every USB-connected phone, then links them to the server.
set ADB=K:\vgtools\android-sdk\platform-tools\adb.exe
set APK=%~dp0android\app\build\outputs\apk\debug\app-debug.apk
for /f "skip=1 tokens=1,2" %%a in ('%ADB% devices') do (
  if "%%b"=="device" (
    echo Installing on %%a ...
    %ADB% -s %%a install -r -g "%APK%"
    %ADB% -s %%a reverse tcp:8000 tcp:8000
  )
)
