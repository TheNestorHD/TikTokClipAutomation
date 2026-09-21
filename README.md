# TikTok Clip Automation

Automatiza el flujo de creación:

**Kick → descarga → deduplicación → edición vertical → subtítulos → exportado → cola → TikTok**

## Instalación

1. Python 3.11 o superior.
2. `pip install -r requirements.txt`
3. `python -m playwright install chromium`
4. Copiá `.env.example` como `.env`.
5. Completá `KICK_CHANNEL` y `NVIDIA_API_KEY`.
6. Poné el `cookies.txt` de TikTok en `TIKTOK_COOKIES_FILE`.
7. Tené FFmpeg disponible en PATH.

## Uso

`python tiktok_clip_automation.py`

La GUI permite guardar la configuración, arrancar/detener el pipeline y ver la cola de publicaciones.

## Watcher

Por defecto consulta los clips de Kick cada **1 segundo**. El intervalo se cambia con `KICK_POLL_SECONDS`. Si Kick responde con error, el watcher utiliza un backoff separado configurado por `KICK_ERROR_BACKOFF_SECONDS`.

## Edición

El motor conserva el pipeline de la versión anterior: detección de facecam con Kimi (hasta 5 intentos) y DiffusionGemma como fallback, Whisper, auto-trim con Nemotron Omni usando proxy 720p/1 FPS y los últimos 120 segundos para clips largos, y render vertical 1080x1920.

## TikTok

El uploader integrado reutiliza Playwright + cookies Netscape del script anterior.
Los Reels se guardan en una cola persistente para no publicar dos veces el mismo archivo. La programación usa la ventana horaria y el límite diario configurados en `.env`.

## Seguridad

No subas `.env` ni las cookies de TikTok al repositorio. La API key de NVIDIA también queda fuera del código.
