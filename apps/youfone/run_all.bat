@echo off
cd /d "%~dp0"
rem  run_all.bat          -> your account
rem  run_all.bat partner  -> partner's account (separate folders + progress)
if "%~1"=="" (set "CFG=") else (set "CFG=--config config.%~1.json")
echo FULL download of every Youfone factuur that still has a PDF.
if not "%~1"=="" echo Account: %~1
echo Run the pilot first if you have not: run_pilot.bat %~1
echo Make sure that account's signed-in MyYoufone tab is still OPEN.
.venv\Scripts\python.exe youfone_docs.py --all %CFG%
pause
