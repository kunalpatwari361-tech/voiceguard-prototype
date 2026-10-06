@echo off
REM Continues training the VoiceGuard AI-voice detector (resumes from saved progress).
REM Runs at low priority; takes about 1-2 hours on this laptop. Restart start_server.bat afterwards.
cd /d "%~dp0backend"
.venv\Scripts\python.exe -u training\lowprio.py training\train_detector.py K:\vgtools\data\vgset
pause
