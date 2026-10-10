@echo off
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul || goto :nopython

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv || goto :nopython
)

echo Installing / checking dependencies...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail

".venv\Scripts\python.exe" launch.py
goto :eof

:nopython
echo.
echo Python 3.9 or newer is required.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1

:fail
echo.
echo Dependency install failed. Check your internet connection and try again.
pause
exit /b 1
