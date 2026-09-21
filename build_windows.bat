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
echo Buscando herramientas multimedia para incluir en el paquete...
set "FFMPEG_SOURCE="
set "FFPROBE_SOURCE="
set "YTDLP_SOURCE="

for /f "delims=" %%F in ('where ffmpeg 2^>nul') do if not defined FFMPEG_SOURCE set "FFMPEG_SOURCE=%%F"
for /f "delims=" %%F in ('where ffprobe 2^>nul') do if not defined FFPROBE_SOURCE set "FFPROBE_SOURCE=%%F"
for /f "delims=" %%F in ('where yt-dlp 2^>nul') do if not defined YTDLP_SOURCE set "YTDLP_SOURCE=%%F"

if not defined FFMPEG_SOURCE (
    echo ERROR: FFmpeg no esta disponible en PATH.
    echo Instala FFmpeg antes de compilar TTCA para incluirlo en el paquete.
    pause
    exit /b 1
)
if not defined FFPROBE_SOURCE (
    echo ERROR: FFprobe no esta disponible en PATH.
    echo FFprobe es necesario para analizar los videos.
    pause
    exit /b 1
)
if not defined YTDLP_SOURCE (
    echo AVISO: yt-dlp no se encontro como ejecutable.
    echo TTCA seguira pudiendo usar el fallback de FFmpeg.
)

echo FFmpeg:  %FFMPEG_SOURCE%
echo FFprobe: %FFPROBE_SOURCE%
if defined YTDLP_SOURCE echo yt-dlp:  %YTDLP_SOURCE%

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
echo Copiando herramientas multimedia al bundle...
if not exist "dist\TTCA\tools\bin" mkdir "dist\TTCA\tools\bin"
copy /y "%FFMPEG_SOURCE%" "dist\TTCA\tools\bin\ffmpeg.exe" >nul
copy /y "%FFPROBE_SOURCE%" "dist\TTCA\tools\bin\ffprobe.exe" >nul
if defined YTDLP_SOURCE copy /y "%YTDLP_SOURCE%" "dist\TTCA\tools\bin\yt-dlp.exe" >nul

echo.
echo ============================================
echo   COMPILACION TERMINADA
echo ============================================
echo.
echo Ejecutable:
echo   dist\TTCA\TTCA.exe
echo.
echo Importante:
echo   - El resultado es un paquete autocontenido en dist\TTCA.
echo   - Deja .env junto a TTCA.exe.
echo   - Las cookies NO se incluyen en la compilacion.
echo   - El archivo .env y tiktok_cookies.txt deben mantenerse privados.
echo   - FFmpeg, FFprobe y yt-dlp quedan dentro de tools\bin cuando fueron encontrados al compilar.
echo   - Playwright Chromium queda dentro de playwright-browsers.
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
