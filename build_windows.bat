@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo   TTCA - Build Windows
echo ============================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta disponible como "py".
    pause
    exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" (
    echo Creando entorno de compilacion...
    py -3.11 -m venv .venv-build
    if errorlevel 1 goto :error
)

call ".venv-build\Scripts\activate.bat"
if errorlevel 1 goto :error

python -m pip install --upgrade pip
if errorlevel 1 goto :error

python -m pip install -r requirements.txt
if errorlevel 1 goto :error

python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :error

echo.
echo Instalando Chromium de Playwright en una carpeta portable...
if not exist "playwright-browsers" mkdir "playwright-browsers"
set PLAYWRIGHT_BROWSERS_PATH=%CD%\playwright-browsers
python -m playwright install chromium
if errorlevel 1 goto :error

echo.
echo Limpiando compilaciones anteriores...
if exist "build" rmdir /s /q "build"
if exist "dist\TTCA" rmdir /s /q "dist\TTCA"

echo.
echo Compilando TTCA...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --name TTCA ^
  --hidden-import=cv2 ^
  --hidden-import=numpy ^
  --hidden-import=PIL ^
  --hidden-import=faster_whisper ^
  tiktok_clip_automation.py

if errorlevel 1 goto :error

echo.
echo Copiando recursos externos...
if exist "assets" xcopy /e /i /y "assets" "dist\TTCA\assets" >nul
if exist "tools" xcopy /e /i /y "tools" "dist\TTCA\tools" >nul
xcopy /e /i /y "playwright-browsers" "dist\TTCA\playwright-browsers" >nul

echo.
echo ============================================
echo   COMPILACION TERMINADA
echo ============================================
echo.
echo Ejecutable:
echo   dist\TTCA\TTCA.exe
echo.
echo Importante:
echo   - Deja .env junto a TTCA.exe.
echo   - Las cookies NO se incluyen en la compilacion.
echo   - El archivo .env y tiktok_cookies.txt deben mantenerse privados.
echo.
pause
exit /b 0

:error
echo.
echo ============================================
echo   ERROR DURANTE LA COMPILACION
echo ============================================
echo.
pause
exit /b 1
