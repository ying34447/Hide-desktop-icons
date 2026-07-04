@echo off
setlocal enabledelayedexpansion

REM ==========================================================
REM  DesktopHider - One-click PyInstaller build script
REM  Pure ASCII to avoid cmd encoding issues.
REM  Update VERSION below to match main.py VERSION constant.
REM ==========================================================

set "VERSION=1.4.0"
set "APP_NAME=DesktopHider%VERSION%"
set "ENTRY=main.py"
set "ICON=app.ico"

echo ========================================
echo   DesktopHider Build Script
echo   Version: %VERSION%
echo ========================================
echo.

REM ---- Check Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+ first.
    pause
    exit /b 1
)

REM ---- Check PyInstaller ----
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [INFO] PyInstaller not found, installing...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller installation failed.
        pause
        exit /b 1
    )
)

REM ---- Check icon file ----
if not exist "%ICON%" (
    echo [WARN] Icon file '%ICON%' not found, using default icon.
    set "ICON_ARG="
    set "ADD_DATA="
) else (
    set "ICON_ARG=--icon=%ICON%"
    set "ADD_DATA=--add-data %ICON%;."
)

echo [INFO] Output: dist\%APP_NAME%.exe
echo [INFO] Entry:  %ENTRY%
if defined ICON_ARG echo [INFO] Icon:  %ICON%
echo.

REM ---- Clean old build artifacts ----
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"
if exist "DesktopHider.spec" del /q "DesktopHider.spec"

REM ---- Run PyInstaller (single line, no caret continuations) ----
REM [FEATURE-3] --collect-all keyboard: ensure keyboard lib hooks bundled
python -m PyInstaller -F -w --name=%APP_NAME% %ICON_ARG% %ADD_DATA% --exclude-module pytest --exclude-module numpy --exclude-module scipy --exclude-module pandas --exclude-module matplotlib --exclude-module IPython --exclude-module notebook --exclude-module tkinter.test --exclude-module unittest --exclude-module pydoc --collect-all PIL --collect-all keyboard --clean %ENTRY%

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See errors above.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Build Success!
echo ========================================
echo.
echo   Output: dist\%APP_NAME%.exe
echo   (matches PHP download_url: DesktopHider%VERSION%.exe)
echo.

REM ---- Also copy a version-less name for convenience ----
if exist "dist\%APP_NAME%.exe" (
    copy "dist\%APP_NAME%.exe" "dist\DesktopHider.exe" >nul 2>nul
    echo   Also copied as: dist\DesktopHider.exe (generic name)
    echo.
)

REM ---- Ask to open output folder ----
set /p "choice=Open output folder? (Y/N): "
if /i "%choice%"=="Y" (
    explorer dist
)

endlocal
