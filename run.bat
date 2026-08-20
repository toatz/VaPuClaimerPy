@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0VaPuClaimer.pyw"
    exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0VaPuClaimer.pyw"
    exit /b
)
echo Python was not found.
echo Install Python 3 from python.org and make sure the Python Launcher is enabled.
pause
