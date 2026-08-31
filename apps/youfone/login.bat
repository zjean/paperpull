@echo off
cd /d "%~dp0"
rem  login.bat          -> your account (config.json, port 9243)
rem  login.bat partner  -> config.partner.json (own folders, profile, port)
if "%~1"=="" (set "CFG=") else (set "CFG=--config config.%~1.json")
echo ============================================================
echo  Youfone Facturen - sign in
echo ============================================================
if not "%~1"=="" echo Account: %~1
echo.
echo A normal browser window opens at https://my.youfone.nl/facturen. Then:
echo   1. Sign in with your Youfone account. This tool never
echo      sees your credentials and never touches the sign-in itself.
echo   2. Confirm you can see your list of facturen.
echo   3. LEAVE THAT BROWSER WINDOW OPEN - do not close it.
echo.
echo IMPORTANT: keep it to ONE MyYoufone tab. Youfone stores the session in
echo the tab it was opened in, so a second tab starts signed out.
echo.
.venv\Scripts\python.exe youfone_docs.py --open-browser %CFG%
echo.
pause
