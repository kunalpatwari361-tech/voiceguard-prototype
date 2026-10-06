@echo off
REM Finds adb.exe and puts its full path in %ADB%. Used by the other .bat scripts.
REM Search order: PATH, ANDROID_HOME, ANDROID_SDK_ROOT, Android Studio default, K:\vgtools.
set "ADB="
for %%P in (adb.exe) do if not "%%~$PATH:P"=="" set "ADB=%%~$PATH:P"
if not defined ADB if defined ANDROID_HOME if exist "%ANDROID_HOME%\platform-tools\adb.exe" set "ADB=%ANDROID_HOME%\platform-tools\adb.exe"
if not defined ADB if defined ANDROID_SDK_ROOT if exist "%ANDROID_SDK_ROOT%\platform-tools\adb.exe" set "ADB=%ANDROID_SDK_ROOT%\platform-tools\adb.exe"
if not defined ADB if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not defined ADB if exist "K:\vgtools\android-sdk\platform-tools\adb.exe" set "ADB=K:\vgtools\android-sdk\platform-tools\adb.exe"
if not defined ADB (
  echo adb.exe was not found. Install "Android SDK Platform-Tools" and set ANDROID_HOME,
  echo or see "adb is not recognized" in README.md.
  exit /b 1
)
exit /b 0
