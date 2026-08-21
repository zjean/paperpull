@echo off
cd /d "%~dp0"
if "%~1"=="" (set "CFG=") else (set "CFG=--config config.%~1.json")
echo Read-only inspection of what your Facturen page shows. Downloads NOTHING.
if not "%~1"=="" echo Account: %~1
echo Make sure you are signed in and your MyYoufone tab is still OPEN.
.venv\Scripts\python.exe youfone_docs.py --diagnose %CFG%
pause
