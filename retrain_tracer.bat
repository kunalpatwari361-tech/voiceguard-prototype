@echo off
REM Retrains the VoiceGuard Source Tracer: human / AI clone-TTS / robotic TTS / AI voice changer / voice-changer app.
REM Makes the voice-changer training clips once, re-uses the detector's cached WavLM features, runs at low priority.
REM Feature extraction pauses itself after 100 minutes - just run this again to continue.
REM Result: backend\app\ai\weights\vg_tracer.npz + vg_tracer_room.npz (+ _report.json). Restart start_server.bat after.
cd /d "%~dp0backend"
if not exist K:\vgtools\data\vgset\manifest_vc.csv .venv\Scripts\python.exe training\add_voice_changer.py K:\vgtools\data\vgset
.venv\Scripts\python.exe -u training\lowprio.py training\train_tracer.py K:\vgtools\data\vgset --max-minutes 100
pause
