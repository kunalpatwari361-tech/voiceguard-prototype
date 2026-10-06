@echo off
REM Pretends to be a second family phone ("Rahul") when only one real phone or emulator is available.
REM Its number +919876500002 must be in VG_OTP_TEST_NUMBERS (backend\.env) with code 246810 - no SMS is sent.
cd /d "%~dp0backend"
set /p CODE=Family invite code shown in the app: 
.venv\Scripts\python.exe -u scripts\sim_family_phone.py --code %CODE% --name Rahul --phone 9876500002 --otp 246810
pause
