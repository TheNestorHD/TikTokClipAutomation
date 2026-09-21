# TikTok Clip Automation

Automatiza el flujo de creación:

**Kick → descarga → deduplicación → edición vertical → subtítulos → exportado → cola → TikTok**

## Instalación

1. Python 3.11 o superior.
2. `pip install -r requirements.txt`
3. `python -m playwright install chromium`
4. Copiá `.env.example` como `.env`.
5. Iniciá **TTCA.pyw** para abrir la aplicación sin consola.
6. Completá la configuración desde la interfaz.

La interfaz incluye accesos directos para obtener una API Key de NVIDIA desde NVIDIA Build, instalar la extensión de exportación de cookies y abrir TikTok Studio. NVIDIA publica su página oficial de gestión de API Keys en `build.nvidia.com/settings/api-keys`. La extensión recomendada en la interfaz es **Get cookies.txt LOCALLY**, que exporta cookies en formato Netscape localmente.
## Uso

`python tiktok_clip_automation.py`

La interfaz de escritorio muestra el estado del watcher, las colas, la etapa actual, el tiempo activo, TikTok y los errores. La edición usa una **única cola secuencial**, por lo que nunca se ejecutan dos procesos de edición/IA al mismo tiempo.

La versión actual también incorpora un uploader de TikTok guiado y sin programación horaria: cuando termina un Reel y la publicación automática está activada, entra directamente a la cola de TikTok.

## Watcher

Por defecto consulta los clips de Kick cada **1 segundo**. El intervalo se cambia con `KICK_POLL_SECONDS`. Si Kick responde con error, el watcher utiliza un backoff separado configurado por `KICK_ERROR_BACKOFF_SECONDS`.

El dedupe combina una ventana temporal, la identidad del stream, duración y miniatura. Además, `SAME_MOMENT_COOLDOWN_SECONDS` bloquea específicamente clips que representan prácticamente el mismo momento. Los clips aceptados quedan en una cola de procesamiento de un solo worker; `PROCESS_QUEUE_COOLDOWN_SECONDS` evita encadenar trabajos de inmediato.

## Edición

El motor conserva el pipeline anterior: detección de facecam con Kimi (hasta 5 intentos) y DiffusionGemma como fallback, Whisper, auto-trim con Nemotron Omni usando proxy 720p/1 FPS y los últimos 120 segundos para clips largos, y render vertical 1080x1920.

FFmpeg se lanza en Windows con prioridad **Idle** por defecto y con un número reducido de hilos para que pueda seguir funcionando junto a OBS con menor competencia por CPU. Se puede ajustar `FFMPEG_PRIORITY` y `FFMPEG_THREADS` desde la configuración avanzada del programa.

## TikTok

El uploader integrado usa Playwright + cookies Netscape. Los Reels se guardan en una cola persistente para evitar publicaciones duplicadas.

No se aplican horarios, franjas, límites diarios ni intervalos artificiales: un Reel terminado se publica inmediatamente cuando la opción de automatización está activada. Los fallos usan los reintentos internos configurados en `TIKTOK_UPLOAD_RETRIES`; con el valor predeterminado no hay espera adicional entre reintentos.

La interfaz permite activar/desactivar la publicación automática, seleccionar el archivo de cookies y abrir las páginas necesarias para preparar la cuenta.

## Seguridad

No subas `.env` ni las cookies de TikTok al repositorio. La API key de NVIDIA también queda fuera del código.
