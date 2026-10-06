@echo off
REM Lets every USB-connected phone reach the laptop server at 127.0.0.1:8000.
set ADB=K:\vgtools\android-sdk\platform-tools\adb.exe
%ADB% devices
for /f "skip=1 tokens=1,2" %%a in ('%ADB% devices') do (
  if "%%b"=="device" (
    echo Linking %%a ...
    %ADB% -s %%a reverse tcp:8000 tcp:8000
  )
)
echo Done. Re-run this after you unplug/replug a phone.
