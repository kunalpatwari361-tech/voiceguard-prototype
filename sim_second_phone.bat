@echo off
REM Pretends to be a second family phone ("Rahul") when only one real phone or emulator is available.
cd /d "%~dp0backend"
set /p CODE=Family invite code shown in the app: 
.venv\Scripts\python.exe -u scripts\sim_family_phone.py --code %CODE% --name Rahul --phone 9876500002
pause
