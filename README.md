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

La GUI permite guardar la configuración, arrancar/detener el pipeline y ver la cola de publicaciones. El watcher detecta clips independientemente del procesamiento, pero la edición usa una **única cola secuencial**, por lo que nunca se ejecutan dos procesos de edición/IA al mismo tiempo.

La versión 0.2 también incorpora el uploader de TikTok actualizado: espera a que TikTok habilite realmente el botón Post, maneja los diálogos de confirmación, valida señales de publicación, guarda capturas de error y reintenta automáticamente según la configuración.

## Watcher

Por defecto consulta los clips de Kick cada **1 segundo**. El intervalo se cambia con `KICK_POLL_SECONDS`. Si Kick responde con error, el watcher utiliza un backoff separado configurado por `KICK_ERROR_BACKOFF_SECONDS`.

El dedupe combina una ventana temporal, la identidad del stream, duración y miniatura. Además, `SAME_MOMENT_COOLDOWN_SECONDS` bloquea específicamente clips que representan prácticamente el mismo momento. Los clips aceptados quedan en una cola de procesamiento de un solo worker; `PROCESS_QUEUE_COOLDOWN_SECONDS` evita encadenar trabajos de inmediato.

## Edición

El motor conserva el pipeline de la versión anterior: detección de facecam con Kimi (hasta 5 intentos) y DiffusionGemma como fallback, Whisper, auto-trim con Nemotron Omni usando proxy 720p/1 FPS y los últimos 120 segundos para clips largos, y render vertical 1080x1920.

## TikTok

El uploader integrado usa Playwright + cookies Netscape. Los Reels se guardan en una cola persistente para evitar publicaciones duplicadas.

La programación respeta la ventana horaria, el límite diario y `TIKTOK_UPLOAD_INTERVAL_MINUTES` como intervalo mínimo entre publicaciones. `TIKTOK_VARIATION_MINUTES` agrega una pequeña variación al horario. Las subidas fallidas tienen reintentos propios (`TIKTOK_UPLOAD_RETRIES`) con una espera configurable.

En modo visible, `TIKTOK_MINIMIZED=true` intenta mantener Chromium fuera del área visible durante la automatización.

## Seguridad

No subas `.env` ni las cookies de TikTok al repositorio. La API key de NVIDIA también queda fuera del código.
