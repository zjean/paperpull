@echo off
cd /d "%~dp0"
if "%~1"=="" (set "CFG=") else (set "CFG=--config config.%~1.json")
echo Resuming the Youfone factuur download.
if not "%~1"=="" echo Account: %~1
echo Make sure that account's signed-in MyYoufone tab is still OPEN.
.venv\Scripts\python.exe youfone_docs.py --resume %CFG%
pause
