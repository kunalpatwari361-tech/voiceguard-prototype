@echo off
REM Makes "adb" work in every NEW terminal by adding platform-tools to your user PATH.
call "%~dp0tools\find_adb.bat" || (pause & exit /b 1)
for %%D in ("%ADB%") do set "ADBDIR=%%~dpD"
set "ADBDIR=%ADBDIR:~0,-1%"
powershell -NoProfile -Command "$d='%ADBDIR%'; $u=[Environment]::GetEnvironmentVariable('Path','User'); if (($u -split ';') -notcontains $d) { [Environment]::SetEnvironmentVariable('Path', ($u.TrimEnd(';') + ';' + $d).TrimStart(';'), 'User'); 'Added to PATH: ' + $d } else { 'Already on PATH: ' + $d }"
echo Close and reopen your terminal (or restart the app) so it picks up the new PATH.
pause
