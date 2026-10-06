@echo off
REM Installs the VoiceGuard APK on every USB-connected phone, then links them to the server.
call "%~dp0tools\find_adb.bat" || (pause & exit /b 1)
set "APK=%~dp0android\app\build\outputs\apk\debug\app-debug.apk"
if not exist "%APK%" (
  echo APK not found. Run build_app.bat first.
  pause & exit /b 1
)
for /f "skip=1 tokens=1,2" %%a in ('call "%ADB%" devices') do (
  if "%%b"=="device" (
    echo Installing on %%a ...
    "%ADB%" -s %%a install -r -g "%APK%"
    "%ADB%" -s %%a reverse tcp:8000 tcp:8000
  )
)
