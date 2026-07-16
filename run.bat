@echo off
setlocal
cd /d "%~dp0"

echo.
echo ============================================
echo   Hi Seoul Company Local Data Manager
echo ============================================
echo.

if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo [SETUP] Created .env from .env.example.
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 goto venv_ready
)

echo [1/3] Creating a Python virtual environment...
echo       Searching for a usable Python 3 installation...

call :try_py_launcher
if exist ".venv\Scripts\python.exe" goto venv_ready

call :try_python_command
if exist ".venv\Scripts\python.exe" goto venv_ready

call :try_python_exe "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist ".venv\Scripts\python.exe" goto venv_ready

for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
) do (
    call :try_python_exe "%%~P"
    if exist ".venv\Scripts\python.exe" goto venv_ready
)

echo.
echo [ERROR] A usable Python 3 installation was not found.
echo.
echo Install 64-bit Python 3.10 or newer from:
echo https://www.python.org/downloads/windows/
echo.
echo IMPORTANT: Enable "Add python.exe to PATH" in the installer.
echo Then close this window and run run.bat again.
pause
exit /b 1

:venv_ready
echo [1/3] Python environment is ready.

".venv\Scripts\python.exe" -c "import fastapi, httpx, openpyxl, dotenv, tzdata" >nul 2>&1
if errorlevel 1 (
    echo [2/3] Installing required packages. The first run may take a few minutes...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Package installation failed. Check your internet connection.
        pause
        exit /b 1
    )
) else (
    echo [2/3] Required packages are ready.
)

echo [3/3] Starting server at http://127.0.0.1:8765
echo Press Ctrl+C in this window to stop the server.
echo.
".venv\Scripts\python.exe" -m app

echo.
echo Server stopped.
pause
endlocal
exit /b 0

:try_py_launcher
where py >nul 2>&1
if errorlevel 1 exit /b 0
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
echo       Using the Windows Python launcher.
py -3 -m venv --clear ".venv" >nul 2>&1
exit /b 0

:try_python_command
where python >nul 2>&1
if errorlevel 1 exit /b 0
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
echo       Using Python from PATH.
python -m venv --clear ".venv" >nul 2>&1
exit /b 0

:try_python_exe
set "PYTHON_CANDIDATE=%~1"
if not exist "%PYTHON_CANDIDATE%" exit /b 0
"%PYTHON_CANDIDATE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
echo       Using a local Python 3 executable.
"%PYTHON_CANDIDATE%" -m venv --clear ".venv" >nul 2>&1
exit /b 0
