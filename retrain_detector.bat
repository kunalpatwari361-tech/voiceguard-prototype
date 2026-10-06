@echo off
REM Continues training the VoiceGuard AI-voice detector (resumes from saved progress).
REM Runs at low priority. Feature extraction pauses itself after 100 minutes - just run this again to continue.
REM Result: backend\app\ai\weights\vg_detector_candidate.npz + _report.json (accuracy per language and phone channel).
REM If it is better, copy it over vg_detector.npz and restart start_server.bat.
cd /d "%~dp0backend"
.venv\Scripts\python.exe -u training\lowprio.py training\train_detector.py K:\vgtools\data\vgset --max-minutes 100
pause
