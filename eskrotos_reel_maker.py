#!/usr/bin/env python3
from __future__ import annotations
"""
Eskrotos Reel Maker
-------------------
Convierte clips horizontales de Kick a Reels verticales 1080x1920
con facecam arriba + divisor + gameplay centrado + subtítulos animados.

Requisitos:
    pip install opencv-python faster-whisper numpy pillow

Uso:
    python eskrotos_reel_maker.py
"""

import os
import sys

# Windows: HuggingFace no puede crear symlinks sin Developer Mode → fuerza copia
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

cv2 = None
np = None

import json
import time
import shutil
import tempfile
import subprocess
from datetime import datetime
from fractions import Fraction
from pathlib import Path

# ============================================================
# CONFIGURACIÓN - EDITÁ ESTO SI CAMBIÁS RUTAS
# ============================================================
CLIPS_DIR = Path(r"G:\Clips")
OUTPUT_DIR = Path(r"G:\Clips\Reels")          # se crea solo si no existe
USED_DIR = Path(r"G:\Clips\Usados")           # clips ya procesados se mueven acá
DIVIDER_PATH = Path(r"G:\assets\eskrotos_kick_divider_epic.png")
FONT_PATH = Path(r"G:\assets\TF2 build.ttf")

TARGET_W = 1080
TARGET_H = 1920
DIVIDER_H = 160

# Subtítulos
SUB_SIZE = 128
SUB_Y_OFFSET = 180          # px debajo del divisor
SUB_MAX_WORDS = 1           # 1 = palabra por palabra
SUB_COLOR = "&H00FFFFFF"    # blanco (ASS BGR)
SUB_BORDER = "&H00000000"   # negro
SUB_BORDER_WIDTH = 12       # borde más grueso (antes 4)

# Detección de facecam
FACE_MAX_RETRIES_KIMI = 5
FACE_FALLBACK_MODEL = "google/diffusiongemma-26b-a4b-it"
FACE_KIMI_MODEL = "moonshotai/kimi-k3"
FACE_KIMI_TIMEOUT = 130
FACE_FALLBACK_TIMEOUT = 60

# Whisper
WHISPER_MODEL = "large-v3"
WHISPER_DEVICE = "auto"     # "cuda", "cpu" o "auto"
WHISPER_COMPUTE = "default" # "float16", "int8", "default"
WHISPER_BACKEND = "auto"    # "auto", "faster-whisper" o "whisper.cpp"

# whisper.cpp opcional: permite usar una build con ROCm/Vulkan en AMD.
# Si no existe, el script mantiene faster-whisper con CUDA/CPU.
WHISPER_CPP_EXE = Path(r"G:\tools\whisper.cpp\whisper-cli.exe")
WHISPER_CPP_MODEL = Path(r"G:\tools\whisper.cpp\models\ggml-large-v3.bin")
WHISPER_CPP_THREADS = max(2, min(8, os.cpu_count() or 8))

# NVIDIA API
# ⚠️ Credencial de prueba de uso personal.
NVIDIA_API_KEY = "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"
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Auto-trim: Nemotron Omni CON VIDEO
NVIDIA_TRIM_OMNI = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
NVIDIA_TRIM_OMNI_TIMEOUT = 180

# Watcher de clips de Kick
KICK_CHANNEL = "eskrotos"
KICK_POLL_SECONDS = 5

# Deduplicación: evita bajar varias veces el mismo momento capturado por usuarios distintos.
DUPLICATE_WINDOW_SECONDS = 75
DUPLICATE_PHASH_DISTANCE = 10
DUPLICATE_DURATION_TOLERANCE = 20
DUPLICATE_TITLE_WINDOW_SECONDS = 30
DUPLICATE_THUMBNAIL_TIMEOUT = 8

# Reintentos de fallos temporales
FAILED_RETRY_SECONDS = 120

# Registro persistente. Mantiene el mismo archivo que usaba la versión anterior.
CLIP_REGISTRY_FILE = Path(r"G:\Clips\seen_clips.json")
REGISTRY_MAX_ENTRIES = 500

# Idempotencia
SKIP_EXISTING_OUTPUT = True
MIN_VALID_OUTPUT_BYTES = 10 * 1024

# ============================================================
# UTILIDADES
# ============================================================

def check_dependencies():
    global cv2, np

    missing = []

    try:
        import cv2 as _cv2
        cv2 = _cv2
    except ImportError:
        missing.append("opencv-python")

    try:
        import numpy as _np
        np = _np
    except ImportError:
        missing.append("numpy")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        missing.append("faster-whisper")

    try:
        from PIL import Image
    except ImportError:
        missing.append("pillow")

    try:
        import requests
    except ImportError:
        missing.append("requests")

    if missing:
        print("\n❌ Faltan dependencias:")
        for m in missing:
            print(f"   pip install {m}")
        print("\nInstalá y volvé a correr el script.")
        sys.exit(1)

    # FFmpeg
    if shutil.which("ffmpeg") is None:
        print("❌ No se encontró FFmpeg en el PATH.")
        sys.exit(1)

    print("✅ Dependencias OK")


def ensure_dirs():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not DIVIDER_PATH.exists():
        print(f"❌ No se encontró el divisor: {DIVIDER_PATH}")
        sys.exit(1)
    if not FONT_PATH.exists():
        print(f"❌ No se encontró la fuente: {FONT_PATH}")
        sys.exit(1)


def get_video_info(path: Path):
    """Obtiene width, height, fps, duration, nb_frames con ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe falló: {result.stderr}")

    data = json.loads(result.stdout)
    video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")

    width = int(video_stream["width"])
    height = int(video_stream["height"])
    fps = float(Fraction(video_stream["r_frame_rate"]))
    duration = float(data["format"]["duration"])
    return width, height, fps, duration


def extract_frame(video_path: Path, time_sec: float = 1.0) -> np.ndarray:
    """Extrae un frame como BGR numpy array."""
    cap = cv2.VideoCapture(str(video_path))
    cap.set(cv2.CAP_PROP_POS_MSEC, time_sec * 1000)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise RuntimeError(f"No se pudo extraer frame de {video_path}")
    return frame


# ============================================================
# DETECCIÓN DE FACECAM
# ============================================================

def is_valid_facecam_box(x, y, w, h, orig_w, orig_h) -> bool:
    """
    Valida que la caja de facecam tenga un tamaño y proporción razonables
    antes de usarla para construir el Reel.
    """
    if w < 120 or h < 120:
        return False
    if x < 0 or y < 0 or x + w > orig_w + 2 or y + h > orig_h + 2:
        return False

    aspect = w / max(h, 1)
    if aspect < 0.55 or aspect > 2.2:
        return False

    area_ratio = (w * h) / max(orig_w * orig_h, 1)
    if area_ratio < 0.01 or area_ratio > 0.45:
        return False

    return True


def detect_facecam_llm(video_path: Path, orig_w: int, orig_h: int, time_sec: float = 2.0):
    """
    Localiza el panel de la facecam con visión.
    Intenta Kimi hasta 5 veces; si las 5 fallan, cae a DiffusionGemma.
    """
    import base64
    import re
    import requests
    from io import BytesIO
    from PIL import Image

    frame = extract_frame(video_path, time_sec)

    max_side = 640
    scale = min(1.0, max_side / max(orig_w, orig_h))
    new_w = max(1, int(orig_w * scale))
    new_h = max(1, int(orig_h * scale))
    frame_small = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    rgb = cv2.cvtColor(frame_small, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    buf = BytesIO()
    pil.save(buf, format="JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    prompt = (
        f"Kick stream screenshot {new_w}x{new_h}px.\n"
        "Find the streamer's facecam/webcam overlay panel (picture-in-picture).\n"
        "It is a rectangular box containing the streamer's face and its full visual frame.\n"
        "IMPORTANT: Return a TIGHT bounding box that matches ONLY the panel itself.\n"
        "Do NOT include gameplay background outside the panel border.\n"
        "The box edges should align with the outer border of the facecam frame.\n"
        'Output ONLY JSON: {"x":N,"y":N,"width":N,"height":N}\n'
        "No other text."
    )

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    models = [
        (FACE_KIMI_MODEL, "Kimi", FACE_KIMI_TIMEOUT, FACE_MAX_RETRIES_KIMI),
        (FACE_FALLBACK_MODEL, "DiffusionGemma", FACE_FALLBACK_TIMEOUT, 1),
    ]

    for model_id, name, timeout_s, retries in models:
        for attempt in range(1, retries + 1):
            print(f"  🤖 Facecam con {name} — intento {attempt}/{retries} (timeout {timeout_s}s)...")

            payload = {
                "model": model_id,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
                "max_tokens": 300,
                "temperature": 0.0,
                "stream": False,
            }

            try:
                r = requests.post(
                    NVIDIA_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout_s,
                )

                if r.status_code in (429, 503):
                    wait = min(30, 5 * attempt)
                    print(f"     ⚠️  {name} HTTP {r.status_code}. Reintento en {wait}s...")
                    time.sleep(wait)
                    continue

                r.raise_for_status()
                data = r.json()
                msg = data["choices"][0]["message"]
                response_text = (msg.get("content") or "") + "\n" + (
                    msg.get("reasoning_content") or ""
                )

                match = re.search(
                    r'\{[^{}]*"x"\s*:\s*-?\d+[^{}]*\}',
                    response_text,
                )
                if not match:
                    print(f"     ⚠️  {name} no devolvió JSON válido.")
                    continue

                box = json.loads(match.group(0))
                x = int(box["x"])
                y = int(box["y"])
                w = int(box["width"])
                h = int(box["height"])

                if w < 30 or h < 30 or x < 0 or y < 0:
                    print(f"     ⚠️  {name} devolvió una caja inválida.")
                    continue

                inv = 1.0 / scale if scale > 0 else 1.0
                x = int(x * inv)
                y = int(y * inv)
                w = int(w * inv)
                h = int(h * inv)

                x = max(0, min(x, orig_w - 2))
                y = max(0, min(y, orig_h - 2))
                w = max(2, min(w, orig_w - x))
                h = max(2, min(h, orig_h - y))

                aspect = w / max(h, 1)
                area_ratio = (w * h) / max(orig_w * orig_h, 1)
                if w < 120 or h < 120:
                    print(f"     ⚠️  {name} box muy chica ({w}x{h}).")
                    continue
                if aspect < 0.55 or aspect > 2.2:
                    print(f"     ⚠️  {name} aspect raro ({aspect:.2f}).")
                    continue
                if area_ratio > 0.45 or area_ratio < 0.01:
                    print(f"     ⚠️  {name} área rara ({area_ratio:.3f}).")
                    continue

                pad_x = max(1, int(w * 0.01))
                pad_y = max(1, int(h * 0.01))
                x = max(0, x - pad_x)
                y = max(0, y - pad_y)
                w = min(orig_w - x, w + 2 * pad_x)
                h = min(orig_h - y, h + 2 * pad_y)

                print(f"  ✅ {name} detectó facecam: x={x} y={y} w={w} h={h}")
                return (x, y, w, h)

            except requests.exceptions.Timeout:
                print(f"     ⚠️  Timeout de {name}.")
            except Exception as e:
                print(f"     ⚠️  Error con {name}: {e}")

        if model_id == FACE_KIMI_MODEL:
            print("  ⚠️  Kimi falló 5 veces. Pasando a DiffusionGemma...")

    print("  ❌ Todos los modelos de visión fallaron para facecam.")
    return None


def _make_trim_proxy(video_path: Path, duration: float) -> tuple[Path, float, float] | None:
    """
    Crea un proxy de 720p a 1 FPS para Nemotron Omni.
    Si el clip supera 120 s, usa los ÚLTIMOS 120 s del original.
    Retorna (ruta, offset_original, duración_proxy).
    """
    max_sec = 120.0
    proxy_duration = min(max_sec, duration)
    source_offset = max(0.0, duration - max_sec)

    out = Path(tempfile.gettempdir()) / f"_eskrotos_trim_proxy_{os.getpid()}.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{source_offset:.3f}",
        "-t", f"{proxy_duration:.3f}",
        "-i", str(video_path),
        "-vf", "fps=1,scale=-2:720",
        "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        str(out),
    ]

    try:
        subprocess.run(cmd, check=True, timeout=120)
        if out.exists() and out.stat().st_size > 1000:
            return out, source_offset, proxy_duration
    except Exception as e:
        print(f"     ⚠️  No se pudo crear proxy para Omni: {e}")
    return None


def suggest_trim_omni_video(video_path: Path, duration: float, max_retries: int = 5) -> tuple[float, float] | None:
    """
    Auto-trim con Nemotron Omni viendo un proxy de 720p a 1 FPS.
    Para clips >120 s, el proxy corresponde a los últimos 120 s del original.
    """
    import base64
    import re
    import requests

    print("  🤖 Auto-trim con Nemotron Omni (video)...")
    proxy_data = _make_trim_proxy(video_path, duration)
    if proxy_data is None:
        return None

    proxy, source_offset, proxy_duration = proxy_data

    try:
        b64 = base64.b64encode(proxy.read_bytes()).decode("utf-8")
        data_url = f"data:video/mp4;base64,{b64}"

        prompt = f"""Sos editor de Reels virales del streamer argentino "Eskrotos" (Kick).
Estilo: humor absurdo, reacciones exageradas, sarcasmo, fallos épicos, punchlines.

Estás VIENDO un proxy de 720p a 1 FPS.
El proxy representa desde {source_offset:.1f}s hasta {source_offset + proxy_duration:.1f}s del clip original.
Duración real del original: {duration:.1f}s.

Elegí el tramo MÁS viral para un Reel de TikTok.

REGLAS DE DURACIÓN:
- Largo IDEAL: 15 a 20 segundos.
- Mínimo: 12 segundos si el material lo permite.
- Máximo: el clip completo si hace falta.
- NUNCA cortes solo el clímax. Incluí contexto:
  * 3–6s ANTES del momento clave
  * el momento principal
  * 2–5s DESPUÉS
- Si el mejor momento necesita más de 20s, devolvé el tramo largo.
- Los tiempos que devuelvas deben ser RELATIVOS AL PROXY, no al original.

Respondé SOLO este JSON:
{{"start": 10.0, "end": 28.0, "reason": "motivo corto"}}
"""

        headers = {
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "model": NVIDIA_TRIM_OMNI,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 300,
            "temperature": 0.2,
            "stream": False,
        }

        for attempt in range(1, max_retries + 1):
            try:
                print(f"     Intento {attempt}/{max_retries}...")
                r = requests.post(
                    NVIDIA_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=NVIDIA_TRIM_OMNI_TIMEOUT,
                )

                if r.status_code in (429, 503):
                    wait = min(60, 10 * attempt)
                    print(f"     ⚠️  Omni HTTP {r.status_code}. Espero {wait}s...")
                    time.sleep(wait)
                    continue

                if r.status_code != 200:
                    wait = min(30, 5 * attempt)
                    print(f"     ⚠️  Omni HTTP {r.status_code}. Espero {wait}s...")
                    time.sleep(wait)
                    continue

                data = r.json()
                msg = data["choices"][0]["message"]
                response_text = (msg.get("content") or "") + "\n" + (
                    msg.get("reasoning_content") or msg.get("reasoning") or ""
                )

                match = re.search(
                    r'\{[^{}]*"start"\s*:\s*-?[\d.]+[^{}]*\}',
                    response_text,
                    re.DOTALL,
                )
                if not match:
                    print("     ⚠️  Omni sin JSON válido. Reintento...")
                    time.sleep(4)
                    continue

                obj = json.loads(match.group(0))
                proxy_start = max(0.0, min(float(obj["start"]), proxy_duration - 1.0))
                proxy_end = max(
                    proxy_start + 3.0,
                    min(float(obj["end"]), proxy_duration),
                )

                start = max(0.0, min(source_offset + proxy_start, duration - 1.0))
                end = max(start + 3.0, min(source_offset + proxy_end, duration))

                min_len = min(12.0, duration)
                if end - start < min_len and duration >= min_len:
                    extra = min_len - (end - start)
                    back = min(start, extra * 0.6)
                    start -= back
                    end = min(duration, end + (extra - back))
                    if end - start < min_len:
                        start = max(0.0, end - min_len)
                    if end - start < min_len:
                        end = min(duration, start + min_len)

                reason = obj.get("reason", "")
                print(f"  ✅ Omni (video): {start:.1f}s → {end:.1f}s ({end-start:.1f}s)")
                if reason:
                    print(f"     Motivo: {reason}")
                return (start, end)

            except requests.exceptions.Timeout:
                wait = min(30, 5 * attempt)
                print(f"     ⚠️  Timeout Omni. Espero {wait}s...")
                time.sleep(wait)
            except Exception as e:
                wait = min(20, 4 * attempt)
                print(f"     ⚠️  Error Omni: {e}. Espero {wait}s...")
                time.sleep(wait)

        print("  ❌ Omni no respondió tras varios reintentos.")
        return None

    finally:
        try:
            proxy.unlink(missing_ok=True)
        except Exception:
            pass


def suggest_trim_llm(duration: float, video_path: Path | None = None) -> tuple[float, float] | None:
    """Punto de entrada al auto-trim; actualmente usa únicamente Nemotron Omni."""
    if video_path is not None and video_path.exists():
        return suggest_trim_omni_video(video_path, duration)

    print("  ⚠️  Sin video para Omni; no se sugiere trim automático.")
    return None


def manual_select_facecam(video_path: Path, initial_time: float = 1.5):
    """
    Selección manual de la facecam con mouse.
    - Arrastrá para dibujar el rectángulo
    - ENTER = aceptar
    - ESC = cancelar
    """
    frame = extract_frame(video_path, initial_time)
    clone = frame.copy()
    selecting = False
    start_pt = None
    end_pt = None
    final_rect = None

    def mouse_cb(event, x, y, flags, param):
        nonlocal selecting, start_pt, end_pt, final_rect, clone

        if event == cv2.EVENT_LBUTTONDOWN:
            selecting = True
            start_pt = (x, y)
            end_pt = (x, y)

        elif event == cv2.EVENT_MOUSEMOVE and selecting:
            end_pt = (x, y)
            temp = clone.copy()
            cv2.rectangle(temp, start_pt, end_pt, (0, 255, 0), 2)
            cv2.putText(temp, "Arrastra y suelta. ENTER=OK  ESC=Cancelar",
                        (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
            cv2.imshow("FACECAM - Seleccioná el recuadro completo", temp)

        elif event == cv2.EVENT_LBUTTONUP:
            selecting = False
            end_pt = (x, y)
            x1, y1 = start_pt
            x2, y2 = end_pt
            rx = min(x1, x2)
            ry = min(y1, y2)
            rw = abs(x2 - x1)
            rh = abs(y2 - y1)
            if rw > 30 and rh > 30:
                final_rect = (rx, ry, rw, rh)
                temp = clone.copy()
                cv2.rectangle(temp, (rx, ry), (rx + rw, ry + rh), (0, 255, 0), 2)
                cv2.putText(temp, f"{rw}x{rh}  -  ENTER=aceptar  ESC=cancelar",
                            (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                cv2.imshow("FACECAM - Seleccioná el recuadro completo", temp)

    win = "FACECAM - Seleccioná el recuadro completo"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, 1280, 720)
    cv2.setMouseCallback(win, mouse_cb)
    display = clone.copy()
    cv2.putText(display, "Arrastra el mouse sobre la FACECAM completa (borde incluido)",
                (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    cv2.imshow(win, display)

    print("\n→ Arrastrá el mouse para seleccionar TODA la facecam (incluyendo bordes y anime).")
    print("  ENTER = aceptar | ESC = cancelar")

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == 13:  # Enter
            break
        elif key == 27:  # ESC
            final_rect = None
            break

    cv2.destroyAllWindows()
    return final_rect


def interactive_trim(video_path: Path, duration: float, words: list = None):
    """
    Player interactivo para elegir In / Out del clip.
    Controles visibles + timeline + subtítulos sencillos (si se pasan words).
    """
    if words is None:
        words = []

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Escala para que entre en pantalla
    max_w, max_h = 1280, 720
    scale = min(max_w / width, max_h / height, 1.0)
    disp_w, disp_h = int(width * scale), int(height * scale)

    current_frame = 0
    playing = False
    in_point = 0.0
    out_point = duration

    # Reproducción ~1.7x más rápida (más ágil)
    play_speed = 1.7
    frame_delay = max(1, int(1000 / (fps * play_speed)))

    win = "TRIM - Espacio=Play  I=In  O=Out  ENTER=OK  ESC=Todo"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, disp_w, disp_h + 80)

    def get_current_subtitle(t: float) -> str:
        """Devuelve hasta 2 palabras activas en el tiempo t."""
        active = [w for w in words if w["start"] <= t <= w["end"] + 0.15]
        if not active:
            # Buscar la más cercana por si hay un pequeño hueco
            nearby = [w for w in words if abs(w["start"] - t) < 0.4 or abs(w["end"] - t) < 0.4]
            active = nearby[:2]
        if not active:
            return ""
        # Tomamos máximo 2 palabras consecutivas
        active = sorted(active, key=lambda x: x["start"])[:2]
        return " ".join(w["word"] for w in active)

    def seek(frame_idx):
        nonlocal current_frame
        current_frame = max(0, min(total_frames - 1, int(frame_idx)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)

    def mouse_cb(event, x, y, flags, param):
        nonlocal current_frame
        if event == cv2.EVENT_LBUTTONDOWN and y > disp_h:
            ratio = x / max(disp_w, 1)
            seek(ratio * total_frames)

    cv2.setMouseCallback(win, mouse_cb)

    # Texto de controles que se dibuja en el video
    help_lines = [
        "ESPACIO = Play/Pause",
        "A / D   = -1s / +1s",
        "W / X   = -0.2s / +0.2s",
        "I       = marcar IN",
        "O       = marcar OUT",
        "Click barra = seek",
        "ENTER   = confirmar",
        "ESC     = todo el clip",
    ]

    print("\n→ Controles del TRIM (también se ven en el video):")
    for line in help_lines:
        print(f"  {line}")

    while True:
        ret, frame = cap.read()
        if not ret:
            # Llegó al final → pausar y volver al in
            playing = False
            seek(in_point * fps)
            continue

        # Redimensionar
        display = cv2.resize(frame, (disp_w, disp_h))

        # === Controles siempre visibles (arriba izquierda) ===
        overlay = display.copy()
        # Fondo semi-transparente para legibilidad
        cv2.rectangle(overlay, (8, 8), (310, 8 + 22 * len(help_lines) + 10), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.55, display, 0.45, 0, display)

        for i, line in enumerate(help_lines):
            y = 28 + i * 22
            color = (0, 255, 255) if i < 3 else (200, 255, 200)
            cv2.putText(display, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)

        # Estado Play/Pause
        status = "PLAY  x1.7" if playing else "PAUSE"
        status_color = (0, 255, 0) if playing else (0, 165, 255)
        cv2.putText(display, status, (disp_w - 140, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2, cv2.LINE_AA)

        # Subtítulo actual (centro inferior del video)
        cur_t = current_frame / fps
        sub_text = get_current_subtitle(cur_t)
        if sub_text:
            # Fondo negro semitransparente + texto blanco con borde
            (tw, th), _ = cv2.getTextSize(sub_text, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 2)
            tx = (disp_w - tw) // 2
            ty = disp_h - 40
            cv2.rectangle(display, (tx - 12, ty - th - 12), (tx + tw + 12, ty + 12), (0, 0, 0), -1)
            # Borde negro grueso simulado
            for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2), (-2, 0), (2, 0), (0, -2), (0, 2)]:
                cv2.putText(display, sub_text, (tx + dx, ty + dy), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(display, sub_text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)

        # Timeline bar (80px)
        bar = np.zeros((80, disp_w, 3), dtype=np.uint8)
        bar[:] = (40, 40, 40)

        # Rango seleccionado
        in_x = int((in_point / duration) * disp_w)
        out_x = int((out_point / duration) * disp_w)
        cv2.rectangle(bar, (in_x, 20), (out_x, 60), (0, 180, 0), -1)

        # Cursor actual
        cur_x = int((current_frame / max(total_frames - 1, 1)) * disp_w)
        cv2.line(bar, (cur_x, 5), (cur_x, 75), (0, 255, 255), 2)

        # Textos de la barra
        cur_t = current_frame / fps
        cv2.putText(bar, f"{cur_t:6.2f}s", (10, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.putText(bar, f"IN {in_point:.2f}s", (in_x + 4, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(bar, f"OUT {out_point:.2f}s", (max(out_x - 90, 0), 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        dur_sel = out_point - in_point
        cv2.putText(bar, f"Duracion: {dur_sel:.2f}s", (disp_w - 160, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)

        combined = np.vstack([display, bar])
        cv2.imshow(win, combined)

        # Timing de reproducción (más rápido cuando está en play)
        delay = frame_delay if playing else 30
        key = cv2.waitKey(delay) & 0xFF

        if not playing:
            # Si está pausado, volvemos a mostrar el mismo frame
            seek(current_frame)

        if key == ord(' '):
            playing = not playing
        elif key in (81, 2, ord('a'), ord('A')):          # Left / A
            seek(current_frame - fps)
            playing = False
        elif key in (83, 3, ord('d'), ord('D')):          # Right / D
            seek(current_frame + fps)
            playing = False
        elif key in (82, 0, ord('w'), ord('W')):          # Up / W (-0.2s)
            seek(current_frame - fps * 0.2)
            playing = False
        elif key in (84, 1, ord('x'), ord('X')):          # Down / X (+0.2s)
            seek(current_frame + fps * 0.2)
            playing = False
        elif key in (ord('i'), ord('I')):
            in_point = current_frame / fps
            if in_point >= out_point:
                out_point = min(duration, in_point + 1.0)
            print(f"  IN marcado: {in_point:.2f}s")
        elif key in (ord('o'), ord('O')):
            out_point = current_frame / fps
            if out_point <= in_point:
                in_point = max(0.0, out_point - 1.0)
            print(f"  OUT marcado: {out_point:.2f}s")
        elif key == 13:  # Enter
            break
        elif key == 27:  # ESC → todo el clip
            in_point, out_point = 0.0, duration
            break

        # Actualizar current_frame real
        current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))

    cap.release()
    cv2.destroyAllWindows()

    # Seguridad
    in_point = max(0.0, min(in_point, duration - 0.1))
    out_point = max(in_point + 0.1, min(out_point, duration))
    print(f"  → Trim final: {in_point:.2f}s → {out_point:.2f}s  ({out_point - in_point:.2f}s)")
    return in_point, out_point


def preview_layout(video_path: Path, facecam_box, orig_w, orig_h, cam_h: int = None):
    """
    Muestra una preview estática de cómo va a quedar el layout vertical.
    """
    frame = extract_frame(video_path, 2.0)
    fx, fy, fw, fh = facecam_box

    # Facecam escalada
    cam = frame[fy:fy+fh, fx:fx+fw]
    if cam_h is None:
        cam_h = int(TARGET_W * fh / fw)
        cam_h = max(280, min(cam_h, 720))
    cam_resized = cv2.resize(cam, (TARGET_W, cam_h), interpolation=cv2.INTER_AREA)

    # Divider
    divider = cv2.imread(str(DIVIDER_PATH))
    if divider is None:
        divider = np.zeros((DIVIDER_H, TARGET_W, 3), dtype=np.uint8)
        divider[:] = (0, 180, 0)  # verde de emergencia
    else:
        divider = cv2.resize(divider, (TARGET_W, DIVIDER_H))

    # Gameplay centrado
    bottom_h = TARGET_H - cam_h - DIVIDER_H
    # Crop central del frame original
    aspect_bottom = TARGET_W / bottom_h
    if orig_w / orig_h > aspect_bottom:
        # original más ancho → crop horizontal
        new_w = int(orig_h * aspect_bottom)
        x0 = (orig_w - new_w) // 2
        game = frame[:, x0:x0+new_w]
    else:
        new_h = int(orig_w / aspect_bottom)
        y0 = (orig_h - new_h) // 2
        game = frame[y0:y0+new_h, :]

    game_resized = cv2.resize(game, (TARGET_W, bottom_h), interpolation=cv2.INTER_AREA)

    # Stack
    preview = np.vstack([cam_resized, divider, game_resized])

    # Redimensionar para que entre en pantalla
    scale = min(1.0, 900 / preview.shape[0])
    if scale < 1.0:
        preview = cv2.resize(preview, None, fx=scale, fy=scale)

    cv2.imshow("PREVIEW del Reel (cualquier tecla para continuar)", preview)
    print("\n→ Preview generada. Presioná cualquier tecla en la ventana para continuar...")
    cv2.waitKey(0)
    cv2.destroyAllWindows()


# ============================================================
# SUBTÍTULOS (Whisper + ASS)
# ============================================================

# Caché global del modelo Whisper (no re-descargar en cada clip)
_WHISPER_MODEL_INSTANCE = None
_WHISPER_MODEL_NAME = None


def _load_whisper_model():
    """Carga faster-whisper una sola vez."""
    global _WHISPER_MODEL_INSTANCE, _WHISPER_MODEL_NAME
    from faster_whisper import WhisperModel

    if _WHISPER_MODEL_INSTANCE is not None:
        return _WHISPER_MODEL_INSTANCE, _WHISPER_MODEL_NAME

    device = WHISPER_DEVICE
    compute = WHISPER_COMPUTE

    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"

    if compute == "default":
        compute = "float16" if device == "cuda" else "int8"

    candidates = [WHISPER_MODEL]
    if WHISPER_MODEL != "medium":
        candidates.append("medium")
    if "small" not in candidates:
        candidates.append("small")

    last_err = None
    for name in candidates:
        try:
            print(f"  🎙️  Cargando Whisper '{name}' (device={device}, compute={compute})...")
            model = WhisperModel(name, device=device, compute_type=compute)
            _WHISPER_MODEL_INSTANCE = model
            _WHISPER_MODEL_NAME = name
            print(f"  ✅ Whisper '{name}' listo")
            return model, name
        except Exception as e:
            print(f"  ⚠️  Error cargando '{name}': {e}")
            last_err = e

    raise RuntimeError(f"No se pudo cargar ningún modelo Whisper. Último error: {last_err}")


def _extract_audio_for_whisper(video_path: Path) -> Path:
    """Extrae audio mono 16 kHz normalizado para Whisper."""
    out = Path(tempfile.gettempdir()) / f"_eskrotos_whisper_audio_{os.getpid()}.wav"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-af", "loudnorm=I=-16:LRA=11:TP=-1.5,highpass=f=80,lowpass=f=8000",
        str(out),
    ]
    subprocess.run(cmd, check=True, timeout=120)
    return out


def _clean_transcribed_words(words: list[dict]) -> list[dict]:
    """Limpia palabras de alucinaciones obvias y duraciones absurdas."""
    hallucination_words = {
        "videos", "video", "subscribe", "suscribete", "suscríbete",
        "thanks", "thank", "you", "www", "http", "com", "music",
        "subtitulos", "subtítulos", "subtitles", "copyright",
    }

    cleaned = []
    for w in words:
        text_value = w["word"].strip()
        if not text_value:
            continue
        if text_value.lower().strip(".,!?¡¿\"'") in hallucination_words:
            continue

        probability = w.get("probability")
        if probability is not None and probability < 0.35:
            continue

        start = float(w["start"])
        end = float(w["end"])
        if end <= start:
            end = start + 0.12
        elif end - start > 2.5:
            end = start + 0.45

        cleaned.append({
            "word": text_value,
            "start": start,
            "end": end,
            "probability": probability,
        })

    return cleaned


def _parse_timecode(value: str) -> float:
    value = value.replace(",", ".")
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_whisper_cpp_json(json_path: Path) -> list[dict]:
    """
    Convierte el JSON full de whisper.cpp en palabras con timestamps.
    Agrupa tokens BPE en palabras según los espacios iniciales.
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    raw_words = []

    for segment in data.get("transcription", []):
        current = []
        current_start = None
        current_end = None
        current_probs = []

        for token in segment.get("tokens", []):
            raw = str(token.get("text") or "")
            if not raw or raw.startswith("[_"):
                continue

            ts = token.get("timestamps") or {}
            try:
                start = _parse_timecode(str(ts["from"]))
                end = _parse_timecode(str(ts["to"]))
            except Exception:
                continue

            probability = token.get("probability", token.get("p"))
            try:
                probability = float(probability) if probability is not None else None
            except (TypeError, ValueError):
                probability = None

            if raw[:1].isspace() and current:
                raw_words.append({
                    "word": "".join(current),
                    "start": current_start,
                    "end": current_end,
                    "probability": min(current_probs) if current_probs else None,
                })
                current = []
                current_start = None
                current_end = None
                current_probs = []

            if current_start is None:
                current_start = start
            current.append(raw.strip() if not current else raw)
            current_end = end
            if probability is not None:
                current_probs.append(probability)

        if current:
            raw_words.append({
                "word": "".join(current),
                "start": current_start,
                "end": current_end,
                "probability": min(current_probs) if current_probs else None,
            })

    return _clean_transcribed_words(raw_words)


def _transcribe_with_whisper_cpp(audio_path: Path) -> tuple[list[dict], str]:
    """Usa whisper.cpp cuando está configurado y sus archivos existen."""
    if not WHISPER_CPP_EXE.exists():
        raise FileNotFoundError(f"No existe whisper-cli: {WHISPER_CPP_EXE}")
    if not WHISPER_CPP_MODEL.exists():
        raise FileNotFoundError(f"No existe el modelo whisper.cpp: {WHISPER_CPP_MODEL}")

    with tempfile.TemporaryDirectory(prefix="eskrotos_whisper_") as temp_dir:
        output_prefix = Path(temp_dir) / "result"

        cmd = [
            str(WHISPER_CPP_EXE),
            "-m", str(WHISPER_CPP_MODEL),
            "-f", str(audio_path),
            "-l", "es",
            "-ojf",
            "-np",
            "-of", str(output_prefix),
            "-bs", "5",
            "-bo", "5",
            "-tp", "0.0",
            "-t", str(WHISPER_CPP_THREADS),
            "--prompt",
            (
                "Streamer argentino en Kick jugando. Habla español rioplatense. "
                "Ejemplos: mirá este, la patada que se comió, boludo, qué carajo, "
                "no puede ser, se la comió, re zarpado, pará pará."
            ),
        ]

        subprocess.run(
            cmd,
            check=True,
            timeout=180,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        json_path = output_prefix.with_suffix(".json")
        if not json_path.exists():
            raise RuntimeError(f"whisper.cpp no generó JSON: {json_path}")

        words = _parse_whisper_cpp_json(json_path)
        return words, "whisper.cpp"


def _transcribe_with_faster_whisper(audio_path: Path) -> tuple[list[dict], str]:
    model, model_name = _load_whisper_model()
    print(f"  🎙️  Transcribiendo con faster-whisper ({model_name})...")

    segments, info = model.transcribe(
        str(audio_path),
        language="es",
        task="transcribe",
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=400,
            speech_pad_ms=200,
            threshold=0.45,
        ),
        beam_size=5,
        best_of=5,
        temperature=[0.0, 0.2, 0.4],
        condition_on_previous_text=False,
        no_speech_threshold=0.55,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        initial_prompt=(
            "Streamer argentino en Kick jugando. Habla español rioplatense. "
            "Ejemplos de frases: mirá este, la patada que se comió, boludo, "
            "qué carajo, no puede ser, se la comió, re zarpado, pará pará."
        ),
    )

    words = []
    for seg in segments:
        if seg.words:
            for word in seg.words:
                words.append({
                    "word": word.word.strip(),
                    "start": float(word.start),
                    "end": float(word.end),
                    "probability": getattr(word, "probability", None),
                })

    return _clean_transcribed_words(words), model_name


def transcribe_video(video_path: Path):
    """Transcribe un clip usando whisper.cpp opcional o faster-whisper."""
    audio_path = None

    try:
        audio_path = _extract_audio_for_whisper(video_path)
        print("     Audio pre-procesado (mono 16k + loudnorm)")
    except Exception as e:
        print(f"     ⚠️  No se pudo pre-procesar audio ({e})")
        audio_path = video_path

    try:
        use_cpp = (
            WHISPER_BACKEND == "whisper.cpp"
            or (
                WHISPER_BACKEND == "auto"
                and WHISPER_CPP_EXE.exists()
                and WHISPER_CPP_MODEL.exists()
            )
        )

        if use_cpp:
            try:
                print("  🚀 Whisper backend: whisper.cpp")
                words, model_name = _transcribe_with_whisper_cpp(audio_path)
            except Exception as e:
                print(f"  ⚠️  whisper.cpp falló: {e}. Fallback a faster-whisper...")
                words, model_name = _transcribe_with_faster_whisper(audio_path)
        else:
            print("  🚀 Whisper backend: faster-whisper")
            words, model_name = _transcribe_with_faster_whisper(audio_path)

        preview = " ".join(w["word"] for w in words[:25])
        if preview:
            print(f"     Preview: {preview}{'...' if len(words) > 25 else ''}")
        print(f"  ✅ {len(words)} palabras detectadas ({model_name})")
        return words

    finally:
        if audio_path is not None and audio_path != video_path:
            try:
                audio_path.unlink(missing_ok=True)
            except Exception:
                pass


# ============================================================
# SUBTÍTULOS (ASS)
# ============================================================

def create_ass_file(words, ass_path: Path, video_w: int, video_h: int, text_y: int):
    """
    Genera subtítulos ASS palabra por palabra con posición fija.
    """
    header = f"""[Script Info]
Title: Eskrotos Reel Subs
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {video_w}
PlayResY: {video_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,TF2 Build,{SUB_SIZE},{SUB_COLOR},&H000000FF,{SUB_BORDER},&H80000000,-1,0,0,0,100,100,0,0,1,{SUB_BORDER_WIDTH},0,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def sec_to_ass(t):
        t = max(0.0, float(t))
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        sec = t % 60
        return f"{h}:{m:02d}:{sec:05.2f}"

    center_x = video_w // 2
    pos = r"{\pos(" + str(center_x) + "," + str(text_y) + r")}"

    events = []
    i = 0
    while i < len(words):
        group = words[i:i + SUB_MAX_WORDS]
        start = float(group[0]["start"])
        end = float(group[-1]["end"])

        if end - start < 0.08:
            end = start + 0.08

        if i + SUB_MAX_WORDS < len(words):
            next_start = float(words[i + SUB_MAX_WORDS]["start"])
            if end > next_start - 0.02:
                end = max(start + 0.06, next_start - 0.02)

        text = " ".join(w["word"] for w in group)
        events.append(
            f"Dialogue: 0,{sec_to_ass(start)},{sec_to_ass(end)},Default,,0,0,0,,"
            f"{pos}{text}\n"
        )
        i += SUB_MAX_WORDS

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write(header)
        f.writelines(events)

    return ass_path


# ============================================================
# PROCESAMIENTO CON FFMPEG# ============================================================
# PROCESAMIENTO CON FFMPEG
# ============================================================

def _escape_ffmpeg_path(p: Path) -> str:
    """
    Escapa una ruta de Windows para el filtro ass de FFmpeg.
    Formato que suele funcionar: C\\:/folder/file.ass
    """
    s = str(p.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        s = s[0] + "\\:" + s[2:]
    return s


def prepare_fonts_dir() -> Path:
    """
    Carpeta de fuentes 100% limpia.
    Borra todo lo anterior para que libass no cargue 'TF2 build.ttf' (con espacio).
    """
    script_dir = Path(__file__).resolve().parent
    clean_fonts_dir = script_dir / "_fonts_temp"
    if clean_fonts_dir.exists():
        shutil.rmtree(clean_fonts_dir, ignore_errors=True)
    clean_fonts_dir.mkdir(parents=True, exist_ok=True)
    clean_font_path = clean_fonts_dir / "TF2Build.ttf"
    shutil.copy2(FONT_PATH, clean_font_path)
    return clean_fonts_dir


def build_ffmpeg_cmd(
    video_path: Path,
    output_path: Path,
    facecam_box,
    orig_w: int,
    orig_h: int,
    ass_path: Path,
    cam_h: int,
    start_sec: float = 0.0,
    end_sec: float = None,
):
    """
    Construye el comando FFmpeg completo.
    Layout:
        [facecam escalada]
        [divisor 1080x160]
        [gameplay centrado]
    + burn de subtítulos ASS
    + trim opcional (start_sec / end_sec)
    """
    fx, fy, fw, fh = facecam_box

    # Validar / clamp del crop de facecam
    fx = max(0, min(int(fx), orig_w - 2))
    fy = max(0, min(int(fy), orig_h - 2))
    fw = max(2, min(int(fw), orig_w - fx))
    fh = max(2, min(int(fh), orig_h - fy))
    # Dimensiones pares (evita crashes raros de algunos builds)
    fw -= fw % 2
    fh -= fh % 2
    fx -= fx % 2
    fy -= fy % 2

    cam_h = max(200, min(int(cam_h), 800))
    cam_h -= cam_h % 2
    bottom_h = TARGET_H - cam_h - DIVIDER_H
    if bottom_h < 200:
        cam_h = TARGET_H - DIVIDER_H - 400
        cam_h -= cam_h % 2
        bottom_h = TARGET_H - cam_h - DIVIDER_H
    bottom_h -= bottom_h % 2

    # Crop del gameplay centrado
    aspect_bottom = TARGET_W / max(bottom_h, 1)
    if orig_w / max(orig_h, 1) > aspect_bottom:
        new_w = max(2, int(orig_h * aspect_bottom))
        new_w = min(new_w, orig_w)
        new_w -= new_w % 2
        x0 = (orig_w - new_w) // 2
        x0 -= x0 % 2
        game_crop = f"crop={new_w}:{orig_h}:{x0}:0"
    else:
        new_h = max(2, int(orig_w / aspect_bottom))
        new_h = min(new_h, orig_h)
        new_h -= new_h % 2
        y0 = (orig_h - new_h) // 2
        y0 -= y0 % 2
        game_crop = f"crop={orig_w}:{new_h}:0:{y0}"

    # Fuentes limpias (solo TF2Build.ttf, sin espacios).
    # Rutas RELATIVAS: FFmpeg corre con cwd=script_dir → evita el infierno de G\:/
    prepare_fonts_dir()
    ass_name = ass_path.name          # "_temp_subs.ass"
    fonts_name = "_fonts_temp"

    filter_complex = (
        f"[0:v]crop={fw}:{fh}:{fx}:{fy},scale={TARGET_W}:{cam_h}:flags=lanczos,setsar=1[cam];"
        f"[0:v]{game_crop},scale={TARGET_W}:{bottom_h}:flags=lanczos,setsar=1[game];"
        f"[1:v]scale={TARGET_W}:{DIVIDER_H},setsar=1[div];"
        f"[cam][div][game]vstack=inputs=3,setsar=1,format=yuv420p[base];"
        f"[base]ass={ass_name}:fontsdir={fonts_name}[outv]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-hide_banner",
    ]

    trim_duration = None
    if end_sec is not None and end_sec > start_sec:
        trim_duration = end_sec - start_sec

    if start_sec and start_sec > 0:
        cmd += ["-ss", f"{start_sec:.3f}"]
    if trim_duration is not None:
        cmd += ["-t", f"{trim_duration:.3f}"]

    cmd += ["-i", str(video_path)]
    cmd += ["-i", str(DIVIDER_PATH)]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
    ]

    if trim_duration is not None:
        cmd += ["-t", f"{trim_duration:.3f}"]

    cmd.append(str(output_path))
    return cmd


def move_to_used(video_path: Path) -> Path | None:
    """Mueve el clip original a Usados y evita colisiones de nombres."""
    try:
        USED_DIR.mkdir(parents=True, exist_ok=True)
        dest = USED_DIR / video_path.name
        if dest.exists():
            stem, suffix = video_path.stem, video_path.suffix
            dest = USED_DIR / f"{stem}_done{suffix}"
        shutil.move(str(video_path), str(dest))
        print(f"  📦 Original movido a: {dest}")
        return dest
    except Exception as e:
        print(f"  ⚠️  No se pudo mover el original a Usados: {e}")
        return None


def process_one_clip(video_path: Path, interactive: bool = True):
    """
    Procesa un solo clip de principio a fin.
    """
    print(f"\n{'='*60}")
    print(f"🎬 Procesando: {video_path.name}")
    print(f"{'='*60}")

    orig_w, orig_h, fps, duration = get_video_info(video_path)
    print(f"  Resolución original: {orig_w}x{orig_h} | {duration:.1f}s | {fps:.2f} fps")

    output_path = OUTPUT_DIR / f"reel_{video_path.stem}.mp4"
    if SKIP_EXISTING_OUTPUT and output_path.exists() and output_path.stat().st_size >= MIN_VALID_OUTPUT_BYTES:
        print(f"  ⏭️  El Reel ya existe: {output_path.name}")
        move_to_used(video_path)
        return True

    # ---- 1. Seleccionar facecam ----
    box = None
    if interactive:
        print("\n  📷 Selección de FACECAM")
        print("  1 = Manual")
        print("  2 = LLM (Kimi x5 → DiffusionGemma)  ← recomendado")
        print("  S = Saltar este clip")
        choice = input("  Elegí [1/2/S] (default 2): ").strip().lower() or "2"

        if choice in ("s",):
            print("  ⏭️  Saltado.")
            return False
        elif choice == "2":
            box = detect_facecam_llm(video_path, orig_w, orig_h)
            if box is None:
                print("  ⚠️  No se pudo detectar automáticamente. Pasando a manual...")
                box = manual_select_facecam(video_path)
        else:
            box = manual_select_facecam(video_path)

        # Preview + posibilidad de corregir
        if box is not None:
            frame = extract_frame(video_path, 1.5)
            fx, fy, fw, fh = box
            preview = frame.copy()
            cv2.rectangle(preview, (fx, fy), (fx + fw, fy + fh), (0, 255, 0), 3)
            cv2.putText(preview, "FACECAM - ENTER=OK  M=Manual  S=Saltar", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            scale = min(1.0, 1200 / preview.shape[1])
            if scale < 1.0:
                preview = cv2.resize(preview, None, fx=scale, fy=scale)
            cv2.imshow("Preview facecam", preview)
            key = cv2.waitKey(0) & 0xFF
            cv2.destroyAllWindows()
            if key in (ord('s'), ord('S')):
                print("  ⏭️  Saltado.")
                return False
            elif key in (ord('m'), ord('M')):
                box = manual_select_facecam(video_path)

        if box is None:
            print("  ❌ Cancelado. Saltando clip.")
            return False
    else:
        # Modo no-interactivo: Kimi x5 → DiffusionGemma.
        box = detect_facecam_llm(video_path, orig_w, orig_h)
        if box is None or not is_valid_facecam_box(*box, orig_w, orig_h):
            print("  ❌ No se detectó facecam válida. Saltando.")
            return False

    # Validación final (también en interactivo)
    if not is_valid_facecam_box(*box, orig_w, orig_h):
        print(f"  ⚠️  Box de facecam inválido {box}. Puede verse deforme.")
        if not interactive:
            print("  ❌ Saltando clip por facecam inválida.")
            return False

    # Calcular altura final de la facecam (fija para todo el video)
    fx, fy, fw, fh = box
    cam_h = int(TARGET_W * fh / fw)
    cam_h = max(280, min(cam_h, 720))
    # Evitar estiramientos extremos si el aspect del origen es raro
    src_aspect = fw / max(fh, 1)
    if src_aspect < 0.7 or src_aspect > 1.9:
        cam_h = min(cam_h, 560)
    print(f"  📐 Altura facecam en el Reel: {cam_h}px  (origen {fw}x{fh})")

    # Preview del layout completo
    if interactive:
        preview_layout(video_path, box, orig_w, orig_h, cam_h=cam_h)
        resp = input("\n  ¿Procesar este clip? [Enter=Sí / n=No]: ").strip().lower()
        if resp == "n":
            print("  ⏭️  Saltado por el usuario.")
            return False

    # ---- 2. Transcribir ----
    words = transcribe_video(video_path)
    if not words:
        print("  ⚠️  No se detectó habla. Se generará el video sin subtítulos.")

    # ---- 3. Trim (auto Omni + opcional ajuste manual) ----
    start_sec = 0.0
    end_sec = duration

    suggested = suggest_trim_llm(duration, video_path=video_path)
    if suggested:
        start_sec, end_sec = suggested

    if interactive:
        if suggested:
            print(f"\n  ⏱️  Omni sugiere: {start_sec:.1f}s → {end_sec:.1f}s ({end_sec-start_sec:.1f}s)")
            print("  Opciones:")
            print("    Enter = usar sugerencia de Omni")
            print("    M     = ajustar manualmente en el player")
            print("    T     = usar el clip completo")
            choice = input("  Elegí [Enter/M/T]: ").strip().lower()
            if choice == "m":
                start_sec, end_sec = interactive_trim(video_path, duration, words=words)
            elif choice == "t":
                start_sec, end_sec = 0.0, duration
                print("  → Clip completo")
            else:
                print(f"  → Usando Omni: {start_sec:.1f}s → {end_sec:.1f}s")
        else:
            print("\n  ⏱️  Omni no pudo sugerir trim. Opciones:")
            print("    M = elegir en el player")
            print("    T = clip completo (default)")
            choice = input("  Elegí [M/T]: ").strip().lower()
            if choice == "m":
                start_sec, end_sec = interactive_trim(video_path, duration, words=words)
            else:
                start_sec, end_sec = 0.0, duration
                print("  → Clip completo")
    else:
        if suggested:
            print(f"  → Auto-trim Omni: {start_sec:.1f}s → {end_sec:.1f}s")
        else:
            print("  → Sin sugerencia de trim, usando clip completo")

    # Filtrar palabras al tramo y ajustar timestamps
    if words:
        words = [w for w in words if w["end"] > start_sec and w["start"] < end_sec]
        for w in words:
            w["start"] = max(0.0, w["start"] - start_sec)
            w["end"] = max(0.0, w["end"] - start_sec)
        print(f"  → {len(words)} palabras dentro del tramo seleccionado")

    # ---- 3. Generar ASS ----
    # Ponemos el .ass en la carpeta de salida (ruta más limpia y predecible en Windows)
    # Guardamos el .ass temporal al lado del script (ruta más simple y estable en Windows)
    script_dir = Path(__file__).resolve().parent
    ass_path = script_dir / "_temp_subs.ass"
    text_y = cam_h + DIVIDER_H + SUB_Y_OFFSET

    try:
        if words:
            create_ass_file(words, ass_path, TARGET_W, TARGET_H, text_y)
        else:
            create_ass_file([], ass_path, TARGET_W, TARGET_H, text_y)

        # ---- 4. FFmpeg ----
        out_name = f"reel_{video_path.stem}.mp4"
        output_path = OUTPUT_DIR / out_name

        print(f"\n  🎬 Renderizando con FFmpeg → {out_name}")
        cmd = build_ffmpeg_cmd(
            video_path, output_path, box, orig_w, orig_h, ass_path, cam_h,
            start_sec=start_sec, end_sec=end_sec
        )

        # Guardamos el comando por si falla (útil para debug)
        debug_cmd_file = OUTPUT_DIR / "last_ffmpeg_cmd.txt"
        with open(debug_cmd_file, "w", encoding="utf-8") as f:
            f.write(" ".join(f'"{c}"' if " " in c else c for c in cmd))

        # Ejecutar desde la carpeta del script (rutas más estables)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(script_dir),
        )

        full_log = []
        for line in process.stdout:
            line = line.rstrip()
            full_log.append(line)
            # Mostrar progreso
            if "time=" in line or "error" in line.lower() or "Error" in line:
                print(f"\r  {line[:100]}", end="", flush=True)

        process.wait()
        print()

        if process.returncode != 0:
            print(f"  ❌ FFmpeg falló (código {process.returncode})")
            print("\n  --- Últimas líneas del error de FFmpeg ---")
            for line in full_log[-30:]:
                print(f"  {line}")
            print("  ------------------------------------------")
            print(f"\n  El comando completo se guardó en: {debug_cmd_file}")
            print("  Copiá ese error y pegámelo para arreglarlo.")
            return False

        print(f"  ✅ Guardado: {output_path}")

        # Mover original a Clips/Usados
        move_to_used(video_path)

        return True

    finally:
        # Limpiar el .ass temporal
        if ass_path.exists():
            try:
                ass_path.unlink()
            except Exception:
                pass


# ============================================================
# MAIN
# ============================================================

# ============================================================
# WATCHER DE CLIPS DE KICK
# ============================================================

def _parse_clip_timestamp(value: str | None) -> float | None:
    """Convierte un timestamp ISO de Kick a epoch."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _clip_stream_identity(clip: dict) -> str | None:
    """Identidad del VOD/livestream al que pertenece el clip."""
    livestream_id = clip.get("livestream_id")
    if livestream_id:
        return f"live:{livestream_id}"

    vod = clip.get("vod")
    if isinstance(vod, dict) and vod.get("id"):
        return f"vod:{vod['id']}"

    channel_id = clip.get("channel_id")
    if channel_id:
        return f"channel:{channel_id}"

    return None


def _compute_thumbnail_phash(url: str | None) -> str | None:
    """Genera un pHash de 64 bits sobre la miniatura del clip."""
    if not url:
        return None

    import requests

    try:
        response = requests.get(
            url,
            timeout=DUPLICATE_THUMBNAIL_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()

        data = np.frombuffer(response.content, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None

        image = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        dct = cv2.dct(image)[:8, :8]
        median = float(np.median(dct[1:, 1:]))
        bits = (dct > median).flatten()

        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)

        return f"{value:016x}"
    except Exception:
        return None


def _phash_distance(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return None


def _normalize_clip_title(title: str | None) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def load_clip_registry() -> dict:
    """
    Carga el registro persistente y migra automáticamente la lista antigua de IDs.
    """
    if not CLIP_REGISTRY_FILE.exists():
        return {}

    try:
        with open(CLIP_REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ⚠️  No se pudo leer el registro: {e}")
        return {}

    if isinstance(data, dict):
        return data

    if isinstance(data, list):
        registry = {
            str(clip_id): {
                "status": "known",
                "migrated_from_legacy": True,
                "last_seen_at": time.time(),
            }
            for clip_id in data
            if clip_id
        }
        save_clip_registry(registry)
        print(f"  🔄 Registro antiguo migrado: {len(registry)} clips.")
        return registry

    print("  ⚠️  Formato de registro desconocido. Arranco con registro vacío.")
    return {}


def save_clip_registry(registry: dict):
    """Guarda el registro de forma atómica y limita su crecimiento."""
    CLIP_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if len(registry) > REGISTRY_MAX_ENTRIES:
        ordered = sorted(
            registry.items(),
            key=lambda item: float(item[1].get("last_seen_at", 0)),
            reverse=True,
        )
        keep = dict(ordered[:REGISTRY_MAX_ENTRIES])
        registry.clear()
        registry.update(keep)

    tmp_path = CLIP_REGISTRY_FILE.with_suffix(CLIP_REGISTRY_FILE.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    tmp_path.replace(CLIP_REGISTRY_FILE)


def update_clip_registry(registry: dict, clip_id: str, **fields):
    # "clip_id" se guarda dentro del registro, pero no puede entrar dos veces
    # como argumento y como **fields.
    fields = dict(fields)
    fields.pop("clip_id", None)

    record = registry.setdefault(str(clip_id), {})
    record.update(fields)
    record["clip_id"] = str(clip_id)
    record["last_seen_at"] = time.time()
    save_clip_registry(registry)


def _clip_is_retryable(record: dict) -> bool:
    status = record.get("status")
    if status not in {"discovered", "failed", "processing"}:
        return False

    last_attempt = float(record.get("last_attempt_at", 0))
    return (time.time() - last_attempt) >= FAILED_RETRY_SECONDS


def _get_clip_thumbnail_url(clip: dict) -> str | None:
    """Usa thumbnail_url del listado y consulta el detalle solo si falta."""
    thumbnail = clip.get("thumbnail_url")
    if thumbnail:
        return thumbnail

    clip_id = clip.get("id")
    if not clip_id:
        return None

    import requests

    try:
        r = requests.get(
            f"https://kick.com/api/v2/clips/{clip_id}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        detail = data.get("clip") if isinstance(data, dict) else None
        return detail.get("thumbnail_url") if isinstance(detail, dict) else None
    except Exception:
        return None


def _find_duplicate_clip(clip: dict, fingerprint: str | None, registry: dict) -> str | None:
    """Busca un clip reciente con la misma escena usando miniatura + metadata."""
    clip_created = _parse_clip_timestamp(clip.get("created_at"))
    if clip_created is None:
        return None

    try:
        clip_duration = float(clip.get("duration")) if clip.get("duration") is not None else None
    except (TypeError, ValueError):
        clip_duration = None

    clip_stream = _clip_stream_identity(clip)
    clip_title = _normalize_clip_title(clip.get("title"))

    candidates = []
    for other_id, record in registry.items():
        if other_id == str(clip.get("id")):
            continue
        if record.get("status") not in {"downloaded", "processing", "processed", "duplicate"}:
            continue

        other_created = record.get("created_timestamp")
        if other_created is None:
            continue

        delta = abs(float(clip_created) - float(other_created))
        if delta > DUPLICATE_WINDOW_SECONDS:
            continue

        other_stream = record.get("stream_identity")
        if clip_stream and other_stream and clip_stream != other_stream:
            continue

        other_duration = record.get("duration")
        if clip_duration is not None and other_duration is not None:
            try:
                if abs(clip_duration - float(other_duration)) > DUPLICATE_DURATION_TOLERANCE:
                    continue
            except (TypeError, ValueError):
                pass

        distance = _phash_distance(fingerprint, record.get("fingerprint"))
        if distance is not None and distance <= DUPLICATE_PHASH_DISTANCE:
            candidates.append((distance, other_id))
            continue

        if (
            not fingerprint
            and clip_title
            and clip_title == record.get("title_normalized")
            and delta <= DUPLICATE_TITLE_WINDOW_SECONDS
        ):
            candidates.append((0, other_id))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def fetch_kick_clips(channel: str = KICK_CHANNEL, limit: int = 50) -> list:
    """Obtiene los clips más recientes del canal."""
    import requests

    url = f"https://kick.com/api/v2/channels/{channel}/clips"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    data = r.json()
    clips = data.get("clips") or data.get("data") or []
    return clips[:limit]


def download_kick_clip(clip: dict, dest_dir: Path) -> Path | None:
    """
    Descarga un clip de Kick (m3u8 → mp4) con FFmpeg.
    Nombre: fecha + título sanitizado + id corto.
    """
    clip_id = clip.get("id") or ""
    title = clip.get("title") or "clip"
    video_url = clip.get("video_url") or clip.get("clip_url")
    if not video_url:
        print(f"  ❌ Clip sin video_url: {clip_id}")
        return None

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:40].strip()
    created = (clip.get("created_at") or "")[:10]
    short_id = clip_id.replace("clip_", "")[-12:]
    out_name = f"{created} {safe_title} {short_id}.mp4".strip()
    out_path = dest_dir / out_name

    if out_path.exists() and out_path.stat().st_size >= MIN_VALID_OUTPUT_BYTES:
        print(f"  ⏭️  Ya existe: {out_name}")
        return out_path

    print(f"  ⬇️  Descargando: {title} ({clip.get('duration', '?')}s)")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        str(out_path),
    ]

    try:
        subprocess.run(cmd, check=True, timeout=180)
        if not out_path.exists() or out_path.stat().st_size < MIN_VALID_OUTPUT_BYTES:
            raise RuntimeError("FFmpeg terminó pero el archivo descargado parece inválido.")
        print(f"  ✅ Descargado: {out_path.name}")
        return out_path
    except Exception as e:
        print(f"  ❌ Error descargando: {e}")
        if out_path.exists():
            out_path.unlink(missing_ok=True)
        return None


def _register_clip_metadata(clip: dict, registry: dict, **extra):
    clip_id = str(clip.get("id"))
    created_timestamp = _parse_clip_timestamp(clip.get("created_at"))

    try:
        duration = float(clip.get("duration")) if clip.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None

    fields = {
        "title": clip.get("title") or "",
        "title_normalized": _normalize_clip_title(clip.get("title")),
        "created_at": clip.get("created_at"),
        "created_timestamp": created_timestamp,
        "started_at": clip.get("started_at"),
        "stream_identity": _clip_stream_identity(clip),
        "duration": duration,
    }
    fields.update(extra)
    update_clip_registry(registry, clip_id, **fields)


def watch_kick_clips():
    """
    Loop 24/7:
    - Consulta Kick cada KICK_POLL_SECONDS.
    - Detecta duplicados por miniatura + metadata antes de descargar el video.
    - Marca 'downloaded' solo después de una descarga válida.
    - Reintenta fallos temporales sin perder el clip.
    """
    print("=" * 60)
    print(f"  WATCHER Kick → /{KICK_CHANNEL}")
    print(f"  Poll cada {KICK_POLL_SECONDS}s")
    print(f"  Dedupe: {DUPLICATE_WINDOW_SECONDS}s / pHash <= {DUPLICATE_PHASH_DISTANCE}")
    print("=" * 60)
    print("  Ctrl+C para detener.\n")

    registry = load_clip_registry()
    print(f"  Registros conocidos: {len(registry)}")

    try:
        current = fetch_kick_clips()
        if not registry:
            for clip in current:
                cid = clip.get("id")
                if cid:
                    _register_clip_metadata(clip, registry, status="known")
            save_clip_registry(registry)
            print(
                f"  🌱 Primera inicialización: {len(current)} clips actuales marcados como conocidos "
                "(solo procesará los que aparezcan después).\n"
            )
        else:
            print()
    except Exception as e:
        print(f"  ⚠️  No se pudo inicializar el registro: {e}\n")

    while True:
        try:
            clips = fetch_kick_clips()
            clips = sorted(
                clips,
                key=lambda c: _parse_clip_timestamp(c.get("created_at")) or 0,
            )

            for clip in clips:
                clip_id = clip.get("id")
                if not clip_id:
                    continue

                clip_id = str(clip_id)
                record = registry.get(clip_id, {})
                status = record.get("status")

                if status in {"known", "processed", "duplicate"}:
                    continue

                if status == "downloaded":
                    downloaded_path = Path(record.get("downloaded_path", ""))
                    if not downloaded_path.exists():
                        record["status"] = "discovered"
                        save_clip_registry(registry)
                    else:
                        print(f"\n🎬 Reanudando procesamiento: {clip.get('title')} ({clip_id})")
                        update_clip_registry(
                            registry,
                            clip_id,
                            status="processing",
                            last_attempt_at=time.time(),
                        )
                        try:
                            ok = process_one_clip(downloaded_path, interactive=False)
                            if ok:
                                update_clip_registry(
                                    registry,
                                    clip_id,
                                    status="processed",
                                    output_path=str(OUTPUT_DIR / f"reel_{downloaded_path.stem}.mp4"),
                                )
                            else:
                                update_clip_registry(
                                    registry,
                                    clip_id,
                                    status="failed",
                                    last_error="process_one_clip devolvió False",
                                )
                        except Exception as e:
                            update_clip_registry(
                                registry,
                                clip_id,
                                status="failed",
                                last_error=str(e),
                            )
                            print(f"  ❌ Error procesando {clip_id}: {e}")
                        continue

                if status in {"discovered", "failed", "processing"} and not _clip_is_retryable(record):
                    continue

                print(f"\n🆕 Clip nuevo: {clip.get('title')} ({clip_id})")

                thumbnail_url = _get_clip_thumbnail_url(clip)
                fingerprint = _compute_thumbnail_phash(thumbnail_url)

                _register_clip_metadata(
                    clip,
                    registry,
                    status="discovered",
                    fingerprint=fingerprint,
                    thumbnail_url=thumbnail_url,
                    last_attempt_at=time.time(),
                )

                duplicate_of = _find_duplicate_clip(clip, fingerprint, registry)
                if duplicate_of:
                    print(f"  ♻️  Duplicado del clip {duplicate_of}. No se descarga.")
                    update_clip_registry(
                        registry,
                        clip_id,
                        status="duplicate",
                        duplicate_of=duplicate_of,
                    )
                    continue

                path = download_kick_clip(clip, CLIPS_DIR)
                if path is None:
                    update_clip_registry(
                        registry,
                        clip_id,
                        status="failed",
                        last_error="download_kick_clip falló",
                    )
                    continue

                update_clip_registry(
                    registry,
                    clip_id,
                    status="downloaded",
                    downloaded_path=str(path),
                )

                print("  🎬 Procesando automáticamente...")
                update_clip_registry(
                    registry,
                    clip_id,
                    status="processing",
                    last_attempt_at=time.time(),
                )

                try:
                    ok = process_one_clip(path, interactive=False)
                    if ok:
                        update_clip_registry(
                            registry,
                            clip_id,
                            status="processed",
                            output_path=str(OUTPUT_DIR / f"reel_{path.stem}.mp4"),
                        )
                    else:
                        update_clip_registry(
                            registry,
                            clip_id,
                            status="failed",
                            last_error="process_one_clip devolvió False",
                        )
                except Exception as e:
                    update_clip_registry(
                        registry,
                        clip_id,
                        status="failed",
                        last_error=str(e),
                    )
                    print(f"  ❌ Error procesando {clip_id}: {e}")

            time.sleep(KICK_POLL_SECONDS)

        except KeyboardInterrupt:
            print("\n\n⏹️  Watcher detenido.")
            break
        except Exception as e:
            print(f"\n  ⚠️  Error en el loop: {e}")
            time.sleep(KICK_POLL_SECONDS)


def main():
    print("=" * 60)
    print("  ESKROTOS REEL MAKER")
    print("  Horizontal → Vertical + Facecam + Subtítulos animados")
    print("=" * 60)

    check_dependencies()
    ensure_dirs()

    print("\nModo:")
    print("  1. Interactivo (clips que ya están en G:\\Clips)")
    print("  2. Automático (clips existentes, sin preguntar)")
    print("  3. Watcher Kick (escucha clips nuevos del canal y los procesa)")
    choice = input("\nElegí [1/2/3] (default 1): ").strip() or "1"

    if choice == "3":
        watch_kick_clips()
        return

    clips = sorted(CLIPS_DIR.glob("*.mp4"))
    if not clips:
        print(f"\n❌ No se encontraron archivos .mp4 en {CLIPS_DIR}")
        sys.exit(1)

    print(f"\n📁 Se encontraron {len(clips)} clips en {CLIPS_DIR}")
    print(f"📂 Los reels se guardarán en {OUTPUT_DIR}\n")

    interactive = choice != "2"
    success = 0
    skipped = 0

    for i, clip in enumerate(clips, 1):
        print(f"\n[{i}/{len(clips)}]")
        try:
            ok = process_one_clip(clip, interactive=interactive)
            if ok:
                success += 1
            else:
                skipped += 1
        except KeyboardInterrupt:
            print("\n\n⏹️  Interrumpido por el usuario.")
            break
        except Exception as e:
            print(f"\n❌ Error procesando {clip.name}: {e}")
            import traceback
            traceback.print_exc()
            skipped += 1
            continue

    print("\n" + "=" * 60)
    print(f"✅ Terminados: {success}")
    print(f"⏭️  Saltados / errores: {skipped}")
    print(f"📂 Carpeta de salida: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
