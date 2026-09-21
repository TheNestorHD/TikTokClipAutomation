#!/usr/bin/env python3
from __future__ import annotations
"""
TikTok Clip Automation
----------------------
Kick → descarga → dedupe → edición vertical → subtítulos → exportado → TikTok
"""

import os
import sys
import json
import time
import shutil
import tempfile
import subprocess
import threading
import queue
import random
import hashlib
import webbrowser
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
sync_playwright = None

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

cv2 = None
np = None

# ============================================================
# CONFIGURACIÓN
# ============================================================
from dotenv import load_dotenv, set_key

APP_VERSION = "0.2.0"
APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"
load_dotenv(ENV_FILE, override=False)

def env_value(name: str, default=None):
    value = os.getenv(name)
    return default if value is None or value == "" else value

def env_bool(name: str, default=False) -> bool:
    value = str(env_value(name, str(default))).strip().lower()
    return value in {"1", "true", "yes", "y", "on", "si", "sí"}

def env_int(name: str, default: int) -> int:
    try:
        return int(float(env_value(name, default)))
    except (TypeError, ValueError):
        return default

def resolve_path(value: str | Path, default_relative: str = "") -> Path:
    raw = Path(str(value or default_relative).strip().strip('"'))
    if raw.is_absolute():
        return raw
    return APP_DIR / raw

# --- Rutas ---
CLIPS_DIR = resolve_path(env_value("CLIPS_DIR", "data/clips"))
OUTPUT_DIR = resolve_path(env_value("OUTPUT_DIR", "data/reels"))
USED_DIR = resolve_path(env_value("USED_DIR", "data/used"))
DIVIDER_PATH = resolve_path(env_value("DIVIDER_PATH", "assets/divider.png"))
FONT_PATH = resolve_path(env_value("FONT_PATH", "assets/TF2 build.ttf"))
CLIP_REGISTRY_FILE = resolve_path(env_value("CLIP_REGISTRY_FILE", "data/seen_clips.json"))

# --- Video ---
TARGET_W = env_int("TARGET_W", 1080)
TARGET_H = env_int("TARGET_H", 1920)
DIVIDER_H = env_int("DIVIDER_H", 160)

# --- Subtítulos ---
SUB_SIZE = env_int("SUB_SIZE", 128)
SUB_Y_OFFSET = env_int("SUB_Y_OFFSET", 180)
SUB_MAX_WORDS = env_int("SUB_MAX_WORDS", 1)
SUB_COLOR = env_value("SUB_COLOR", "&H00FFFFFF")
SUB_BORDER = env_value("SUB_BORDER", "&H00000000")
SUB_BORDER_WIDTH = env_int("SUB_BORDER_WIDTH", 12)

# --- Facecam ---
FACE_MAX_RETRIES_KIMI = env_int("FACE_MAX_RETRIES_KIMI", 5)
FACE_FALLBACK_MODEL = env_value("FACE_FALLBACK_MODEL", "google/diffusiongemma-26b-a4b-it")
FACE_KIMI_MODEL = env_value("FACE_KIMI_MODEL", "moonshotai/kimi-k3")
FACE_KIMI_TIMEOUT = env_int("FACE_KIMI_TIMEOUT", 130)
FACE_FALLBACK_TIMEOUT = env_int("FACE_FALLBACK_TIMEOUT", 60)
FACE_SERVER_ERROR_RETRIES = max(0, env_int("FACE_SERVER_ERROR_RETRIES", 0))

# --- Whisper ---
WHISPER_MODEL = env_value("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = env_value("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE = env_value("WHISPER_COMPUTE", "default")
WHISPER_BACKEND = env_value("WHISPER_BACKEND", "auto")
WHISPER_CPP_EXE = resolve_path(
    env_value("WHISPER_CPP_EXE", "tools/whisper.cpp/whisper-cli.exe")
)
WHISPER_CPP_MODEL = resolve_path(
    env_value("WHISPER_CPP_MODEL", "tools/whisper.cpp/models/ggml-large-v3.bin")
)
WHISPER_CPP_THREADS = env_int(
    "WHISPER_CPP_THREADS",
    max(2, min(8, os.cpu_count() or 8))
)

# --- NVIDIA / IA ---
NVIDIA_API_KEY = env_value("NVIDIA_API_KEY", "")
NVIDIA_API_URL = env_value(
    "NVIDIA_API_URL",
    "https://integrate.api.nvidia.com/v1/chat/completions",
)
NVIDIA_TRIM_OMNI = env_value(
    "NVIDIA_TRIM_OMNI",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
)
NVIDIA_TRIM_OMNI_TIMEOUT = env_int("NVIDIA_TRIM_OMNI_TIMEOUT", 180)

# --- Kick watcher ---
KICK_CHANNEL = env_value("KICK_CHANNEL", "eskrotos")
KICK_POLL_SECONDS = max(1, env_int("KICK_POLL_SECONDS", 1))
KICK_ERROR_BACKOFF_SECONDS = max(1, env_int("KICK_ERROR_BACKOFF_SECONDS", 5))

# --- Dedupe ---
DUPLICATE_WINDOW_SECONDS = env_int("DUPLICATE_WINDOW_SECONDS", 75)
DUPLICATE_PHASH_DISTANCE = env_int("DUPLICATE_PHASH_DISTANCE", 10)
DUPLICATE_DURATION_TOLERANCE = env_int("DUPLICATE_DURATION_TOLERANCE", 20)
DUPLICATE_TITLE_WINDOW_SECONDS = env_int("DUPLICATE_TITLE_WINDOW_SECONDS", 30)
DUPLICATE_THUMBNAIL_TIMEOUT = env_int("DUPLICATE_THUMBNAIL_TIMEOUT", 8)

# --- Reintentos / idempotencia ---
SAME_MOMENT_COOLDOWN_SECONDS = env_int("SAME_MOMENT_COOLDOWN_SECONDS", 45)
PROCESS_QUEUE_COOLDOWN_SECONDS = env_int("PROCESS_QUEUE_COOLDOWN_SECONDS", 5)
FAILED_RETRY_SECONDS = env_int("FAILED_RETRY_SECONDS", 120)
REGISTRY_MAX_ENTRIES = env_int("REGISTRY_MAX_ENTRIES", 500)
SKIP_EXISTING_OUTPUT = env_bool("SKIP_EXISTING_OUTPUT", True)
MIN_VALID_OUTPUT_BYTES = env_int("MIN_VALID_OUTPUT_BYTES", 10 * 1024)

# --- TikTok ---
TIKTOK_COOKIES_FILE = resolve_path(
    env_value("TIKTOK_COOKIES_FILE", "data/tiktok_cookies.txt")
)
TIKTOK_HEADLESS = env_bool("TIKTOK_HEADLESS", True)
TIKTOK_AUTO_UPLOAD = env_bool("TIKTOK_AUTO_UPLOAD", True)
TIKTOK_UPLOAD_RETRIES = max(1, env_int("TIKTOK_UPLOAD_RETRIES", 3))
TIKTOK_UPLOAD_RETRY_DELAY_SECONDS = max(0, env_int("TIKTOK_UPLOAD_RETRY_DELAY_SECONDS", 0))
TIKTOK_MINIMIZED = env_bool("TIKTOK_MINIMIZED", True)
TIKTOK_PROCESSING_TIMEOUT_SECONDS = max(30, env_int("TIKTOK_PROCESSING_TIMEOUT_SECONDS", 180))
TIKTOK_CONFIRM_TIMEOUT_SECONDS = max(30, env_int("TIKTOK_CONFIRM_TIMEOUT_SECONDS", 90))
TIKTOK_UPLOAD_CHECK_SECONDS = max(5, env_int("TIKTOK_UPLOAD_CHECK_SECONDS", 30))
TIKTOK_CAPTION_TEMPLATE = env_value(
    "TIKTOK_CAPTION_TEMPLATE",
    "{title} #tiktok #kick",
)
TIKTOK_UPLOAD_REGISTRY = resolve_path(
    env_value("TIKTOK_UPLOAD_REGISTRY", "data/tiktok_uploads.json")
)

PIPELINE_ON_PROCESSED = None
PIPELINE_DOWNLOAD_QUEUE = None
PIPELINE_JOB_QUEUE = None
PIPELINE_DOWNLOAD_THREAD = None
PIPELINE_PROCESSING_THREAD = None
PIPELINE_REGISTRY = None
PIPELINE_REGISTRY_LOCK = threading.RLock()

# ============================================================
# UTILIDADES
# ============================================================

def check_dependencies(exit_on_error: bool = False):
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

    global sync_playwright
    try:
        from playwright.sync_api import sync_playwright as _sync_playwright
        sync_playwright = _sync_playwright
    except ImportError:
        missing.append("playwright")

    if missing:
        print("\n❌ Faltan dependencias:")
        for m in missing:
            print(f"   pip install {m}")
        if exit_on_error:
            sys.exit(1)
        return False

    if shutil.which("ffmpeg") is None:
        print("❌ No se encontró FFmpeg en el PATH.")
        if exit_on_error:
            sys.exit(1)
        return False

    print("✅ Dependencias OK")
    return True


def ensure_dirs(strict: bool = False):
    for path in (CLIPS_DIR, OUTPUT_DIR, USED_DIR):
        path.mkdir(parents=True, exist_ok=True)

    CLIP_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    TIKTOK_UPLOAD_REGISTRY.parent.mkdir(parents=True, exist_ok=True)

    if not DIVIDER_PATH.exists():
        print(f"⚠️  No se encontró el divisor: {DIVIDER_PATH}")
        print("   Se usará un divisor de emergencia durante el render.")
    if not FONT_PATH.exists():
        print(f"⚠️  No se encontró la fuente: {FONT_PATH}")
        print("   FFmpeg usará una fuente alternativa del sistema.")
        if strict:
            return False
    return True


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
                    detail = (r.text or "").strip().replace("\\n", " ")
                    print(
                        f"     ⚠️  {name} HTTP {r.status_code}. "
                        f"Reintento en {wait}s... {detail[:300]}"
                    )
                    time.sleep(wait)
                    continue

                if 500 <= r.status_code <= 599:
                    detail = (r.text or "").strip().replace("\\n", " ")
                    print(
                        f"     ⚠️  {name} HTTP {r.status_code} (error del servidor). "
                        f"{detail[:500]}"
                    )
                    if attempt <= FACE_SERVER_ERROR_RETRIES:
                        wait = min(20, 5 * attempt)
                        print(f"     🔁 Reintento por error 5xx en {wait}s...")
                        time.sleep(wait)
                        continue
                    break

                if r.status_code >= 400:
                    detail = (r.text or "").strip().replace("\\n", " ")
                    print(
                        f"     ⚠️  {name} HTTP {r.status_code}: "
                        f"{detail[:500]}"
                    )
                    break

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

    print(
        "  ❌ Todos los modelos de visión fallaron para facecam. "
        "Si ambos devuelven HTTP 5xx, el problema está del lado del servicio NVIDIA "
        "o de su endpoint de inferencia, no en la detección local."
    )
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
    clean_fonts_dir = APP_DIR / "_fonts_temp"
    if clean_fonts_dir.exists():
        shutil.rmtree(clean_fonts_dir, ignore_errors=True)
    clean_fonts_dir.mkdir(parents=True, exist_ok=True)

    if FONT_PATH.exists():
        shutil.copy2(FONT_PATH, clean_fonts_dir / "TF2Build.ttf")

    return clean_fonts_dir


def ffmpeg_creation_flags():
    """Ejecuta FFmpeg con prioridad reducida en Windows."""
    if os.name != "nt":
        return 0
    priorities = {
        "normal": getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0),
        "below_normal": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
        "idle": getattr(subprocess, "IDLE_PRIORITY_CLASS", 0),
    }
    return priorities.get(FFMPEG_PRIORITY, priorities["idle"])


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

    divider_input = DIVIDER_PATH
    if not divider_input.exists():
        divider_input = APP_DIR / "_fallback_divider.png"
        if not divider_input.exists():
            fallback = np.zeros((DIVIDER_H, TARGET_W, 3), dtype=np.uint8)
            fallback[:] = (0, 180, 0)
            cv2.imwrite(str(divider_input), fallback)

    cmd += ["-i", str(divider_input)]

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
        "-threads", str(FFMPEG_THREADS),
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
        # Validación extra: un mp4 con tamaño suficiente pero moov atom roto / video
        # corrupto pasa el filtro de tamaño. Hacemos un probe barato para confirmarlo.
        try:
            get_video_info(video_path)
        except Exception as probe_err:
            print(f"  ⚠️  El clip existe pero ffprobe no lo pudo leer: {probe_err}")
            print("  → Lo marco como descarga corrupta y aborto (no se saltea ni se mueve).")
            raise RuntimeError(f"Clip inválido/corrupto: {video_path.name}")
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
            creationflags=ffmpeg_creation_flags(),
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
    with PIPELINE_REGISTRY_LOCK:
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

        tmp_path = CLIP_REGISTRY_FILE.with_suffix(
            CLIP_REGISTRY_FILE.suffix + ".tmp"
        )
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
        tmp_path.replace(CLIP_REGISTRY_FILE)


def update_clip_registry(registry: dict, clip_id: str, **fields):
    # "clip_id" se guarda dentro del registro, pero no puede entrar dos veces
    # como argumento y como **fields.
    with PIPELINE_REGISTRY_LOCK:
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

    # Los clips "discovered" (recién detectados o reseteados por archivo faltante)
    # no tienen cooldown: el cooldown es solo para errores reales de proceso.
    if status == "discovered":
        return True

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
        if record.get("status") not in {"queued", "downloading", "downloaded", "processing", "processed", "duplicate"}:
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

        if delta <= SAME_MOMENT_COOLDOWN_SECONDS:
            candidates.append((-1, other_id))
            continue

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
    Descarga un clip de Kick (m3u8 → mp4).

    1) Intenta con yt-dlp (maneja HLS correctamente y reintenta segmentos).
    2) Fallback: FFmpeg con flags de reconexión para streams HLS
       (-multiple_requests evita reuse de conexiones y 416s al reintentar).

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

    def _is_valid() -> bool:
        return out_path.exists() and out_path.stat().st_size >= MIN_VALID_OUTPUT_BYTES

    def _cleanup_partial():
        if out_path.exists() and not _is_valid():
            try:
                out_path.unlink()
            except Exception:
                pass

    max_attempts = 3

    yt_dlp = shutil.which("yt-dlp")
    if yt_dlp:
        cmd = [
            yt_dlp,
            "--no-playlist",
            "--no-part",          # escribe directo al .mp4 final
            "--no-mtime",
            "--retries", "3",
            "--fragment-retries", "5",
            "--restrict-filenames",
            "-f", "best[ext=mp4]/best",
            "-o", str(out_path),
            video_url,
        ]
    else:
        cmd = None

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(f"  🔁 Reintento de descarga ({attempt}/{max_attempts})...")
            _cleanup_partial()

        if cmd is not None:
            print(f"  ⬇️  Descargando con yt-dlp: {title} ({clip.get('duration', '?')}s)")
        else:
            print(f"  ⬇️  Descargando con FFmpeg: {title} ({clip.get('duration', '?')}s)")
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                "-multiple_requests", "1",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-rw_timeout", "30000000",
                "-i", video_url,
                "-c", "copy",
                "-bsf:a", "aac_adtstoasc",
                str(out_path),
            ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            stderr = (result.stderr or "").strip()

            if result.returncode != 0:
                tail = "\n".join(stderr.splitlines()[-6:]) if stderr else "(sin salida de error)"
                print(f"  ⚠️  Descarga falló (código {result.returncode}):\n  {tail}")
                continue

            if not _is_valid():
                print("  ⚠️  La descarga terminó pero el archivo parece inválido/corrupto.")
                continue

            print(f"  ✅ Descargado: {out_path.name}")
            return out_path

        except subprocess.TimeoutExpired:
            print("  ⚠️  Timeout de descarga (300s).")
        except Exception as e:
            print(f"  ⚠️  Error descargando: {e}")

    _cleanup_partial()
    print(f"  ❌ No se pudo descargar {clip_id} tras {max_attempts} intentos.")
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


def watch_kick_clips(stop_event=None, on_processed=None):
    """
    Watcher de Kick con pipeline por etapas:

    1) Watcher: detecta clips y aplica deduplicación.
    2) Downloader: descarga un clip por vez.
    3) Processor: procesa un clip por vez (IA + Whisper + FFmpeg).

    La descarga puede adelantarse mientras el processor está ocupado, pero
    las llamadas de IA y los renders siempre son estrictamente secuenciales.
    """
    global PIPELINE_DOWNLOAD_QUEUE, PIPELINE_JOB_QUEUE
    global PIPELINE_DOWNLOAD_THREAD, PIPELINE_PROCESSING_THREAD
    global PIPELINE_REGISTRY, PIPELINE_ON_PROCESSED

    print("=" * 60)
    print(f"  WATCHER Kick → /{KICK_CHANNEL}")
    print(f"  Poll cada {KICK_POLL_SECONDS}s")
    print(
        f"  Dedupe: cooldown {SAME_MOMENT_COOLDOWN_SECONDS}s + "
        f"ventana {DUPLICATE_WINDOW_SECONDS}s / pHash <= {DUPLICATE_PHASH_DISTANCE}"
    )
    print(
        f"  Descarga: 1 worker | Procesamiento: 1 worker | "
        f"Cooldown: {PROCESS_QUEUE_COOLDOWN_SECONDS}s"
    )
    print("=" * 60)
    print("  Ctrl+C para detener.\n")

    if stop_event is None:
        stop_event = threading.Event()

    download_queue = queue.Queue()
    process_queue = queue.Queue()

    PIPELINE_DOWNLOAD_QUEUE = download_queue
    PIPELINE_JOB_QUEUE = process_queue
    PIPELINE_ON_PROCESSED = on_processed

    registry = load_clip_registry()
    PIPELINE_REGISTRY = registry
    print(f"  Registros conocidos: {len(registry)}")

    def registry_update(clip_id: str, **fields):
        with PIPELINE_REGISTRY_LOCK:
            update_clip_registry(registry, clip_id, **fields)

    def processor_worker():
        print("  ⚙️  Processor activo: máximo 1 clip de IA/render a la vez.")

        while not stop_event.is_set():
            try:
                job = process_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            clip_id = str(job.get("id"))
            path = Path(job.get("local_path", ""))

            try:
                if not path.exists():
                    registry_update(
                        clip_id,
                        status="failed",
                        last_error="archivo descargado desapareció del disco",
                        last_attempt_at=time.time(),
                    )
                    print(f"  ❌ No existe el archivo para procesar: {path}")
                    continue

                registry_update(
                    clip_id,
                    status="processing",
                    downloaded_path=str(path),
                    last_attempt_at=time.time(),
                    last_error=None,
                )

                print(
                    f"\n🎬 Procesando en secuencia: "
                    f"{job.get('title') or path.name}"
                )

                ok = process_one_clip(path, interactive=False)

                if ok:
                    output_path = OUTPUT_DIR / f"reel_{path.stem}.mp4"
                    registry_update(
                        clip_id,
                        status="processed",
                        output_path=str(output_path),
                        last_error=None,
                    )
                    print(f"  ✅ Clip terminado: {output_path.name}")

                    if PIPELINE_ON_PROCESSED:
                        try:
                            PIPELINE_ON_PROCESSED(output_path, job)
                        except Exception as callback_error:
                            print(
                                f"  ⚠️  Callback post-procesado falló: "
                                f"{callback_error}"
                            )
                else:
                    registry_update(
                        clip_id,
                        status="failed",
                        last_error="process_one_clip devolvió False",
                        last_attempt_at=time.time(),
                    )

            except Exception as exc:
                registry_update(
                    clip_id,
                    status="failed",
                    last_error=str(exc),
                    last_attempt_at=time.time(),
                )
                print(f"  ❌ Error procesando {clip_id}: {exc}")

            finally:
                process_queue.task_done()

            if (
                PROCESS_QUEUE_COOLDOWN_SECONDS > 0
                and not stop_event.is_set()
                and not process_queue.empty()
            ):
                print(
                    f"  💤 Cooldown de procesamiento: "
                    f"{PROCESS_QUEUE_COOLDOWN_SECONDS}s..."
                )
                if stop_event.wait(PROCESS_QUEUE_COOLDOWN_SECONDS):
                    break

        print("  ⏹️  Processor detenido.")

    def downloader_worker():
        print("  ⬇️  Downloader activo: máximo 1 descarga simultánea.")

        while not stop_event.is_set():
            try:
                clip = download_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            clip_id = str(clip.get("id"))

            try:
                record = registry.get(clip_id, {})
                downloaded_path_str = record.get("downloaded_path")
                downloaded_path = (
                    Path(downloaded_path_str)
                    if downloaded_path_str
                    else None
                )

                if downloaded_path is not None and downloaded_path.exists():
                    path = downloaded_path
                    print(
                        f"\n📦 Descarga ya disponible: "
                        f"{path.name}"
                    )
                else:
                    registry_update(
                        clip_id,
                        status="downloading",
                        last_attempt_at=time.time(),
                        last_error=None,
                    )
                    print(
                        f"\n⬇️  Descargando clip: "
                        f"{clip.get('title')} ({clip_id})"
                    )

                    path = download_kick_clip(clip, CLIPS_DIR)

                    if path is None:
                        registry_update(
                            clip_id,
                            status="failed",
                            last_error="download_kick_clip falló",
                            last_attempt_at=time.time(),
                        )
                        continue

                    registry_update(
                        clip_id,
                        status="downloaded",
                        downloaded_path=str(path),
                        last_error=None,
                    )
                    print(
                        f"  ✅ Descargado y listo para procesar: "
                        f"{path.name}"
                    )

                process_queue.put({
                    **clip,
                    "id": clip_id,
                    "local_path": str(path),
                })

            except Exception as exc:
                registry_update(
                    clip_id,
                    status="failed",
                    last_error=str(exc),
                    last_attempt_at=time.time(),
                )
                print(f"  ❌ Error descargando {clip_id}: {exc}")

            finally:
                download_queue.task_done()

        print("  ⏹️  Downloader detenido.")

    PIPELINE_PROCESSING_THREAD = threading.Thread(
        target=processor_worker,
        name="ClipProcessingWorker",
        daemon=True,
    )
    PIPELINE_DOWNLOAD_THREAD = threading.Thread(
        target=downloader_worker,
        name="ClipDownloadWorker",
        daemon=True,
    )

    PIPELINE_PROCESSING_THREAD.start()
    PIPELINE_DOWNLOAD_THREAD.start()

    # Recuperación tras cierre/reinicio:
    # - downloaded con archivo existente -> vuelve a la cola de procesamiento.
    # - processing con archivo existente -> vuelve a downloaded y se reanuda.
    # - queued/downloading sin archivo -> queda como discovered para que
    #   el watcher lo vuelva a detectar si sigue en la API.
    for clip_id, record in list(registry.items()):
        status = record.get("status")
        downloaded_path_str = record.get("downloaded_path")

        if downloaded_path_str:
            local_path = Path(downloaded_path_str)
            if local_path.exists() and status in {"queued", "downloaded", "processing", "downloading"}:
                if status in {"processing", "downloading"}:
                    registry_update(
                        clip_id,
                        status="downloaded",
                        last_error="reanudado tras reinicio",
                        last_attempt_at=0,
                    )

                process_queue.put({
                    "id": clip_id,
                    "title": record.get("title") or local_path.stem,
                    "created_at": record.get("created_at"),
                    "local_path": str(local_path),
                })
                print(
                    f"  ♻️  Reanudado desde disco: {local_path.name}"
                )
            elif status in {"queued", "downloading", "processing", "downloaded"}:
                registry_update(
                    clip_id,
                    status="discovered",
                    downloaded_path=None,
                    last_error="archivo no disponible; se reintentará",
                    last_attempt_at=0,
                )

    try:
        current = fetch_kick_clips()

        if not registry:
            for clip in current:
                cid = clip.get("id")
                if cid:
                    _register_clip_metadata(
                        clip,
                        registry,
                        status="known",
                    )
            save_clip_registry(registry)
            print(
                f"  🌱 Primera inicialización: {len(current)} clips actuales "
                "marcados como conocidos (solo procesará los que aparezcan después).\n"
            )
        else:
            print()
    except Exception as exc:
        print(f"  ⚠️  No se pudo inicializar el registro: {exc}\n")

    polls_since_heartbeat = 0
    heartbeat_polls = max(
        1,
        round(60 / max(KICK_POLL_SECONDS, 1)),
    )

    while not stop_event.is_set():
        try:
            clips = fetch_kick_clips()
            clips = sorted(
                clips,
                key=lambda c: _parse_clip_timestamp(c.get("created_at")) or 0,
            )

            for clip in clips:
                if stop_event.is_set():
                    break

                clip_id = clip.get("id")
                if not clip_id:
                    continue

                clip_id = str(clip_id)
                record = registry.get(clip_id, {})
                status = record.get("status")

                if status in {
                    "known",
                    "queued",
                    "downloading",
                    "downloaded",
                    "processing",
                    "processed",
                    "duplicate",
                }:
                    continue

                downloaded_path_str = record.get("downloaded_path")
                if status in {"failed", "discovered"} and downloaded_path_str:
                    candidate_path = Path(downloaded_path_str)
                    if candidate_path.exists():
                        registry_update(
                            clip_id,
                            status="downloaded",
                            last_error=None,
                        )
                        process_queue.put({
                            **clip,
                            "id": clip_id,
                            "local_path": str(candidate_path),
                        })
                        continue

                    registry_update(
                        clip_id,
                        status="discovered",
                        downloaded_path=None,
                        last_error="archivo desapareció del disco",
                    )
                    status = "discovered"

                if status == "failed" and not _clip_is_retryable(record):
                    continue

                print(
                    f"\n🆕 Clip detectado: "
                    f"{clip.get('title')} ({clip_id})"
                )

                thumbnail_url = _get_clip_thumbnail_url(clip)
                fingerprint = _compute_thumbnail_phash(thumbnail_url)

                duplicate_of = _find_duplicate_clip(
                    clip,
                    fingerprint,
                    registry,
                )

                if duplicate_of:
                    print(
                        f"  ♻️  Duplicado/cooldown del clip {duplicate_of}. "
                        "No se descarga ni se procesa."
                    )
                    _register_clip_metadata(
                        clip,
                        registry,
                        status="duplicate",
                        fingerprint=fingerprint,
                        thumbnail_url=thumbnail_url,
                        duplicate_of=duplicate_of,
                    )
                    continue

                _register_clip_metadata(
                    clip,
                    registry,
                    status="queued",
                    fingerprint=fingerprint,
                    thumbnail_url=thumbnail_url,
                    last_attempt_at=time.time(),
                )

                download_queue.put(clip)
                print(
                    f"  📥 Encolado para descarga: "
                    f"{clip.get('title')} "
                    f"(descargas pendientes: {download_queue.qsize()})"
                )

            polls_since_heartbeat += 1
            if polls_since_heartbeat >= heartbeat_polls:
                polls_since_heartbeat = 0

                queued = sum(
                    1
                    for r in registry.values()
                    if r.get("status") == "queued"
                )
                downloaded = sum(
                    1
                    for r in registry.values()
                    if r.get("status") == "downloaded"
                )
                processing = sum(
                    1
                    for r in registry.values()
                    if r.get("status") == "processing"
                )
                known = sum(
                    1
                    for r in registry.values()
                    if r.get("status") == "known"
                )

                ts = datetime.now().strftime("%H:%M:%S")
                print(
                    f"  [{ts}] ⏱️  Watcher vivo — "
                    f"{len(clips)} clips en API, "
                    f"{known} conocidos, "
                    f"{queued} esperando descarga, "
                    f"{downloaded} descargados/esperando IA, "
                    f"{processing} procesando, "
                    f"DQ={download_queue.qsize()}, "
                    f"PQ={process_queue.qsize()}.",
                    flush=True,
                )

            if stop_event.wait(KICK_POLL_SECONDS):
                break

        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"\n  ⚠️  Error en el loop: {exc}")
            if stop_event.wait(KICK_ERROR_BACKOFF_SECONDS):
                break

    # Normalizar trabajos que quedaron en la cola de descarga al detenerse.
    # Los ya descargados conservan su archivo para que el siguiente arranque
    # los pueda reanudar directamente en la cola de procesamiento.
    with PIPELINE_REGISTRY_LOCK:
        for clip_id, record in list(registry.items()):
            status = record.get("status")
            if status in {"queued", "downloading"} and not record.get("downloaded_path"):
                record["status"] = "discovered"
                record["last_attempt_at"] = 0
                record["last_error"] = "reanudar tras detención"

        save_clip_registry(registry)

    print(
        "\n⏹️  Watcher detenido. "
        "Los estados persistidos permiten reanudar las descargas/procesamientos."
    )

    PIPELINE_DOWNLOAD_QUEUE = None
    PIPELINE_JOB_QUEUE = None
    PIPELINE_DOWNLOAD_THREAD = None
    PIPELINE_PROCESSING_THREAD = None
    PIPELINE_REGISTRY = None



# ============================================================
# TikTok uploader helpers (integrados)
# ============================================================
def cargar_cookies(context, cookies_path: Path):
    """Carga cookies desde un archivo Netscape cookies.txt"""
    cookies = []
    with open(cookies_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7:
                domain, flag, path, secure, expires, name, value = parts[:7]
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path,
                    "expires": int(expires) if expires.isdigit() else -1,
                    "httpOnly": False,
                    "secure": secure.upper() == "TRUE",
                    "sameSite": "Lax"
                })
    context.add_cookies(cookies)


def cerrar_popups(page):
    """Intenta cerrar los popups más comunes de TikTok Studio + tours"""
    selectores = [
        # Botones normales
        'button:has-text("Got it")',
        'button:has-text("Got It")',
        'button:has-text("Not now")',
        'button:has-text("Close")',
        'button:has-text("Accept")',
        'button:has-text("Continue")',
        'button:has-text("Post now")',
        'button:has-text("Skip")',
        'button:has-text("Next")',
        'button:has-text("Done")',
        'button:has-text("Entendido")',
        'button:has-text("Saltar")',
        
        # React Joyride
        '[data-test-id="overlay"]',
        '.react-joyride__overlay',
        'div[class*="react-joyride"]',
        'button[data-action="close"]',
        'button[aria-label="Close"]',
        'button[aria-label="close"]',
    ]

    for sel in selectores:
        try:
            elements = page.locator(sel)
            count = elements.count()
            for i in range(count):
                el = elements.nth(i)
                if el.is_visible(timeout=800):
                    el.click(timeout=1500)
                    print(f"  → Cerrado popup/tour: {sel}")
                    time.sleep(0.6)
        except Exception:
            pass

    # Click suave fuera por si queda overlay
    try:
        page.mouse.click(10, 10)
        time.sleep(0.4)
    except:
        pass


def manejar_dialogo_salida(page):
    """Si aparece el cartel 'Are you sure you want to exit?', hace click en Cancel"""
    try:
        # Buscar el diálogo de salida
        dialogo = page.locator('text="Are you sure you want to exit?"')
        if dialogo.is_visible(timeout=1500):
            print("  → Detectado diálogo de salida, haciendo click en Cancel...")
            cancel_btn = page.locator('button:has-text("Cancel")').first
            if cancel_btn.is_visible(timeout=1000):
                cancel_btn.click()
                time.sleep(1)
                return True
    except Exception:
        pass
    return False



def subir_video(ruta_video: Path, caption: str) -> bool:
    """Intenta publicar un Reel en TikTok Studio usando Playwright."""
    for intento in range(1, TIKTOK_UPLOAD_RETRIES + 1):
        if intento > 1:
            print(
                f"\n↻ Reintento {intento}/{TIKTOK_UPLOAD_RETRIES} "
                f"en {TIKTOK_UPLOAD_RETRY_DELAY_SECONDS}s..."
            )
            time.sleep(TIKTOK_UPLOAD_RETRY_DELAY_SECONDS)

        if _subir_video_intento(ruta_video, caption, intento):
            return True

    print(f"✗ Falló después de {TIKTOK_UPLOAD_RETRIES} intentos.")
    return False


def _subir_video_intento(
    ruta_video: Path,
    caption: str,
    intento: int = 1,
) -> bool:
    global sync_playwright

    if sync_playwright is None:
        from playwright.sync_api import sync_playwright as _sync_playwright
        sync_playwright = _sync_playwright

    print(
        f"\n[{datetime.now().strftime('%H:%M:%S')}] "
        f"Subiendo (intento {intento}): {ruta_video.name}"
    )
    print(f"Caption: {caption[:80]}{'...' if len(caption) > 80 else ''}")

    browser = None
    with sync_playwright() as p:
        browser_args = ["--disable-blink-features=AutomationControlled"]

        if not TIKTOK_HEADLESS and TIKTOK_MINIMIZED:
            # Chromium suele ignorar --start-minimized en Playwright.
            # Sacamos la ventana del área visible en modo no-headless.
            browser_args.append("--window-position=-32000,-32000")

        browser = p.chromium.launch(
            headless=TIKTOK_HEADLESS,
            args=browser_args,
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            ),
        )

        try:
            if not TIKTOK_COOKIES_FILE.exists():
                raise FileNotFoundError(
                    f"No existe el archivo de cookies: {TIKTOK_COOKIES_FILE}"
                )

            if not ruta_video.exists():
                raise FileNotFoundError(
                    f"No existe el video: {ruta_video}"
                )

            cargar_cookies(context, TIKTOK_COOKIES_FILE)
            page = context.new_page()

            print("→ Navegando a TikTok Studio Upload...")
            page.goto(
                "https://www.tiktok.com/tiktokstudio/upload?lang=en",
                timeout=60000,
            )
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)

            cerrar_popups(page)

            print("→ Subiendo archivo...")
            file_input = page.locator('input[type="file"]').first
            file_input.wait_for(state="attached", timeout=15000)
            file_input.set_input_files(str(ruta_video))
            time.sleep(2)

            print("→ Esperando a que el video se procese (Post habilitado)...")
            procesado = False
            deadline = time.time() + TIKTOK_PROCESSING_TIMEOUT_SECONDS

            while time.time() < deadline:
                try:
                    btn = page.locator(
                        'button[data-e2e="post_video_button"], '
                        'button:has-text("Post")'
                    ).first

                    if btn.is_visible(timeout=800):
                        disabled = (
                            btn.get_attribute("disabled")
                            or btn.get_attribute("aria-disabled")
                        )
                        clases = (btn.get_attribute("class") or "").lower()

                        if (
                            disabled not in ["true", "True", True]
                            and "disabled" not in clases
                        ):
                            procesado = True
                            break
                except Exception:
                    pass

                time.sleep(2)

            if not procesado:
                print(
                    f"✗ El video no terminó de procesarse en "
                    f"{TIKTOK_PROCESSING_TIMEOUT_SECONDS}s"
                )
                page.screenshot(path="error_procesamiento.png")
                return False

            print("→ Video procesado, botón Post habilitado")
            time.sleep(1.5)
            cerrar_popups(page)

            print("→ Escribiendo descripción...")
            desc = page.locator('div[contenteditable="true"]').first
            desc.wait_for(state="visible", timeout=20000)
            desc.click()
            time.sleep(0.4)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(0.3)
            desc.type(caption, delay=20)
            time.sleep(1.2)
            cerrar_popups(page)

            print("→ Buscando botón Publicar...")

            post_selectors = [
                'button[data-e2e="post_video_button"]',
                'button:has-text("Post"):not([disabled])',
                'div[class*="btn-post"] button',
                'button.Button__root--type-primary:has-text("Post")',
                'button.TUXButton--primary:has-text("Post")',
                '//button[contains(@class,"primary") and .//div[text()="Post"]]',
            ]

            clicked = False
            for sel in post_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0 and btn.is_visible(timeout=2500):
                        disabled = (
                            btn.get_attribute("disabled")
                            or btn.get_attribute("aria-disabled")
                        )
                        if disabled in ["true", "True", True]:
                            continue

                        btn.scroll_into_view_if_needed()
                        time.sleep(0.5)

                        try:
                            btn.click(timeout=5000)
                        except Exception:
                            btn.evaluate("el => el.click()")

                        print(f"  → Click correcto en: {sel}")
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                print("✗ No se encontró el botón de Publicar")
                page.screenshot(path="error_no_post_button.png")
                return False

            print(
                f"→ Esperando confirmación (hasta "
                f"{TIKTOK_CONFIRM_TIMEOUT_SECONDS}s)..."
            )

            publicado = False
            deadline = time.time() + TIKTOK_CONFIRM_TIMEOUT_SECONDS

            while time.time() < deadline:
                content = page.content().lower()
                url = page.url.lower()

                exito = any([
                    "your video is being uploaded" in content,
                    "video published" in content,
                    "uploaded successfully" in content,
                    "publicado" in content,
                    "se está subiendo" in content,
                    "/tiktokstudio/content" in url,
                    ("manage" in url and "content" in url),
                    ("content" in url and "tiktokstudio" in url),
                ])

                fallo = any([
                    "something went wrong" in content,
                    "try again" in content,
                    "failed" in content and "upload" in content,
                ])

                if exito and not fallo:
                    publicado = True
                    break

                post_now = False
                for sel in [
                    'button:has-text("Post now")',
                    'button:has-text("Publicar ahora")',
                    (
                        '//button[contains(translate(translate(., '
                        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
                        '"abcdefghijklmnopqrstuvwxyz"), "POST NOW", '
                        '"post now"), "post now")]'
                    ),
                ]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=600):
                            try:
                                btn.click(timeout=1500)
                            except Exception:
                                btn.evaluate("el => el.click()")

                            print(
                                f"  → Confirmado modal Post now "
                                f"({sel[:30]})"
                            )
                            post_now = True
                            break
                    except Exception:
                        pass

                if post_now:
                    time.sleep(2)
                    continue

                if manejar_dialogo_salida(page):
                    print(
                        "  → Diálogo de salida cancelado. "
                        "Reintentando click en Post..."
                    )
                    time.sleep(1)

                    for sel in post_selectors:
                        try:
                            btn = page.locator(sel).first
                            if btn.count() > 0 and btn.is_visible(timeout=800):
                                disabled = (
                                    btn.get_attribute("disabled")
                                    or btn.get_attribute("aria-disabled")
                                )
                                if disabled in ["true", "True", True]:
                                    continue

                                try:
                                    btn.click(timeout=1500)
                                except Exception:
                                    btn.evaluate("el => el.click()")

                                print(
                                    f"  → Re-click en Post: {sel[:40]}"
                                )
                                break
                        except Exception:
                            continue

                    time.sleep(2)
                    continue

                time.sleep(2)

            if publicado:
                print("✓ Subido correctamente")
                return True

            print("✗ No se confirmó la publicación")
            page.screenshot(path="error_post_fallido.png")
            print("  (Se guardó captura: error_post_fallido.png)")
            return False

        except Exception as e:
            print(f"✗ Error inesperado: {e}")
            try:
                page.screenshot(path="error_excepcion.png")
            except Exception:
                pass
            return False
        finally:
            if browser is not None:
                browser.close()


# ============================================================
# TIKTOK: SUBIDA AUTOMÁTICA
# ============================================================

def cargar_json(path: Path, default=None):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default if default is not None else {}
    return default if default is not None else {}

def guardar_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def _caption_for_clip(clip: dict) -> str:
    title = (clip.get("title") or "Nuevo clip").strip()
    try:
        return TIKTOK_CAPTION_TEMPLATE.format(title=title)
    except Exception:
        return title

class TikTokUploadManager:
    """Publica cada Reel inmediatamente, sin horarios ni límites artificiales."""

    def __init__(self, log_callback=None):
        self.log_callback = log_callback or (lambda msg: print(msg))
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None

    def log(self, msg):
        self.log_callback(msg)

    def load_state(self):
        return cargar_json(TIKTOK_UPLOAD_REGISTRY, {"items": {}})

    def save_state(self, state):
        guardar_json(TIKTOK_UPLOAD_REGISTRY, state)

    def enqueue(self, video_path: Path, clip: dict | None = None):
        video_path = Path(video_path)
        if not video_path.exists():
            self.log(f"⚠️  TikTok: no existe {video_path}")
            return False

        state = self.load_state()
        items = state.setdefault("items", {})
        key = str(video_path.resolve())

        existing = items.get(key)
        if existing and existing.get("status") in {"uploaded", "queued", "uploading"}:
            return False

        title = (clip or {}).get("title") or video_path.stem
        items[key] = {
            "video_path": key,
            "title": title,
            "caption": _caption_for_clip(clip or {"title": title}),
            "status": "queued",
            "created_at": time.time(),
            "scheduled_at": None,
            "last_error": None,
            "retry_count": 0,
        }
        self.save_state(state)
        self.log(f"📤 TikTok: agregado → {video_path.name}")
        self.wake_event.set()
        return True

    def discover_existing_reels(self):
        if not TIKTOK_AUTO_UPLOAD:
            return
        for video_path in sorted(OUTPUT_DIR.glob("*.mp4")):
            self.enqueue(video_path, {"title": video_path.stem})

    def retry_failed(self):
        state = self.load_state()
        changed = 0
        for item in state.setdefault("items", {}).values():
            if item.get("status") == "failed" and Path(item.get("video_path", "")).exists():
                item["status"] = "queued"
                item["scheduled_at"] = None
                item["last_error"] = None
                item["retry_count"] = int(item.get("retry_count", 0)) + 1
                changed += 1
        if changed:
            self.save_state(state)
            self.wake_event.set()
            self.log(f"🔁 TikTok: {changed} fallo(s) reencolado(s).")
        return changed

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.wake_event.clear()
        self.thread = threading.Thread(
            target=self._worker,
            name="TikTokUploadWorker",
            daemon=True,
        )
        self.thread.start()
        self.log("✅ TikTok automático iniciado: publicación inmediata.")

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.log("⏹️  TikTok automático detenido.")

    def _worker(self):
        while not self.stop_event.is_set():
            if not TIKTOK_AUTO_UPLOAD:
                self.stop_event.wait(TIKTOK_UPLOAD_CHECK_SECONDS)
                continue

            state = self.load_state()
            items = state.setdefault("items", {})
            pending = [
                item for item in items.values()
                if item.get("status") == "queued"
                and Path(item.get("video_path", "")).exists()
            ]
            pending.sort(key=lambda x: x.get("created_at", 0))

            if not pending:
                self.wake_event.wait(TIKTOK_UPLOAD_CHECK_SECONDS)
                self.wake_event.clear()
                continue

            item = pending[0]
            video_path = Path(item["video_path"])
            item["status"] = "uploading"
            item["last_error"] = None
            self.save_state(state)

            self.log(f"🚀 TikTok: subiendo → {video_path.name}")

            try:
                success = subir_video(video_path, item.get("caption", ""))
            except Exception as exc:
                success = False
                item["last_error"] = str(exc)
                self.log(f"❌ TikTok: excepción durante la subida: {exc}")

            if success:
                now = datetime.now()
                item["status"] = "uploaded"
                item["uploaded_date"] = now.strftime("%Y-%m-%d")
                item["uploaded_at"] = now.isoformat()
                item["uploaded_timestamp"] = time.time()
                item["last_error"] = None
                item["scheduled_at"] = None
                self.save_state(state)
                self.log(f"✅ TikTok: publicado → {video_path.name}")
            else:
                item["status"] = "failed"
                item["scheduled_at"] = None
                item["retry_count"] = int(item.get("retry_count", 0)) + 1
                if not item.get("last_error"):
                    item["last_error"] = "La publicación no pudo confirmarse."
                self.save_state(state)
                self.log(
                    f"❌ TikTok: falló {video_path.name}. "
                    "Queda en FALLIDO hasta reintentar desde la interfaz."
                )

# ============================================================
# GUI
# ============================================================

class QueueTextWriter:
    def __init__(self, original, output_queue):
        self.original = original
        self.output_queue = output_queue

    def write(self, text_value):
        if self.original:
            try:
                self.original.write(text_value)
                self.original.flush()
            except Exception:
                pass
        if text_value:
            self.output_queue.put(text_value)

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except Exception:
                pass


class TikTokClipAutomationApp:
    TITLE = "TikTok Clip Automation"
    NVIDIA_KEY_URL = "https://build.nvidia.com/settings/api-keys"
    NVIDIA_MODELS_URL = "https://build.nvidia.com/explore"
    COOKIES_EXTENSION_URL = (
        "https://chromewebstore.google.com/detail/get-cookiestxt-locally/"
        "cclelndahbckbenkjhflpdbgdldlbecc"
    )
    TIKTOK_STUDIO_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

    BG = "#0B0F17"
    SIDEBAR = "#101725"
    CARD = "#151E2E"
    CARD_ALT = "#1B263A"
    BORDER = "#28364C"
    TEXT = "#F3F5F9"
    MUTED = "#8F9BB2"
    ACCENT = "#6C63FF"
    ACCENT_HOVER = "#7D75FF"
    GREEN = "#3DDC97"
    YELLOW = "#FFC857"
    RED = "#FF5D73"
    BLUE = "#51A8FF"

    def __init__(self, root):
        self.root = root
        self.root.title(self.TITLE)
        self.root.geometry("1320x820")
        self.root.minsize(1120, 720)

        self.output_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.watcher_thread = None
        self.tiktok_manager = None
        self.entry_vars = {}
        self.current_page = None
        self.running = False
        self.start_time = None
        self.pulse = False
        self.key_visible = False

        self.status_var = tk.StringVar(value="Detenido")
        self.stage_var = tk.StringVar(value="Esperando")
        self.uptime_var = tk.StringVar(value="00:00:00")
        self.activity_var = tk.StringVar(value="El pipeline está detenido.")
        self.watcher_var = tk.StringVar(value="Watcher detenido")
        self.tiktok_var = tk.StringVar(value="TikTok detenido")
        self.download_queue_var = tk.StringVar(value="0")
        self.process_queue_var = tk.StringVar(value="0")
        self.tiktok_queue_var = tk.StringVar(value="0")
        self.processed_var = tk.StringVar(value="0")
        self.failed_var = tk.StringVar(value="0")
        self.dedupe_var = tk.StringVar(value="0")
        self.ffmpeg_var = tk.StringVar(value="Muy baja · Idle")

        self.auto_upload_var = tk.BooleanVar(value=True)
        self.headless_var = tk.BooleanVar(value=True)
        self.ffmpeg_priority_var = tk.StringVar(value="idle")

        try:
            import customtkinter as ctk
            self.ctk = ctk
        except ImportError as exc:
            root.withdraw()
            messagebox.showerror(
                self.TITLE,
                "Falta customtkinter. Ejecutá:\n\npip install customtkinter\n\n"
                f"Detalle: {exc}",
            )
            root.destroy()
            return

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self._build_ui()
        self._refresh_config_vars()
        self._install_output_redirect()

        self._show_page("dashboard")
        self._tick_ui()
        self._refresh_views()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # --------------------------------------------------------
    # UI base
    # --------------------------------------------------------

    def _frame(self, parent, **kwargs):
        return self.ctk.CTkFrame(
            parent,
            fg_color=kwargs.pop("fg_color", self.CARD),
            border_width=kwargs.pop("border_width", 0),
            border_color=kwargs.pop("border_color", self.BORDER),
            corner_radius=kwargs.pop("corner_radius", 14),
            **kwargs,
        )

    def _label(self, parent, text="", size=14, color=None, bold=False, **kwargs):
        return self.ctk.CTkLabel(
            parent,
            text=text,
            text_color=color or self.TEXT,
            font=self.ctk.CTkFont(
                family="Segoe UI",
                size=size,
                weight="bold" if bold else "normal",
            ),
            **kwargs,
        )

    def _button(self, parent, text, command, width=150, primary=False, **kwargs):
        return self.ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=38,
            corner_radius=10,
            fg_color=self.ACCENT if primary else self.CARD_ALT,
            hover_color=self.ACCENT_HOVER if primary else self.BORDER,
            text_color=self.TEXT,
            font=self.ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            **kwargs,
        )

    def _build_ui(self):
        root_frame = self.ctk.CTkFrame(
            self.root,
            fg_color=self.BG,
            corner_radius=0,
        )
        root_frame.pack(fill="both", expand=True)

        self.sidebar = self.ctk.CTkFrame(
            root_frame,
            width=235,
            fg_color=self.SIDEBAR,
            corner_radius=0,
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        content = self.ctk.CTkFrame(
            root_frame,
            fg_color=self.BG,
            corner_radius=0,
        )
        content.pack(side="right", fill="both", expand=True)

        brand = self._frame(self.sidebar, fg_color=self.SIDEBAR, corner_radius=0)
        brand.pack(fill="x", padx=18, pady=(24, 18))

        self._label(
            brand,
            "TTCA",
            size=30,
            color=self.ACCENT,
            bold=True,
        ).pack(anchor="w")
        self._label(
            brand,
            "TikTok Clip Automation",
            size=12,
            color=self.MUTED,
        ).pack(anchor="w", pady=(0, 2))
        self._label(
            brand,
            f"v{APP_VERSION}",
            size=10,
            color=self.MUTED,
        ).pack(anchor="w")

        self.nav_buttons = {}
        nav = [
            ("dashboard", "⌂", "Inicio"),
            ("setup", "⚙", "Configuración"),
            ("pipeline", "⚡", "Pipeline"),
            ("tiktok", "▶", "TikTok"),
            ("activity", "≡", "Actividad"),
        ]
        for page_id, icon, label in nav:
            btn = self.ctk.CTkButton(
                self.sidebar,
                text=f"{icon}   {label}",
                anchor="w",
                height=44,
                corner_radius=10,
                fg_color="transparent",
                hover_color=self.CARD_ALT,
                text_color=self.TEXT,
                font=self.ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                command=lambda pid=page_id: self._show_page(pid),
            )
            btn.pack(fill="x", padx=14, pady=4)
            self.nav_buttons[page_id] = btn

        self.sidebar_spacer = self.ctk.CTkFrame(
            self.sidebar, fg_color="transparent"
        )
        self.sidebar_spacer.pack(fill="both", expand=True)

        self._label(
            self.sidebar,
            "Procesamiento",
            size=11,
            color=self.MUTED,
        ).pack(anchor="w", padx=18, pady=(0, 3))
        self._label(
            self.sidebar,
            "FFmpeg · prioridad muy baja",
            size=11,
            color=self.GREEN,
        ).pack(anchor="w", padx=18, pady=(0, 20))

        header = self.ctk.CTkFrame(content, fg_color=self.BG, height=72, corner_radius=0)
        header.pack(fill="x", padx=28, pady=(18, 0))
        header.pack_propagate(False)

        left = self.ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", fill="y")

        self.header_title = self._label(
            left, "Inicio", size=25, bold=True
        )
        self.header_title.pack(anchor="w")
        self.header_subtitle = self._label(
            left, "Todo listo para automatizar.",
            size=11, color=self.MUTED
        )
        self.header_subtitle.pack(anchor="w")

        status_box = self.ctk.CTkFrame(header, fg_color=self.CARD, corner_radius=12)
        status_box.pack(side="right", padx=0, pady=4)

        self.status_dot = self._label(
            status_box,
            "●",
            size=17,
            color=self.RED,
            bold=True,
        )
        self.status_dot.pack(side="left", padx=(13, 4))
        self.header_status = self._label(
            status_box, "Detenido", size=12, bold=True
        )
        self.header_status.pack(side="left", padx=(0, 13))

        self.pages_container = self.ctk.CTkFrame(
            content, fg_color=self.BG, corner_radius=0
        )
        self.pages_container.pack(fill="both", expand=True, padx=28, pady=(8, 22))

        self.pages = {}
        for page_id in ("dashboard", "setup", "pipeline", "tiktok", "activity"):
            page = self.ctk.CTkFrame(
                self.pages_container,
                fg_color=self.BG,
                corner_radius=0,
            )
            self.pages[page_id] = page

        self._build_dashboard(self.pages["dashboard"])
        self._build_setup(self.pages["setup"])
        self._build_pipeline(self.pages["pipeline"])
        self._build_tiktok(self.pages["tiktok"])
        self._build_activity(self.pages["activity"])

    def _show_page(self, page_id):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[page_id].pack(fill="both", expand=True)
        self.current_page = page_id

        labels = {
            "dashboard": ("Inicio", "Resumen del sistema y estado del pipeline."),
            "setup": ("Configuración", "Configuración guiada: sin tocar archivos a mano."),
            "pipeline": ("Pipeline", "Descarga, IA, Whisper y render, todo supervisado."),
            "tiktok": ("TikTok", "Publicación automática en cuanto termina cada Reel."),
            "activity": ("Actividad", "Logs en vivo y diagnóstico."),
        }
        title, subtitle = labels[page_id]
        self.header_title.configure(text=title)
        self.header_subtitle.configure(text=subtitle)

        for key, btn in self.nav_buttons.items():
            btn.configure(
                fg_color=self.ACCENT if key == page_id else "transparent"
            )

    # --------------------------------------------------------
    # Dashboard
    # --------------------------------------------------------

    def _stat_card(self, parent, title, variable, icon):
        card = self._frame(parent, fg_color=self.CARD)
        card.pack(side="left", fill="both", expand=True, padx=5)
        top = self.ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 0))
        self._label(top, icon, size=17, color=self.ACCENT, bold=True).pack(side="left")
        self._label(top, title, size=11, color=self.MUTED, bold=True).pack(
            side="left", padx=8
        )
        self._label(card, variable.get(), size=27, bold=True).pack(
            anchor="w", padx=16, pady=(7, 14)
        )
        # Keep a direct reference for updates.
        return card

    def _build_dashboard(self, parent):
        hero = self._frame(parent, fg_color=self.CARD)
        hero.pack(fill="x", pady=(4, 15))

        left = self.ctk.CTkFrame(hero, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=22, pady=22)

        self._label(
            left,
            "Automatización activa",
            size=12,
            color=self.ACCENT,
            bold=True,
        ).pack(anchor="w")
        self.activity_big = self._label(
            left,
            "Esperando un nuevo clip de Kick…",
            size=24,
            bold=True,
            wraplength=650,
            justify="left",
        )
        self.activity_big.pack(anchor="w", pady=(8, 4))
        self._label(
            left,
            "El programa detecta, descarga, edita, subtitula y publica sin intervención.",
            size=12,
            color=self.MUTED,
            wraplength=680,
            justify="left",
        ).pack(anchor="w")

        right = self.ctk.CTkFrame(hero, fg_color="transparent", width=280)
        right.pack(side="right", padx=22, pady=22)
        right.pack_propagate(False)

        self._label(right, "ETAPA ACTUAL", size=10, color=self.MUTED, bold=True).pack(
            anchor="w"
        )
        self.dashboard_stage = self._label(
            right, "Esperando", size=17, color=self.TEXT, bold=True
        )
        self.dashboard_stage.pack(anchor="w", pady=(6, 12))

        self.activity_progress = self.ctk.CTkProgressBar(
            right,
            height=8,
            corner_radius=6,
            fg_color=self.CARD_ALT,
            progress_color=self.ACCENT,
            mode="indeterminate",
        )
        self.activity_progress.pack(fill="x")
        self.activity_progress.stop()

        self._label(right, "TIEMPO ACTIVO", size=10, color=self.MUTED, bold=True).pack(
            anchor="w", pady=(18, 0)
        )
        self._label(right, self.uptime_var, size=17, bold=True).pack(anchor="w", pady=(5, 0))

        stats = self.ctk.CTkFrame(parent, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 15))
        self._build_stat_update_cards(stats)

        lower = self.ctk.CTkFrame(parent, fg_color="transparent")
        lower.pack(fill="both", expand=True)

        status_card = self._frame(lower, fg_color=self.CARD)
        status_card.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self._label(status_card, "Estado del sistema", size=15, bold=True).pack(
            anchor="w", padx=18, pady=(16, 10)
        )

        self.system_rows = {}
        for key, label in [
            ("watcher", "Watcher Kick"),
            ("download", "Descargas"),
            ("processor", "Procesamiento"),
            ("tiktok", "TikTok"),
            ("ffmpeg", "FFmpeg"),
        ]:
            row = self.ctk.CTkFrame(status_card, fg_color=self.CARD_ALT, corner_radius=9)
            row.pack(fill="x", padx=16, pady=5)
            self._label(row, label, size=11, color=self.MUTED).pack(
                side="left", padx=12, pady=9
            )
            value = self._label(row, "—", size=11, bold=True)
            value.pack(side="right", padx=12)
            self.system_rows[key] = value

        actions = self._frame(lower, fg_color=self.CARD)
        actions.pack(side="right", fill="both", expand=True, padx=(8, 0))
        self._label(actions, "Acciones rápidas", size=15, bold=True).pack(
            anchor="w", padx=18, pady=(16, 10)
        )

        action_wrap = self.ctk.CTkFrame(actions, fg_color="transparent")
        action_wrap.pack(fill="x", padx=16)

        self.start_button = self._button(
            action_wrap,
            "▶  Iniciar automatización",
            self.start_pipeline,
            width=240,
            primary=True,
        )
        self.start_button.pack(fill="x", pady=4)

        self.stop_button = self._button(
            action_wrap,
            "■  Detener",
            self.stop_pipeline,
            width=240,
        )
        self.stop_button.pack(fill="x", pady=4)
        self.stop_button.configure(state="disabled")

        self._button(
            action_wrap,
            "↻  Procesar clips existentes",
            self.process_existing_async,
            width=240,
        ).pack(fill="x", pady=4)

        setup_hint = self._frame(actions, fg_color=self.CARD_ALT)
        setup_hint.pack(fill="x", padx=16, pady=(15, 16))
        self._label(
            setup_hint,
            "¿Es la primera vez?",
            size=11,
            color=self.YELLOW,
            bold=True,
        ).pack(anchor="w", padx=12, pady=(10, 2))
        self._label(
            setup_hint,
            "Configurá NVIDIA y las cookies de TikTok desde Configuración.",
            size=11,
            color=self.MUTED,
            wraplength=350,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _build_stat_update_cards(self, parent):
        self._make_number_card(
            parent, "Descargas pendientes", self.download_queue_var, "↓"
        )
        self._make_number_card(
            parent, "Procesando", self.process_queue_var, "⚡"
        )
        self._make_number_card(
            parent, "TikToks en cola", self.tiktok_queue_var, "▶"
        )
        self._make_number_card(
            parent, "Fallos", self.failed_var, "!"
        )

    def _make_number_card(self, parent, title, variable, icon):
        card = self._frame(parent, fg_color=self.CARD)
        card.pack(side="left", fill="both", expand=True, padx=5)
        self._label(card, f"{icon}  {title}", size=10, color=self.MUTED, bold=True).pack(
            anchor="w", padx=15, pady=(13, 0)
        )
        label = self._label(card, variable.get(), size=25, bold=True)
        label.pack(anchor="w", padx=15, pady=(2, 13))
        variable.trace_add(
            "write",
            lambda *_args, target=label, var=variable: target.configure(text=var.get())
        )

    # --------------------------------------------------------
    # Setup
    # --------------------------------------------------------

    def _build_setup(self, parent):
        scroll = self.ctk.CTkScrollableFrame(
            parent,
            fg_color=self.BG,
            corner_radius=0,
        )
        scroll.pack(fill="both", expand=True)

        self._setup_card(
            scroll,
            "1",
            "Canal de Kick",
            "Escribí el nombre del canal que querés monitorear.",
            self._make_entry_row,
            {"key": "KICK_CHANNEL", "placeholder": "ej. eskrotos"},
        )

        self._setup_card(
            scroll,
            "2",
            "NVIDIA API Key",
            "La clave se guarda solamente en el .env local del programa.",
            self._make_api_row,
            {},
        )

        self._setup_card(
            scroll,
            "3",
            "Cookies de TikTok",
            "Exportá tus cookies en formato Netscape cookies.txt y seleccioná el archivo.",
            self._make_cookie_row,
            {},
        )

        tiktok_card = self._frame(scroll, fg_color=self.CARD)
        tiktok_card.pack(fill="x", pady=(10, 10), padx=4)
        self._label(tiktok_card, "4", size=14, color=self.ACCENT, bold=True).pack(
            side="left", padx=(18, 10), pady=17
        )
        body = self.ctk.CTkFrame(tiktok_card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=(0, 18), pady=14)
        self._label(body, "Publicación TikTok", size=15, bold=True).pack(anchor="w")
        self._label(
            body,
            "Publica automáticamente en cuanto termina cada Reel. No hay horarios, franjas ni límites artificiales.",
            size=11,
            color=self.MUTED,
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(3, 10))
        self.auto_upload_switch = self.ctk.CTkSwitch(
            body,
            text="Activar publicación automática",
            variable=self.auto_upload_var,
            onvalue=True,
            offvalue=False,
            progress_color=self.ACCENT,
            button_color=self.ACCENT,
            button_hover_color=self.ACCENT_HOVER,
        )
        self.auto_upload_switch.pack(anchor="w", pady=(0, 6))

        self.headless_switch = self.ctk.CTkSwitch(
            body,
            text="Usar navegador TikTok sin ventana visible",
            variable=self.headless_var,
            onvalue=True,
            offvalue=False,
            progress_color=self.ACCENT,
            button_color=self.ACCENT,
            button_hover_color=self.ACCENT_HOVER,
        )
        self.headless_switch.pack(anchor="w", pady=3)

        self._setup_card(
            scroll,
            "5",
            "Rendimiento",
            "FFmpeg se ejecuta con prioridad muy baja y con menos hilos para que el PC del streamer siga usable.",
            self._make_performance_row,
            {},
        )

        self._setup_card(
            scroll,
            "6",
            "Rutas y herramientas",
            "Las rutas pueden ser absolutas o relativas a la carpeta de TTCA.",
            self._make_paths_form,
            {},
        )

        save = self._frame(scroll, fg_color=self.CARD)
        save.pack(fill="x", pady=10, padx=4)
        self._button(save, "💾 Guardar configuración", self.save_settings, width=210, primary=True).pack(
            side="left", padx=16, pady=14
        )
        self._button(save, "↻ Recargar", self._refresh_config_vars, width=120).pack(
            side="left", padx=4, pady=14
        )
        self.setup_status = self._label(save, "Configuración sin guardar.", size=11, color=self.MUTED)
        self.setup_status.pack(side="left", padx=18)

    def _setup_card(self, parent, number, title, description, builder, kwargs):
        card = self._frame(parent, fg_color=self.CARD)
        card.pack(fill="x", pady=6, padx=4)
        self._label(card, number, size=14, color=self.ACCENT, bold=True).pack(
            side="left", padx=(18, 10), pady=16
        )
        body = self.ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=(0, 18), pady=14)
        self._label(body, title, size=15, bold=True).pack(anchor="w")
        self._label(
            body,
            description,
            size=11,
            color=self.MUTED,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(3, 9))
        builder(body, **kwargs)

    def _ensure_var(self, key, value=""):
        if key not in self.entry_vars:
            self.entry_vars[key] = tk.StringVar()
        return self.entry_vars[key]

    def _make_entry_row(self, parent, key, placeholder=""):
        row = self.ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        var = self._ensure_var(key)
        self.kick_entry = self.ctk.CTkEntry(
            row,
            textvariable=var,
            height=38,
            placeholder_text=placeholder,
        )
        self.kick_entry.pack(side="left", fill="x", expand=True)
        return row

    def _make_api_row(self, parent):
        row = self.ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        var = self._ensure_var("NVIDIA_API_KEY")
        self.api_entry = self.ctk.CTkEntry(
            row,
            textvariable=var,
            height=38,
            show="*",
        )
        self.api_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(row, "Mostrar", self._toggle_api_visibility, width=95).pack(
            side="left", padx=4
        )
        self._button(
            row,
            "🔑 Obtener API Key",
            lambda: self._open_url(self.NVIDIA_KEY_URL),
            width=160,
        ).pack(side="left", padx=4)
        self._button(
            row,
            "Modelos",
            lambda: self._open_url(self.NVIDIA_MODELS_URL),
            width=100,
        ).pack(side="left", padx=4)

    def _make_cookie_row(self, parent):
        row = self.ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        var = self._ensure_var("TIKTOK_COOKIES_FILE")
        self.cookies_entry = self.ctk.CTkEntry(
            row,
            textvariable=var,
            height=38,
        )
        self.cookies_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._button(row, "Seleccionar", self._choose_cookie_file, width=110).pack(
            side="left", padx=4
        )
        self._button(
            row,
            "🍪 Extensión",
            lambda: self._open_url(self.COOKIES_EXTENSION_URL),
            width=110,
        ).pack(side="left", padx=4)
        self._button(
            row,
            "TikTok Studio",
            lambda: self._open_url(self.TIKTOK_STUDIO_URL),
            width=120,
        ).pack(side="left", padx=4
        )

        self.cookie_status = self._label(parent, "No comprobado.", size=10, color=self.MUTED)
        self.cookie_status.pack(anchor="w", pady=(7, 0))

    def _make_performance_row(self, parent):
        row = self.ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        self._label(row, "Prioridad FFmpeg", size=11, color=self.MUTED, bold=True).pack(
            side="left", padx=(0, 10)
        )
        self.ffmpeg_menu = self.ctk.CTkOptionMenu(
            row,
            values=["Normal", "Baja", "Muy baja"],
            variable=tk.StringVar(value="Muy baja"),
            width=145,
            height=36,
            fg_color=self.CARD_ALT,
            button_color=self.ACCENT,
            button_hover_color=self.ACCENT_HOVER,
        )
        self.ffmpeg_menu.pack(side="left")

        # Use an independent env variable through a trace-free helper.
        self.ffmpeg_ui_value = tk.StringVar(value="Muy baja")

        note = self._label(
            row,
            "  Recomendado para streaming: Muy baja (Idle) + CPU limitada.",
            size=10,
            color=self.GREEN,
        )
        note.pack(side="left", padx=12)

        self.ffmpeg_menu.configure(command=self._on_ffmpeg_menu_change)

    def _make_paths_form(self, parent):
        fields = [
            ("CLIPS_DIR", "Clips"),
            ("OUTPUT_DIR", "Reels"),
            ("USED_DIR", "Usados"),
            ("DIVIDER_PATH", "Divisor"),
            ("FONT_PATH", "Fuente"),
            ("WHISPER_CPP_EXE", "Whisper CLI"),
            ("WHISPER_CPP_MODEL", "Whisper modelo"),
        ]
        self.path_widgets = {}
        for key, label in fields:
            row = self.ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=3)
            self._label(row, label, size=10, color=self.MUTED, width=120, anchor="w").pack(
                side="left"
            )
            var = self._ensure_var(key)
            entry = self.ctk.CTkEntry(row, textvariable=var, height=34)
            entry.pack(side="left", fill="x", expand=True, padx=(0, 7))
            self.path_widgets[key] = entry

            command = self._choose_directory if key in {"CLIPS_DIR", "OUTPUT_DIR", "USED_DIR"} else self._choose_file_for_key
            self._button(
                row,
                "Examinar",
                lambda k=key, cmd=command: cmd(k),
                width=88,
            ).pack(side="left")

    # --------------------------------------------------------
    # Pipeline
    # --------------------------------------------------------

    def _build_pipeline(self, parent):
        top = self._frame(parent, fg_color=self.CARD)
        top.pack(fill="x", pady=(4, 10))
        self._label(top, "Control del pipeline", size=15, bold=True).pack(
            side="left", padx=18, pady=15
        )
        self.pipeline_status_label = self._label(
            top, "Detenido", size=12, color=self.RED, bold=True
        )
        self.pipeline_status_label.pack(side="right", padx=18)

        controls = self.ctk.CTkFrame(parent, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 10))
        self._button(
            controls, "▶ Iniciar", self.start_pipeline, width=130, primary=True
        ).pack(side="left", padx=(0, 6))
        self._button(
            controls, "■ Detener", self.stop_pipeline, width=120
        ).pack(side="left", padx=6)
        self._button(
            controls,
            "↻ Encolar existentes",
            self.process_existing_async,
            width=160,
        ).pack(side="left", padx=6)

        queue_card = self._frame(parent, fg_color=self.CARD)
        queue_card.pack(fill="both", expand=True)

        self._label(
            queue_card,
            "Colas en tiempo real",
            size=15,
            bold=True,
        ).pack(anchor="w", padx=18, pady=(16, 5))
        self._label(
            queue_card,
            "La descarga puede adelantarse, pero IA/Whisper/FFmpeg usan un único worker secuencial.",
            size=10,
            color=self.MUTED,
        ).pack(anchor="w", padx=18, pady=(0, 12))

        grid = self.ctk.CTkFrame(queue_card, fg_color="transparent")
        grid.pack(fill="x", padx=14)

        self.pipeline_cards = {}
        entries = [
            ("download", "Descargas pendientes", self.download_queue_var, "↓"),
            ("process", "Procesamiento pendientes", self.process_queue_var, "⚡"),
            ("tiktok", "TikToks pendientes", self.tiktok_queue_var, "▶"),
            ("failed", "Fallos", self.failed_var, "!"),
        ]
        for idx, (key, title, var, icon) in enumerate(entries):
            card = self._frame(grid, fg_color=self.CARD_ALT)
            card.grid(row=0, column=idx, sticky="ew", padx=4)
            grid.columnconfigure(idx, weight=1)
            self._label(card, f"{icon} {title}", size=10, color=self.MUTED, bold=True).pack(
                anchor="w", padx=13, pady=(11, 0)
            )
            self._label(card, var.get(), size=23, bold=True).pack(
                anchor="w", padx=13, pady=(2, 11)
            )
            self.pipeline_cards[key] = card

        status = self._frame(queue_card, fg_color=self.CARD_ALT)
        status.pack(fill="x", padx=18, pady=18)
        self._label(status, "Actividad", size=11, color=self.MUTED, bold=True).pack(
            anchor="w", padx=13, pady=(10, 2)
        )
        self.pipeline_activity_label = self._label(
            status,
            self.activity_var.get(),
            size=12,
            wraplength=950,
            justify="left",
        )
        self.pipeline_activity_label.pack(anchor="w", padx=13, pady=(0, 10))

    # --------------------------------------------------------
    # TikTok
    # --------------------------------------------------------

    def _build_tiktok(self, parent):
        intro = self._frame(parent, fg_color=self.CARD)
        intro.pack(fill="x", pady=(4, 10))
        self._label(intro, "Publicación automática", size=17, bold=True).pack(
            anchor="w", padx=18, pady=(15, 2)
        )
        self._label(
            intro,
            "Cuando termina un Reel, entra directamente a TikTok. No existen horarios ni esperas artificiales.",
            size=11,
            color=self.MUTED,
        ).pack(anchor="w", padx=18, pady=(0, 14))

        row = self.ctk.CTkFrame(intro, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 14))
        self._label(row, "Publicación:", size=11, color=self.MUTED, bold=True).pack(side="left")
        self.tiktok_status_big = self._label(row, "Desactivada", size=12, color=self.RED, bold=True)
        self.tiktok_status_big.pack(side="left", padx=7)

        queue = self._frame(parent, fg_color=self.CARD)
        queue.pack(fill="both", expand=True)
        top = self.ctk.CTkFrame(queue, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(14, 8))
        self._label(top, "Cola de publicaciones", size=15, bold=True).pack(side="left")
        self._button(
            top,
            "🔁 Reintentar fallidos",
            self.retry_failed_tiktok,
            width=165,
        ).pack(side="right")

        self.tiktok_text = self.ctk.CTkTextbox(
            queue,
            fg_color=self.CARD_ALT,
            text_color=self.TEXT,
            border_width=0,
            corner_radius=10,
            font=self.ctk.CTkFont(family="Consolas", size=11),
        )
        self.tiktok_text.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.tiktok_text.configure(state="disabled")

    # --------------------------------------------------------
    # Activity
    # --------------------------------------------------------

    def _build_activity(self, parent):
        log_card = self._frame(parent, fg_color=self.CARD)
        log_card.pack(fill="both", expand=True, pady=(4, 0))

        head = self.ctk.CTkFrame(log_card, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 8))
        self._label(head, "Logs en vivo", size=15, bold=True).pack(side="left")
        self._button(head, "Limpiar", self.clear_logs, width=90).pack(side="right")

        self.log_text = self.ctk.CTkTextbox(
            log_card,
            fg_color="#0A0E15",
            text_color=self.TEXT,
            border_width=0,
            corner_radius=10,
            font=self.ctk.CTkFont(family="Consolas", size=10),
        )
        self.log_text.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------
    # Config helpers
    # --------------------------------------------------------

    def _refresh_config_vars(self):
        reload_config_from_env()
        keys = [
            "KICK_CHANNEL",
            "KICK_POLL_SECONDS",
            "KICK_ERROR_BACKOFF_SECONDS",
            "NVIDIA_API_KEY",
            "CLIPS_DIR",
            "OUTPUT_DIR",
            "USED_DIR",
            "DIVIDER_PATH",
            "FONT_PATH",
            "WHISPER_CPP_EXE",
            "WHISPER_CPP_MODEL",
            "TIKTOK_COOKIES_FILE",
            "TIKTOK_AUTO_UPLOAD",
            "TIKTOK_HEADLESS",
            "TIKTOK_CAPTION_TEMPLATE",
            "FACE_SERVER_ERROR_RETRIES",
            "SAME_MOMENT_COOLDOWN_SECONDS",
            "PROCESS_QUEUE_COOLDOWN_SECONDS",
            "FFMPEG_PRIORITY",
            "FFMPEG_THREADS",
            "TIKTOK_UPLOAD_RETRIES",
            "TIKTOK_PROCESSING_TIMEOUT_SECONDS",
            "TIKTOK_CONFIRM_TIMEOUT_SECONDS",
        ]
        for key in keys:
            var = self._ensure_var(key)
            var.set(str(os.getenv(key, "")))

        self.auto_upload_var.set(env_bool("TIKTOK_AUTO_UPLOAD", True))
        self.headless_var.set(env_bool("TIKTOK_HEADLESS", True))

        priority = env_value("FFMPEG_PRIORITY", "idle").strip().lower()
        if priority == "normal":
            label = "Normal"
        elif priority == "below_normal":
            label = "Baja"
        else:
            label = "Muy baja"
        if hasattr(self, "ffmpeg_menu"):
            self.ffmpeg_menu.set(label)
        self.ffmpeg_ui_value.set(label)
        self.ffmpeg_var.set(f"{label} · {priority}")

        if hasattr(self, "cookie_status"):
            self._refresh_cookie_status()
        if hasattr(self, "setup_status"):
            self.setup_status.configure(text="Configuración recargada.", text_color=self.GREEN)

    def save_settings(self):
        try:
            ENV_FILE.parent.mkdir(parents=True, exist_ok=True)

            values = {
                "KICK_CHANNEL": self.entry_vars.get("KICK_CHANNEL", tk.StringVar()).get().strip(),
                "KICK_POLL_SECONDS": self.entry_vars.get("KICK_POLL_SECONDS", tk.StringVar(value="1")).get().strip(),
                "KICK_ERROR_BACKOFF_SECONDS": self.entry_vars.get("KICK_ERROR_BACKOFF_SECONDS", tk.StringVar(value="5")).get().strip(),
                "NVIDIA_API_KEY": self.entry_vars.get("NVIDIA_API_KEY", tk.StringVar()).get().strip(),
                "CLIPS_DIR": self.entry_vars.get("CLIPS_DIR", tk.StringVar()).get().strip(),
                "OUTPUT_DIR": self.entry_vars.get("OUTPUT_DIR", tk.StringVar()).get().strip(),
                "USED_DIR": self.entry_vars.get("USED_DIR", tk.StringVar()).get().strip(),
                "DIVIDER_PATH": self.entry_vars.get("DIVIDER_PATH", tk.StringVar()).get().strip(),
                "FONT_PATH": self.entry_vars.get("FONT_PATH", tk.StringVar()).get().strip(),
                "WHISPER_CPP_EXE": self.entry_vars.get("WHISPER_CPP_EXE", tk.StringVar()).get().strip(),
                "WHISPER_CPP_MODEL": self.entry_vars.get("WHISPER_CPP_MODEL", tk.StringVar()).get().strip(),
                "TIKTOK_COOKIES_FILE": self.entry_vars.get("TIKTOK_COOKIES_FILE", tk.StringVar()).get().strip(),
                "TIKTOK_AUTO_UPLOAD": "true" if self.auto_upload_var.get() else "false",
                "TIKTOK_HEADLESS": "true" if self.headless_var.get() else "false",
                "TIKTOK_CAPTION_TEMPLATE": self.entry_vars.get("TIKTOK_CAPTION_TEMPLATE", tk.StringVar(value="{title} #tiktok #kick")).get().strip(),
                "FACE_SERVER_ERROR_RETRIES": self.entry_vars.get("FACE_SERVER_ERROR_RETRIES", tk.StringVar(value="0")).get().strip(),
                "SAME_MOMENT_COOLDOWN_SECONDS": self.entry_vars.get("SAME_MOMENT_COOLDOWN_SECONDS", tk.StringVar(value="45")).get().strip(),
                "PROCESS_QUEUE_COOLDOWN_SECONDS": self.entry_vars.get("PROCESS_QUEUE_COOLDOWN_SECONDS", tk.StringVar(value="5")).get().strip(),
                "FFMPEG_PRIORITY": self.ffmpeg_ui_to_env(),
                "FFMPEG_THREADS": self.entry_vars.get("FFMPEG_THREADS", tk.StringVar(value=str(max(1, (os.cpu_count() or 4)-2)))).get().strip(),
                "TIKTOK_UPLOAD_RETRIES": self.entry_vars.get("TIKTOK_UPLOAD_RETRIES", tk.StringVar(value="3")).get().strip(),
                "TIKTOK_PROCESSING_TIMEOUT_SECONDS": self.entry_vars.get("TIKTOK_PROCESSING_TIMEOUT_SECONDS", tk.StringVar(value="180")).get().strip(),
                "TIKTOK_CONFIRM_TIMEOUT_SECONDS": self.entry_vars.get("TIKTOK_CONFIRM_TIMEOUT_SECONDS", tk.StringVar(value="90")).get().strip(),
            }

            for key, value in values.items():
                set_key(str(ENV_FILE), key, value, quote_mode="auto")

            load_dotenv(ENV_FILE, override=True)
            reload_config_from_env()
            ensure_dirs()

            self.setup_status.configure(text="✓ Configuración guardada.", text_color=self.GREEN)
            self.log("✅ Configuración guardada desde la interfaz.")
            self._refresh_cookie_status()

            if self.running:
                self.log("ℹ️  Los cambios de configuración se aplican al próximo reinicio del pipeline.")
        except Exception as exc:
            messagebox.showerror(self.TITLE, f"No se pudo guardar:\n{exc}")

    def ffmpeg_ui_to_env(self):
        value = self.ffmpeg_menu.get() if hasattr(self, "ffmpeg_menu") else "Muy baja"
        return {
            "Normal": "normal",
            "Baja": "below_normal",
            "Muy baja": "idle",
        }.get(value, "idle")

    def _on_ffmpeg_menu_change(self, value):
        self.ffmpeg_ui_value.set(value)
        self.ffmpeg_var.set(f"{value} · {self.ffmpeg_ui_to_env()}")

    def _toggle_api_visibility(self):
        self.key_visible = not self.key_visible
        self.api_entry.configure(show="" if self.key_visible else "*")

    def _refresh_cookie_status(self):
        try:
            path = Path(self.entry_vars.get("TIKTOK_COOKIES_FILE", tk.StringVar()).get().strip())
            if path.exists():
                self.cookie_status.configure(
                    text=f"✓ Cookies encontradas · {path}",
                    text_color=self.GREEN,
                )
            else:
                self.cookie_status.configure(
                    text="⚠ No se encontró el archivo de cookies.",
                    text_color=self.YELLOW,
                )
        except Exception:
            pass

    def _choose_cookie_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar cookies.txt",
            filetypes=[
                ("cookies.txt", "*.txt"),
                ("Archivos de texto", "*.txt"),
                ("Todos", "*.*"),
            ],
        )
        if path:
            self._ensure_var("TIKTOK_COOKIES_FILE").set(path)
            self._refresh_cookie_status()

    def _choose_directory(self, key):
        current = self.entry_vars.get(key)
        initial = current.get() if current else str(APP_DIR)
        path = filedialog.askdirectory(
            title=f"Seleccionar {key}",
            initialdir=initial if Path(initial).exists() else str(APP_DIR),
        )
        if path:
            self._ensure_var(key).set(path)

    def _choose_file_for_key(self, key):
        current = self.entry_vars.get(key)
        initial = current.get() if current else str(APP_DIR)
        initial_dir = str(Path(initial).parent) if Path(initial).parent.exists() else str(APP_DIR)
        path = filedialog.askopenfilename(
            title=f"Seleccionar {key}",
            initialdir=initial_dir,
        )
        if path:
            self._ensure_var(key).set(path)

    def _open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception as exc:
            self.log(f"⚠️  No se pudo abrir el navegador: {exc}")

    # --------------------------------------------------------
    # Pipeline control
    # --------------------------------------------------------

    def _install_output_redirect(self):
        global _GUI_OLD_STDOUT, _GUI_OLD_STDERR
        _GUI_OLD_STDOUT = sys.stdout
        _GUI_OLD_STDERR = sys.stderr
        sys.stdout = QueueTextWriter(_GUI_OLD_STDOUT, self.output_queue)
        sys.stderr = QueueTextWriter(_GUI_OLD_STDERR, self.output_queue)

    def _restore_output_redirect(self):
        global _GUI_OLD_STDOUT, _GUI_OLD_STDERR
        if "_GUI_OLD_STDOUT" in globals():
            sys.stdout = _GUI_OLD_STDOUT
        if "_GUI_OLD_STDERR" in globals():
            sys.stderr = _GUI_OLD_STDERR

    def log(self, message):
        self.output_queue.put(str(message) + "\n")

    def _drain_output_queue(self):
        while True:
            try:
                chunk = self.output_queue.get_nowait()
            except queue.Empty:
                break

            if hasattr(self, "log_text"):
                self.log_text.configure(state="normal")
                self.log_text.insert("end", chunk)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")

            lower = chunk.lower()
            if "descargando" in lower or "download" in lower:
                self.stage_var.set("Descargando")
            elif "facecam" in lower or "kimi" in lower or "diffusiongemma" in lower:
                self.stage_var.set("Analizando facecam")
            elif "whisper" in lower or "transcrib" in lower:
                self.stage_var.set("Transcribiendo")
            elif "omni" in lower:
                self.stage_var.set("Seleccionando fragmento")
            elif "renderizando" in lower or "ffmpeg" in lower:
                self.stage_var.set("Renderizando")
            elif "tiktok: subiendo" in lower:
                self.stage_var.set("Publicando en TikTok")
            elif "clip terminado" in lower or "publicado" in lower:
                self.stage_var.set("Finalizado")

            self.activity_var.set(chunk.strip()[-240:] or self.activity_var.get())
            if hasattr(self, "activity_big"):
                self.activity_big.configure(text=self.activity_var.get())
            if hasattr(self, "pipeline_activity_label"):
                self.pipeline_activity_label.configure(text=self.activity_var.get())

        try:
            self.root.after(120, self._drain_output_queue)
        except Exception:
            pass

    def start_pipeline(self):
        global PIPELINE_ON_PROCESSED

        if self.watcher_thread and self.watcher_thread.is_alive():
            return

        try:
            reload_config_from_env()
            if not check_dependencies():
                messagebox.showerror(
                    self.TITLE,
                    "Faltan dependencias. Revisá Actividad para ver el detalle.",
                )
                return

            ensure_dirs()
            problems = validate_runtime_config()
            if problems:
                self._show_config_problems(problems)
                return

            self.stop_event.clear()

            self.tiktok_manager = TikTokUploadManager(log_callback=self.log)
            self.tiktok_manager.discover_existing_reels()
            self.tiktok_manager.start()

            PIPELINE_ON_PROCESSED = self._pipeline_processed

            self.watcher_thread = threading.Thread(
                target=self._watcher_worker,
                name="KickWatcher",
                daemon=True,
            )
            self.watcher_thread.start()

            self.running = True
            self.start_time = time.time()
            self.status_var.set("Ejecutando")
            self.header_status.configure(text="Ejecutando")
            self.pipeline_status_label.configure(text="Ejecutando", text_color=self.GREEN)
            self.watcher_var.set(f"Activo · /{KICK_CHANNEL}")
            self.tiktok_var.set(
                "Activo · publicación inmediata"
                if TIKTOK_AUTO_UPLOAD
                else "Desactivado"
            )
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.activity_progress.start()
            self.log("🚀 TTCA v0.2 iniciado.")
        except Exception as exc:
            self.log(f"❌ No se pudo iniciar el pipeline: {exc}")
            messagebox.showerror(self.TITLE, str(exc))

    def _show_config_problems(self, problems):
        text_value = "\n".join(f"• {p}" for p in problems)
        messagebox.showwarning(
            self.TITLE,
            "Revisá esta configuración antes de iniciar:\n\n" + text_value,
        )
        self._show_page("setup")

    def _watcher_worker(self):
        try:
            watch_kick_clips(
                stop_event=self.stop_event,
                on_processed=PIPELINE_ON_PROCESSED,
            )
        except Exception as exc:
            self.log(f"❌ Watcher terminó por error: {exc}")
        finally:
            self.root.after(0, self._pipeline_stopped_ui)

    def _pipeline_processed(self, output_path: Path, clip: dict):
        if not TIKTOK_AUTO_UPLOAD:
            return
        if self.tiktok_manager is None:
            self.tiktok_manager = TikTokUploadManager(log_callback=self.log)
            self.tiktok_manager.start()
        self.tiktok_manager.enqueue(output_path, clip)
        self.root.after(0, self._refresh_tiktok_queue)

    def stop_pipeline(self):
        self.stop_event.set()
        if self.tiktok_manager:
            self.tiktok_manager.stop()
        self.watcher_var.set("Deteniendo…")
        self.tiktok_var.set("Deteniendo…")
        self.log("⏹️  Deteniendo pipeline…")

    def _pipeline_stopped_ui(self):
        self.running = False
        self.status_var.set("Detenido")
        self.header_status.configure(text="Detenido")
        self.pipeline_status_label.configure(text="Detenido", text_color=self.RED)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.activity_progress.stop()
        self.watcher_var.set("Detenido")
        self.tiktok_var.set("Detenido")
        self.stage_var.set("Esperando")

    # --------------------------------------------------------
    # Existing clips / TikTok
    # --------------------------------------------------------

    def process_existing_async(self):
        threading.Thread(
            target=self._queue_existing_worker,
            name="ExistingClipsQueueWorker",
            daemon=True,
        ).start()

    def _queue_existing_worker(self):
        reload_config_from_env()
        clips = sorted(CLIPS_DIR.glob("*.mp4"))
        if not clips:
            self.log(f"📁 No hay clips en {CLIPS_DIR}")
            return

        if not check_dependencies():
            return

        ensure_dirs()

        if (
            PIPELINE_JOB_QUEUE is None
            or PIPELINE_PROCESSING_THREAD is None
            or not PIPELINE_PROCESSING_THREAD.is_alive()
        ):
            self.log(
                "⚠️  Iniciá el pipeline antes: los clips existentes "
                "usan la misma cola secuencial."
            )
            return

        if TIKTOK_AUTO_UPLOAD and self.tiktok_manager is None:
            self.tiktok_manager = TikTokUploadManager(log_callback=self.log)
            self.tiktok_manager.start()

        queued_count = 0
        for clip in clips:
            if self.stop_event.is_set():
                break

            clip_path = clip.resolve()
            clip_id = (
                "local_"
                + hashlib.sha1(str(clip_path).encode("utf-8")).hexdigest()[:20]
            )

            with PIPELINE_REGISTRY_LOCK:
                record = PIPELINE_REGISTRY.get(clip_id, {})
                status = record.get("status")

            if status in {"queued", "downloading", "downloaded", "processing", "processed"}:
                continue

            local_job = {
                "id": clip_id,
                "title": clip.stem,
                "created_at": datetime.fromtimestamp(
                    clip_path.stat().st_mtime
                ).isoformat(),
                "local_path": str(clip_path),
            }

            with PIPELINE_REGISTRY_LOCK:
                update_clip_registry(
                    PIPELINE_REGISTRY,
                    clip_id,
                    status="queued",
                    downloaded_path=str(clip_path),
                    last_attempt_at=time.time(),
                    last_error=None,
                )

            PIPELINE_JOB_QUEUE.put(local_job)
            queued_count += 1

        self.log(
            f"📥 {queued_count} clip(s) existente(s) agregados a la cola secuencial."
        )
        self.root.after(0, self._refresh_views)

    def retry_failed_tiktok(self):
        if self.tiktok_manager is None:
            self.tiktok_manager = TikTokUploadManager(log_callback=self.log)
            self.tiktok_manager.start()
        count = self.tiktok_manager.retry_failed()
        self._refresh_tiktok_queue()
        if count:
            self.log(f"🔁 Se reintentará(n) {count} publicación(es) fallida(s).")

    def _refresh_tiktok_queue(self):
        try:
            state = cargar_json(TIKTOK_UPLOAD_REGISTRY, {"items": {}})
            items = state.get("items", {})
            lines = []
            for item in sorted(
                items.values(),
                key=lambda x: x.get("created_at", 0),
                reverse=True,
            )[:80]:
                status = str(item.get("status", "?")).upper()
                name = Path(item.get("video_path", "")).name
                error = item.get("last_error")
                suffix = f"  ·  {error[:70]}" if error else ""
                lines.append(f"{status:10}  |  {name}{suffix}")

            self.tiktok_text.configure(state="normal")
            self.tiktok_text.delete("1.0", "end")
            self.tiktok_text.insert("end", "\n".join(lines) or "Cola vacía.")
            self.tiktok_text.configure(state="disabled")
        except Exception:
            pass

    # --------------------------------------------------------
    # Live dashboard
    # --------------------------------------------------------

    def _refresh_views(self):
        try:
            dq = PIPELINE_DOWNLOAD_QUEUE.qsize() if PIPELINE_DOWNLOAD_QUEUE else 0
            pq = PIPELINE_JOB_QUEUE.qsize() if PIPELINE_JOB_QUEUE else 0

            self.download_queue_var.set(str(dq))
            self.process_queue_var.set(str(pq))

            state = PIPELINE_REGISTRY or {}
            failed = sum(1 for item in state.values() if item.get("status") == "failed")
            self.failed_var.set(str(failed))

            tiktok_state = cargar_json(TIKTOK_UPLOAD_REGISTRY, {"items": {}})
            t_items = tiktok_state.get("items", {})
            t_pending = sum(
                1 for item in t_items.values()
                if item.get("status") in {"queued", "uploading"}
            )
            self.tiktok_queue_var.set(str(t_pending))

            if self.running:
                self.system_rows["watcher"].configure(text="● Activo", text_color=self.GREEN)
                self.system_rows["download"].configure(
                    text=f"{dq} pendientes",
                    text_color=self.BLUE if dq else self.MUTED,
                )
                self.system_rows["processor"].configure(
                    text=f"{pq} pendientes",
                    text_color=self.YELLOW if pq else self.GREEN,
                )
                self.system_rows["tiktok"].configure(
                    text="Publicación inmediata" if TIKTOK_AUTO_UPLOAD else "Desactivado",
                    text_color=self.GREEN if TIKTOK_AUTO_UPLOAD else self.MUTED,
                )
            else:
                self.system_rows["watcher"].configure(text="Detenido", text_color=self.RED)
                self.system_rows["download"].configure(text="Detenido", text_color=self.MUTED)
                self.system_rows["processor"].configure(text="Detenido", text_color=self.MUTED)
                self.system_rows["tiktok"].configure(text="Detenido", text_color=self.MUTED)

            self.system_rows["ffmpeg"].configure(
                text=self.ffmpeg_var.get(),
                text_color=self.GREEN if "idle" in self.ffmpeg_var.get().lower() else self.YELLOW,
            )

            self.tiktok_status_big.configure(
                text="Activa · inmediata" if TIKTOK_AUTO_UPLOAD else "Desactivada",
                text_color=self.GREEN if TIKTOK_AUTO_UPLOAD else self.RED,
            )
            self._refresh_tiktok_queue()
        except Exception:
            pass

        try:
            self.root.after(1000, self._refresh_views)
        except Exception:
            pass

    def _tick_ui(self):
        try:
            if self.running:
                elapsed = max(0, int(time.time() - (self.start_time or time.time())))
                self.uptime_var.set(
                    f"{elapsed//3600:02d}:{(elapsed%3600)//60:02d}:{elapsed%60:02d}"
                )
                self.activity_progress.start()

                self.pulse = not self.pulse
                self.status_dot.configure(
                    text="●" if self.pulse else "◉",
                    text_color=self.GREEN,
                )
                self.header_status.configure(text=self.status_var.get())
                self.dashboard_stage.configure(text=self.stage_var.get())
                self.ffmpeg_var.set(
                    f"{self.ffmpeg_menu.get() if hasattr(self, 'ffmpeg_menu') else 'Muy baja'} · "
                    f"{self.ffmpeg_ui_to_env()}"
                )
            else:
                self.status_dot.configure(text="●", text_color=self.RED)
                self.dashboard_stage.configure(text="Esperando")
            self.root.after(700, self._tick_ui)
        except Exception:
            pass

    def clear_logs(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def on_close(self):
        self.stop_event.set()
        if self.tiktok_manager:
            self.tiktok_manager.stop()
        try:
            self._restore_output_redirect()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass



def reload_config_from_env():
    # Releer el archivo por si fue editado fuera de la GUI.
    load_dotenv(ENV_FILE, override=True)

    global CLIPS_DIR, OUTPUT_DIR, USED_DIR, DIVIDER_PATH, FONT_PATH, CLIP_REGISTRY_FILE
    global TARGET_W, TARGET_H, DIVIDER_H
    global SUB_SIZE, SUB_Y_OFFSET, SUB_MAX_WORDS, SUB_COLOR, SUB_BORDER, SUB_BORDER_WIDTH
    global FACE_MAX_RETRIES_KIMI, FACE_FALLBACK_MODEL, FACE_KIMI_MODEL, FACE_KIMI_TIMEOUT, FACE_FALLBACK_TIMEOUT
    global FACE_SERVER_ERROR_RETRIES
    global WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE, WHISPER_BACKEND
    global WHISPER_CPP_EXE, WHISPER_CPP_MODEL, WHISPER_CPP_THREADS
    global NVIDIA_API_KEY, NVIDIA_API_URL, NVIDIA_TRIM_OMNI, NVIDIA_TRIM_OMNI_TIMEOUT
    global KICK_CHANNEL, KICK_POLL_SECONDS, KICK_ERROR_BACKOFF_SECONDS
    global DUPLICATE_WINDOW_SECONDS, DUPLICATE_PHASH_DISTANCE, DUPLICATE_DURATION_TOLERANCE
    global DUPLICATE_TITLE_WINDOW_SECONDS, DUPLICATE_THUMBNAIL_TIMEOUT
    global SAME_MOMENT_COOLDOWN_SECONDS, PROCESS_QUEUE_COOLDOWN_SECONDS
    global FAILED_RETRY_SECONDS, REGISTRY_MAX_ENTRIES, SKIP_EXISTING_OUTPUT, MIN_VALID_OUTPUT_BYTES
    global TIKTOK_COOKIES_FILE, TIKTOK_HEADLESS, TIKTOK_AUTO_UPLOAD
    global TIKTOK_UPLOAD_RETRIES, TIKTOK_UPLOAD_RETRY_DELAY_SECONDS, TIKTOK_MINIMIZED
    global TIKTOK_PROCESSING_TIMEOUT_SECONDS, TIKTOK_CONFIRM_TIMEOUT_SECONDS
    global TIKTOK_UPLOAD_CHECK_SECONDS, FFMPEG_PRIORITY, FFMPEG_THREADS
    global TIKTOK_CAPTION_TEMPLATE, TIKTOK_UPLOAD_REGISTRY

    CLIPS_DIR = resolve_path(env_value("CLIPS_DIR", "data/clips"))
    OUTPUT_DIR = resolve_path(env_value("OUTPUT_DIR", "data/reels"))
    USED_DIR = resolve_path(env_value("USED_DIR", "data/used"))
    DIVIDER_PATH = resolve_path(env_value("DIVIDER_PATH", "assets/divider.png"))
    FONT_PATH = resolve_path(env_value("FONT_PATH", "assets/TF2 build.ttf"))
    CLIP_REGISTRY_FILE = resolve_path(env_value("CLIP_REGISTRY_FILE", "data/seen_clips.json"))

    TARGET_W = env_int("TARGET_W", 1080)
    TARGET_H = env_int("TARGET_H", 1920)
    DIVIDER_H = env_int("DIVIDER_H", 160)

    SUB_SIZE = env_int("SUB_SIZE", 128)
    SUB_Y_OFFSET = env_int("SUB_Y_OFFSET", 180)
    SUB_MAX_WORDS = env_int("SUB_MAX_WORDS", 1)
    SUB_COLOR = env_value("SUB_COLOR", "&H00FFFFFF")
    SUB_BORDER = env_value("SUB_BORDER", "&H00000000")
    SUB_BORDER_WIDTH = env_int("SUB_BORDER_WIDTH", 12)

    FACE_MAX_RETRIES_KIMI = env_int("FACE_MAX_RETRIES_KIMI", 5)
    FACE_FALLBACK_MODEL = env_value("FACE_FALLBACK_MODEL", "google/diffusiongemma-26b-a4b-it")
    FACE_KIMI_MODEL = env_value("FACE_KIMI_MODEL", "moonshotai/kimi-k3")
    FACE_KIMI_TIMEOUT = env_int("FACE_KIMI_TIMEOUT", 130)
    FACE_FALLBACK_TIMEOUT = env_int("FACE_FALLBACK_TIMEOUT", 60)
    FACE_SERVER_ERROR_RETRIES = max(0, env_int("FACE_SERVER_ERROR_RETRIES", 0))

    WHISPER_MODEL = env_value("WHISPER_MODEL", "large-v3")
    WHISPER_DEVICE = env_value("WHISPER_DEVICE", "auto")
    WHISPER_COMPUTE = env_value("WHISPER_COMPUTE", "default")
    WHISPER_BACKEND = env_value("WHISPER_BACKEND", "auto")
    WHISPER_CPP_EXE = resolve_path(
        env_value("WHISPER_CPP_EXE", "tools/whisper.cpp/whisper-cli.exe")
    )
    WHISPER_CPP_MODEL = resolve_path(
        env_value("WHISPER_CPP_MODEL", "tools/whisper.cpp/models/ggml-large-v3.bin")
    )
    WHISPER_CPP_THREADS = env_int(
        "WHISPER_CPP_THREADS",
        max(2, min(8, os.cpu_count() or 8))
    )

    NVIDIA_API_KEY = env_value("NVIDIA_API_KEY", "")
    NVIDIA_API_URL = env_value(
        "NVIDIA_API_URL",
        "https://integrate.api.nvidia.com/v1/chat/completions",
    )
    NVIDIA_TRIM_OMNI = env_value(
        "NVIDIA_TRIM_OMNI",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    )
    NVIDIA_TRIM_OMNI_TIMEOUT = env_int("NVIDIA_TRIM_OMNI_TIMEOUT", 180)

    KICK_CHANNEL = env_value("KICK_CHANNEL", "eskrotos")
    KICK_POLL_SECONDS = max(1, env_int("KICK_POLL_SECONDS", 1))
    KICK_ERROR_BACKOFF_SECONDS = max(1, env_int("KICK_ERROR_BACKOFF_SECONDS", 5))

    DUPLICATE_WINDOW_SECONDS = env_int("DUPLICATE_WINDOW_SECONDS", 75)
    DUPLICATE_PHASH_DISTANCE = env_int("DUPLICATE_PHASH_DISTANCE", 10)
    DUPLICATE_DURATION_TOLERANCE = env_int("DUPLICATE_DURATION_TOLERANCE", 20)
    DUPLICATE_TITLE_WINDOW_SECONDS = env_int("DUPLICATE_TITLE_WINDOW_SECONDS", 30)
    DUPLICATE_THUMBNAIL_TIMEOUT = env_int("DUPLICATE_THUMBNAIL_TIMEOUT", 8)
    SAME_MOMENT_COOLDOWN_SECONDS = max(0, env_int("SAME_MOMENT_COOLDOWN_SECONDS", 45))
    PROCESS_QUEUE_COOLDOWN_SECONDS = max(0, env_int("PROCESS_QUEUE_COOLDOWN_SECONDS", 5))

    FAILED_RETRY_SECONDS = env_int("FAILED_RETRY_SECONDS", 120)
    REGISTRY_MAX_ENTRIES = env_int("REGISTRY_MAX_ENTRIES", 500)
    SKIP_EXISTING_OUTPUT = env_bool("SKIP_EXISTING_OUTPUT", True)
    MIN_VALID_OUTPUT_BYTES = env_int("MIN_VALID_OUTPUT_BYTES", 10 * 1024)

    TIKTOK_COOKIES_FILE = resolve_path(
        env_value("TIKTOK_COOKIES_FILE", "data/tiktok_cookies.txt")
    )
    TIKTOK_HEADLESS = env_bool("TIKTOK_HEADLESS", True)
    TIKTOK_AUTO_UPLOAD = env_bool("TIKTOK_AUTO_UPLOAD", True)
    TIKTOK_UPLOAD_RETRIES = max(1, env_int("TIKTOK_UPLOAD_RETRIES", 3))
    TIKTOK_UPLOAD_RETRY_DELAY_SECONDS = max(0, env_int("TIKTOK_UPLOAD_RETRY_DELAY_SECONDS", 0))
    TIKTOK_MINIMIZED = env_bool("TIKTOK_MINIMIZED", True)
    TIKTOK_PROCESSING_TIMEOUT_SECONDS = max(30, env_int("TIKTOK_PROCESSING_TIMEOUT_SECONDS", 180))
    TIKTOK_CONFIRM_TIMEOUT_SECONDS = max(30, env_int("TIKTOK_CONFIRM_TIMEOUT_SECONDS", 90))
    TIKTOK_UPLOAD_CHECK_SECONDS = max(5, env_int("TIKTOK_UPLOAD_CHECK_SECONDS", 30))
    FFMPEG_PRIORITY = env_value("FFMPEG_PRIORITY", "idle").strip().lower()
    if FFMPEG_PRIORITY not in {"normal", "below_normal", "idle"}:
        FFMPEG_PRIORITY = "idle"
    FFMPEG_THREADS = max(
        1,
        env_int(
            "FFMPEG_THREADS",
            max(1, (os.cpu_count() or 4) - 2),
        ),
    )
    TIKTOK_CAPTION_TEMPLATE = env_value(
        "TIKTOK_CAPTION_TEMPLATE",
        "{title} #tiktok #kick",
    )
    TIKTOK_UPLOAD_REGISTRY = resolve_path(
        env_value("TIKTOK_UPLOAD_REGISTRY", "data/tiktok_uploads.json")
    )


def validate_runtime_config():
    problems = []
    if not NVIDIA_API_KEY:
        problems.append("NVIDIA_API_KEY está vacío.")
    if TIKTOK_AUTO_UPLOAD and not TIKTOK_COOKIES_FILE.exists():
        problems.append(f"No existe la cookie de TikTok: {TIKTOK_COOKIES_FILE}")
    if shutil.which("ffmpeg") is None:
        problems.append("FFmpeg no está en PATH.")
    return problems


def _prepare_gui_stdio():
    """Evita cualquier ventana de consola cuando TTCA se ejecuta con pythonw/.pyw."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main():
    _prepare_gui_stdio()
    reload_config_from_env()
    ensure_dirs()

    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    app = TikTokClipAutomationApp(root)
    problems = validate_runtime_config()
    if problems:
        app.log("⚠️  Configuración pendiente:")
        for problem in problems:
            app.log(f"   - {problem}")
    root.mainloop()


if __name__ == "__main__":
    main()
