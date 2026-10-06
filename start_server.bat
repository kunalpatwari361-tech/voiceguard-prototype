@echo off
REM Starts the VoiceGuard AI server on this laptop (port 8000).
cd /d "%~dp0backend"
echo Loading AI models (first start takes ~30 s)...
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
