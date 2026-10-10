@echo off
REM Starts the VoiceGuard AI server on this laptop (port 8000).
REM Phones find it by themselves - over the same Wi-Fi (or the laptop's Mobile hotspot), or over USB.
cd /d "%~dp0backend"
echo Loading AI models (first start takes ~30 s)...
echo.
echo Phones on the same Wi-Fi connect by themselves - no cable needed. This laptop's addresses:
ipconfig | findstr /c:"IPv4"
echo If Windows Firewall asks: tick "Private networks" and click Allow (only the first time).
echo.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
