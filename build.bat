@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "APP_NAME=osu-sayobot-helper"

set "PYTHON_CMD="

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "pass" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=.venv\Scripts\python.exe"
)

if "%PYTHON_CMD%"=="" (
    py -3 -c "pass" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if "%PYTHON_CMD%"=="" (
    python -c "pass" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if "%PYTHON_CMD%"=="" (
    echo No usable Python was found.
    echo Please install Python 3.8+ or recreate the project venv.
    exit /b 1
)

echo Using Python command: %PYTHON_CMD%

echo Installing runtime requirements...
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install runtime requirements.
    exit /b 1
)

%PYTHON_CMD% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo PyInstaller was not found. Installing...
    %PYTHON_CMD% -m pip install pyinstaller
    if errorlevel 1 (
        echo Failed to install PyInstaller.
        echo Please check your network or run manually:
        echo %PYTHON_CMD% -m pip install pyinstaller
        exit /b 1
    )
)

echo Building %APP_NAME%.exe ...
%PYTHON_CMD% -m PyInstaller --onefile --windowed --icon assets\app.ico --add-data "assets\app.ico;assets" --add-data "assets\app-source.png;assets" --name "%APP_NAME%" --clean --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtOpenGL --exclude-module PySide6.QtSvg --exclude-module PySide6.QtPrintSupport main.py
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

if not exist dist mkdir dist
copy /Y config.json dist\config.json >nul
if errorlevel 1 (
    echo Failed to copy config.json.
    exit /b 1
)

echo.
echo Build complete:
echo dist\%APP_NAME%.exe
echo dist\config.json
echo.
echo Send both files in dist to users without Python.
endlocal
