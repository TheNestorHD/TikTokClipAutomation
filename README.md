# TikTok Clip Automation

Automatiza el flujo de creación:

**Kick → descarga → deduplicación → edición vertical → subtítulos → exportado → cola → TikTok**

## Instalación

1. Python 3.11 o superior.
2. `pip install -r requirements.txt`
3. `python -m playwright install chromium`
4. Copiá `.env.example` como `.env`.
5. Iniciá **TTCA.pyw** para abrir la aplicación sin consola. Si Windows no tiene asociada la extensión `.pyw` a Python, usá **TTCA.vbs** (también es invisible).
6. Completá la configuración desde la interfaz.

La interfaz incluye accesos directos para obtener una API Key de NVIDIA desde NVIDIA Build, instalar la extensión de exportación de cookies y abrir TikTok Studio. NVIDIA publica su página oficial de gestión de API Keys en `build.nvidia.com/settings/api-keys`. La extensión recomendada en la interfaz es **Get cookies.txt LOCALLY**, que exporta cookies en formato Netscape localmente.
## Uso

`python tiktok_clip_automation.py`

La interfaz de escritorio muestra el estado del watcher, las colas, la etapa actual, el tiempo activo, TikTok y los errores. El acento visual de la aplicación usa el verde de Kick (`#53FC18`). La edición usa una **única cola secuencial**, por lo que nunca se ejecutan dos procesos de edición/IA al mismo tiempo.

La versión actual también incorpora un uploader de TikTok guiado y sin programación horaria: cada Reel terminado entra a la cola de TikTok. Según la configuración, se publica inmediatamente o se sube como borrador para revisión.

## Rutas

Las rutas estándar se configuran automáticamente como rutas relativas a la carpeta del programa (`data/clips`, `data/reels`, `data/used`, `assets`, `tools`, etc.). TTCA crea automáticamente las carpetas necesarias al iniciar. Si elegís una ubicación externa desde la interfaz, esa ruta se conserva como absoluta; las rutas internas al programa se vuelven a guardar como relativas.

## Watcher

Por defecto consulta los clips de Kick cada **1 segundo**. El intervalo se cambia con `KICK_POLL_SECONDS`. Si Kick responde con error, el watcher utiliza un backoff separado configurado por `KICK_ERROR_BACKOFF_SECONDS`.

El dedupe combina una ventana temporal, la identidad del stream, duración y miniatura. Además, `SAME_MOMENT_COOLDOWN_SECONDS` bloquea específicamente clips que representan prácticamente el mismo momento. Los clips aceptados quedan en una cola de procesamiento de un solo worker; `PROCESS_QUEUE_COOLDOWN_SECONDS` evita encadenar trabajos de inmediato.

## Edición

El motor conserva el pipeline anterior: detección de facecam con Kimi (hasta 5 intentos) y DiffusionGemma como fallback, Whisper, auto-trim con Nemotron Omni usando proxy 720p/1 FPS y los últimos 120 segundos para clips largos, y render vertical 1080x1920.

FFmpeg se lanza en Windows con prioridad **Idle** por defecto y con un número reducido de hilos para que pueda seguir funcionando junto a OBS con menor competencia por CPU. Se puede ajustar `FFMPEG_PRIORITY` y `FFMPEG_THREADS` desde la configuración avanzada del programa.

## TikTok

El uploader integrado usa Playwright + cookies Netscape. Los Reels se guardan en una cola persistente para evitar publicaciones duplicadas.

No se aplican horarios, franjas, límites diarios ni intervalos artificiales. Un Reel terminado se procesa inmediatamente:
- **Publicar automáticamente activado:** se publica en TikTok.
- **Publicar automáticamente desactivado:** se sube a TikTok y se guarda como borrador, sin publicarlo.

La descripción admite dos modos. **Título + hashtags** usa una plantilla configurable con `{title}` y `{hashtags}`. **IA · Omni** envía a Nemotron Omni el mismo proxy 720p/1 FPS usado para el recorte y, además, el título, el nombre del canal y la plataforma (`Kick`); con ese contexto genera la descripción y los hashtags. En modo IA, TTCA garantiza además el hashtag del canal y `#kick`. Si Omni no devuelve una descripción válida, TTCA vuelve a la plantilla manual.

Los fallos usan los reintentos internos configurados en `TIKTOK_UPLOAD_RETRIES`; con el valor predeterminado no hay espera adicional entre reintentos.

La interfaz permite elegir el destino publicación/borrador, seleccionar el archivo de cookies y abrir las páginas necesarias para preparar la cuenta.

## Layout especial para Just Chatting

TTCA lee la categoría del clip desde la API de Kick. Cuando la categoría es **Just Chatting**, el pipeline:

- omite completamente la detección de facecam con Kimi/DiffusionGemma;
- coloca el clip original a pantalla completa dentro del panel superior del Reel;
- conserva el divisor central cuando assets/divider.png existe;
- coloca debajo un gameplay aleatorio tomado de la carpeta assets/;
- repite en loop el gameplay de fondo si su duración es menor que la del clip.

Podés dejar los videos de fondo directamente dentro de assets/ o en subcarpetas. Se aceptan formatos comunes como .mp4, .mov, .mkv, .webm, .m4v, .avi, .ts y .m2ts.

El build_windows.bat copia toda la carpeta assets/ al paquete dist/TTCA/assets, por lo que esos videos quedan incluidos en el compilado.

## Seguridad

No subas `.env` ni las cookies de TikTok al repositorio. La API key de NVIDIA también queda fuera del código.


### Divisor
El archivo configurado en `DIVIDER_PATH` debe ser un PNG de exactamente **1080 × 160 píxeles**. Si el archivo no existe, TTCA no inserta un divisor de reemplazo: el gameplay ocupa automáticamente todo el espacio que habría quedado reservado para esos 160 píxeles.

### Papelera de reciclaje
En la configuración de destino de clips usados podés elegir la Papelera. Internamente TTCA usa `USED_DIR=__RECYCLE_BIN__` y `send2trash` para enviar el archivo al destino de reciclaje del sistema en lugar de crear otra carpeta.

### Detección de cámara y VTuber
La IA primero busca una **webcam/cámara real** del streamer. Solo cuando no encuentra una cámara válida intenta localizar un **avatar VTuber 2D o 3D**. También evita seleccionar chat, alertas, logos, donaciones u otros overlays como si fueran la cámara.

### Lanzamiento sin consola
`TTCA.pyw` está pensado para Windows y no debería mostrar una consola cuando está asociado a `pythonw.exe`. `TTCA.vbs` ofrece un segundo lanzador invisible cuando la asociación de `.pyw` no está configurada correctamente.