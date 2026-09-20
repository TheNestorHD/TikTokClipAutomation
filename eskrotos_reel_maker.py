#!/usr/bin/env python3
"""
Eskrotos Reel Maker
-------------------
Convierte clips horizontales de Kick a Reels verticales 1080x1920
con facecam arriba + divisor + gameplay centrado + subtítulos animados.

Requisitos:
    pip install opencv-python mediapipe faster-whisper numpy tqdm pillow

Uso:
    python eskrotos_reel_maker.py
"""

import os
import sys

# Windows: HuggingFace no puede crear symlinks sin Developer Mode → fuerza copia
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import cv2
import math
import json
import time
import shutil
import tempfile
import subprocess
import numpy as np
from pathlib import Path
from tqdm import tqdm

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

# Detección de cara
FACE_PADDING = 0.90         # padding muy generoso alrededor de la cara
FACE_PAD_RIGHT_EXTRA = 0.70 # extra grande a la derecha (la cara suele estar a la izquierda del panel)
FACE_PAD_TOP_EXTRA = 0.25   # un poco más arriba (para incluir el borde superior del overlay)
MIN_FACE_SIZE = 50
SAMPLE_FRAMES = 8

# Whisper
WHISPER_MODEL = "large-v3"  # más preciso (español). Alternativas: "medium", "small"
WHISPER_DEVICE = "auto"     # "cuda", "cpu" o "auto"
WHISPER_COMPUTE = "default" # "float16", "int8", "default" (deja que elija)

# NVIDIA API
# ⚠️ Regenerá la key después de probar (quedó expuesta en el chat)
NVIDIA_API_KEY = "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"
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "moonshotai/kimi-k3"                      # visión / facecam (legacy)
# Facecam (visión): se prueban en orden hasta que uno responda
NVIDIA_MODELS_FACECAM = [
    ("moonshotai/kimi-k3", 130),                         # preciso, a veces lento
    ("google/diffusiongemma-26b-a4b-it", 60),             # fallback visión
]
# Auto-trim: primero Omni CON VIDEO; si falla, modelos de texto
NVIDIA_TRIM_OMNI = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
NVIDIA_TRIM_OMNI_TIMEOUT = 180
NVIDIA_MODELS_TRIM_TEXT = [
    "google/diffusiongemma-26b-a4b-it",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "moonshotai/kimi-k3",
]

# Watcher de clips de Kick
KICK_CHANNEL = "eskrotos"           # slug del canal
KICK_POLL_SECONDS = 5               # cada cuántos segundos revisa
KICK_DOWNLOAD_COOLDOWN = 45         # pausa tras empezar una descarga (anti-duplicados)
SEEN_CLIPS_FILE = Path(r"G:\Clips\seen_clips.json")  # IDs ya vistos

# ============================================================
# UTILIDADES
# ============================================================

def check_dependencies():
    missing = []
    try:
        import mediapipe
    except ImportError:
        missing.append("mediapipe")
    try:
        import cv2
    except ImportError:
        missing.append("opencv-python")
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
    fps = eval(video_stream["r_frame_rate"])  # "30/1" → 30.0
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

def detect_facecam_auto(video_path: Path, orig_w: int, orig_h: int):
    """
    Detecta la facecam usando MediaPipe Face Detection.
    Muestrea varios frames y promedia la bounding box más grande.
    Retorna (x, y, w, h) o None si no encuentra nada confiable.
    """
    import mediapipe as mp

    mp_face = mp.solutions.face_detection
    detector = mp_face.FaceDetection(model_selection=1, min_detection_confidence=0.5)

    width, height, fps, duration = get_video_info(video_path)
    times = np.linspace(0.5, min(duration - 0.5, 8.0), SAMPLE_FRAMES)

    boxes = []
    for t in times:
        frame = extract_frame(video_path, t)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = detector.process(rgb)

        if results.detections:
            # Elegimos la detección más grande (probablemente el streamer)
            best = max(results.detections, key=lambda d: d.location_data.relative_bounding_box.width *
                                                          d.location_data.relative_bounding_box.height)
            bb = best.location_data.relative_bounding_box
            x = int(bb.xmin * width)
            y = int(bb.ymin * height)
            w = int(bb.width * width)
            h = int(bb.height * height)

            if w >= MIN_FACE_SIZE and h >= MIN_FACE_SIZE:
                boxes.append((x, y, w, h))

    detector.close()

    if not boxes:
        return None

    # Promedio
    xs, ys, ws, hs = zip(*boxes)
    x = int(np.mean(xs))
    y = int(np.mean(ys))
    w = int(np.mean(ws))
    h = int(np.mean(hs))

    # Padding muy generoso + extra a la derecha y un poco arriba
    # (la cara suele estar a la izquierda del panel de la webcam)
    pad_x = int(w * FACE_PADDING)
    pad_y = int(h * FACE_PADDING)
    pad_right_extra = int(w * FACE_PAD_RIGHT_EXTRA)
    pad_top_extra = int(h * FACE_PAD_TOP_EXTRA)

    x = max(0, x - pad_x)
    y = max(0, y - pad_y - pad_top_extra)
    w = min(orig_w - x, w + pad_x + pad_x + pad_right_extra)
    h = min(orig_h - y, h + pad_y + pad_y + pad_top_extra)

    # Evitar que se vaya demasiado horizontal, pero priorizando la derecha
    aspect = w / max(h, 1)
    if aspect > 1.85:
        new_w = int(h * 1.55)
        # Empujamos hacia la derecha (no centramos)
        x = min(orig_w - new_w, x + (w - new_w))
        w = new_w

    if not is_valid_facecam_box(x, y, w, h, orig_w, orig_h):
        return None
    return (x, y, w, h)


def is_valid_facecam_box(x, y, w, h, orig_w, orig_h) -> bool:
    """
    Rechaza cajas que producirían facecam deforme (línea de píxeles, estirada, etc.).
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
    Localiza el panel de la facecam con modelos de visión (NVIDIA).
    Prueba en orden: Kimi → DiffusionGemma. Devuelve (x, y, w, h) o None.
    """
    import base64
    import re
    import requests
    from io import BytesIO
    from PIL import Image

    frame = extract_frame(video_path, time_sec)

    # Imagen chica = mucho más rápido y estable
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
        "It is a rectangular box (usually bottom-right) containing:\n"
        "- streamer's face, red/colored border, username, optional anime decoration\n"
        "IMPORTANT: Return a TIGHT bounding box that matches ONLY the panel itself.\n"
        "Do NOT include gameplay background outside the panel border.\n"
        "The box edges should align with the outer border of the facecam frame.\n"
        'Output ONLY JSON: {"x":N,"y":N,"width":N,"height":N}\n'
        "No other text."
    )

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    short_names = {
        "moonshotai/kimi-k3": "Kimi",
        "google/diffusiongemma-26b-a4b-it": "DiffusionGemma",
    }

    for model_id, timeout_s in NVIDIA_MODELS_FACECAM:
        name = short_names.get(model_id, model_id.split("/")[-1])
        print(f"  🤖 Facecam con {name} (timeout {timeout_s}s)...")

        payload = {
            "model": model_id,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]
            }],
            "max_tokens": 300,
            "temperature": 0.0,
            "stream": False
        }

        try:
            r = requests.post(NVIDIA_API_URL, headers=headers, json=payload, timeout=timeout_s)
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            reasoning = msg.get("reasoning_content") or ""

            text = content + "\n" + reasoning
            match = re.search(r'\{[^{}]*"x"\s*:\s*\d+[^{}]*\}', text)
            if not match:
                print(f"     ⚠️  {name} no devolvió JSON válido, siguiente...")
                continue

            box = json.loads(match.group(0))
            x = int(box["x"])
            y = int(box["y"])
            w = int(box["width"])
            h = int(box["height"])

            # Validación en la imagen chica
            if w < 30 or h < 30 or x < 0 or y < 0:
                print(f"     ⚠️  {name} box demasiado chico/negativo, siguiente...")
                continue

            # Escalar a resolución original
            inv = 1.0 / scale if scale > 0 else 1.0
            x = int(x * inv)
            y = int(y * inv)
            w = int(w * inv)
            h = int(h * inv)

            # Clamp al frame
            x = max(0, min(x, orig_w - 2))
            y = max(0, min(y, orig_h - 2))
            w = max(2, min(w, orig_w - x))
            h = max(2, min(h, orig_h - y))

            # Rechazar cajas deformes (línea de píxeles / estiradas)
            aspect = w / max(h, 1)
            area_ratio = (w * h) / max(orig_w * orig_h, 1)
            if w < 120 or h < 120:
                print(f"     ⚠️  {name} box muy chico ({w}x{h}), siguiente...")
                continue
            if aspect < 0.55 or aspect > 2.2:
                print(f"     ⚠️  {name} aspect raro ({aspect:.2f}), siguiente...")
                continue
            if area_ratio > 0.45 or area_ratio < 0.01:
                print(f"     ⚠️  {name} área rara ({area_ratio:.3f}), siguiente...")
                continue

            # Padding mínimo
            pad_x = max(1, int(w * 0.01))
            pad_y = max(1, int(h * 0.01))
            x = max(0, x - pad_x)
            y = max(0, y - pad_y)
            w = min(orig_w - x, w + 2 * pad_x)
            h = min(orig_h - y, h + 2 * pad_y)

            print(f"  ✅ {name} detectó facecam: x={x} y={y} w={w} h={h}")
            return (x, y, w, h)

        except requests.exceptions.Timeout:
            print(f"     ⚠️  Timeout de {name} (>{timeout_s}s), siguiente...")
            continue
        except Exception as e:
            print(f"     ⚠️  Error con {name}: {e}")
            continue

    print("  ❌ Todos los modelos de visión fallaron para facecam.")
    return None


def _make_trim_proxy(video_path: Path, max_sec: float = 60.0) -> Path | None:
    """Proxy liviano 720p @ 1fps para Nemotron Omni (video)."""
    import base64
    out = Path(tempfile.gettempdir()) / "_eskrotos_trim_proxy.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-t", str(max_sec),
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
            return out
    except Exception as e:
        print(f"     ⚠️  No se pudo crear proxy para Omni: {e}")
    return None


def suggest_trim_omni_video(video_path: Path, duration: float, max_retries: int = 5) -> tuple[float, float] | None:
    """
    Auto-trim con Nemotron Omni VIENDO el video.
    Reintenta varias veces si hay 503/429/timeout. NO usa Gemma.
    """
    import re
    import base64
    import requests

    print(f"  🤖 Auto-trim con Nemotron Omni (video)...")
    proxy = _make_trim_proxy(video_path, max_sec=min(60.0, duration + 1))
    if proxy is None:
        return None

    try:
        b64 = base64.b64encode(proxy.read_bytes()).decode("utf-8")
        data_url = f"data:video/mp4;base64,{b64}"
    except Exception as e:
        print(f"     ⚠️  Error leyendo proxy: {e}")
        return None

    prompt = f"""Sos editor de Reels virales del streamer argentino "Eskrotos" (Kick).
Estilo: humor absurdo, reacciones exageradas, sarcasmo, fallos épicos, punchlines.

Estás VIENDO el clip (proxy ~{min(duration, 60):.0f}s). Duración real del original: {duration:.1f}s.

Elegí el tramo MÁS viral para un Reel de TikTok:

REGLAS DE DURACIÓN (obligatorio):
- Largo IDEAL: 15 a 20 segundos.
- Mínimo: 12 segundos (si el original es más corto, usá todo).
- Máximo: el clip completo ({duration:.1f}s) si hace falta.
- NUNCA cortes solo el clímax. Incluí contexto:
  * 3–6s ANTES del momento clave (setup / jugada / frase que arma el chiste)
  * el momento principal
  * 2–5s DESPUÉS (reacción, celebración, comentario del streamer)
- Mal ejemplo: solo la celebración de un gol (7s sin contexto).
- Bien: jugada → gol → celebración + reacción (~15–20s).
- Si el mejor momento necesita más de 20s o casi todo el video, devolvé el tramo largo o start=0 y end={duration:.1f}.

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
                NVIDIA_API_URL, headers=headers, json=payload,
                timeout=NVIDIA_TRIM_OMNI_TIMEOUT,
            )
            if r.status_code in (503, 429):
                wait = min(60, 10 * attempt)
                print(f"     ⚠️  Omni saturado (HTTP {r.status_code}). Espero {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                wait = min(30, 5 * attempt)
                print(f"     ⚠️  Omni HTTP {r.status_code}. Espero {wait}s...")
                time.sleep(wait)
                continue

            data = r.json()
            msg = data["choices"][0]["message"]
            text = (msg.get("content") or "") + "\n" + (
                msg.get("reasoning_content") or msg.get("reasoning") or ""
            )
            match = re.search(r'\{[^{}]*"start"\s*:\s*[\d.]+[^{}]*\}', text, re.DOTALL)
            if not match:
                print("     ⚠️  Omni sin JSON válido. Reintento...")
                time.sleep(4)
                continue

            obj = json.loads(match.group(0))
            start = max(0.0, min(float(obj["start"]), duration - 1.0))
            end = max(start + 3.0, min(float(obj["end"]), duration))
            # Preferir tramos con contexto: si quedó muy corto, extender hacia atrás/adelante
            min_len = min(12.0, duration)
            if end - start < min_len and duration >= min_len:
                extra = min_len - (end - start)
                # 60% del extra hacia atrás (contexto), 40% hacia adelante (reacción)
                back = min(start, extra * 0.6)
                start = start - back
                end = min(duration, end + (extra - back))
                # Si aún falta, comer del otro lado
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

    print("  ❌ Omni no respondió tras varios reintentos (sin fallback a Gemma).")
    return None


def suggest_trim_llm(words: list, duration: float, video_path: Path | None = None) -> tuple[float, float] | None:
    """
    Auto-trim SOLO con Nemotron Omni (video). Reintenta si falla.
    Si Omni no da resultado, devuelve None → en interactivo podés marcar a mano.
    """
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
    """Carga Whisper una sola vez. Si large-v3 falla (symlinks Windows), cae a medium."""
    global _WHISPER_MODEL_INSTANCE, _WHISPER_MODEL_NAME
    from faster_whisper import WhisperModel

    if _WHISPER_MODEL_INSTANCE is not None:
        return _WHISPER_MODEL_INSTANCE, _WHISPER_MODEL_NAME

    device = WHISPER_DEVICE
    compute = WHISPER_COMPUTE
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
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
        except OSError as e:
            # WinError 1314 u otros problemas de descarga/symlinks
            print(f"  ⚠️  No se pudo cargar '{name}': {e}")
            last_err = e
            continue
        except Exception as e:
            print(f"  ⚠️  Error cargando '{name}': {e}")
            last_err = e
            continue

    raise RuntimeError(f"No se pudo cargar ningún modelo Whisper. Último error: {last_err}")


def _extract_audio_for_whisper(video_path: Path) -> Path:
    """
    Extrae audio mono 16kHz normalizado (menos música/juego = menos alucinaciones).
    """
    out = Path(tempfile.gettempdir()) / "_eskrotos_whisper_audio.wav"
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


def transcribe_video(video_path: Path):
    """
    Transcribe con faster-whisper y devuelve lista de palabras con timestamps.
    Cada item: {"word": str, "start": float, "end": float}
    """
    model, model_name = _load_whisper_model()
    print(f"  🎙️  Transcribiendo con Whisper ({model_name})...")

    # Audio limpio → menos "VIDEOS!" / basura por música del juego
    try:
        audio_path = _extract_audio_for_whisper(video_path)
        audio_src = str(audio_path)
        print("     Audio pre-procesado (mono 16k + loudnorm)")
    except Exception as e:
        print(f"     ⚠️  No se pudo pre-procesar audio ({e}), uso el video directo")
        audio_src = str(video_path)

    segments, info = model.transcribe(
        audio_src,
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
        # temperature en lista: si alucina con 0.0, prueba valores más altos
        temperature=[0.0, 0.2, 0.4],
        # False reduce alucinaciones en clips cortos con mucho ruido
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

    # Palabras basura típicas de alucinación de Whisper
    HALLUCINATION_WORDS = {
        "videos", "video", "subscribe", "suscribete", "suscríbete",
        "thanks", "thank", "you", "www", "http", "com", "music",
        "subtitulos", "subtítulos", "subtitles", "copyright",
    }

    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                text = w.word.strip()
                if not text:
                    continue
                # Filtrar alucinaciones obvias
                if text.lower().strip(".,!?¡¿\"'") in HALLUCINATION_WORDS:
                    continue
                # Filtrar tokens casi sin confianza si existe
                if hasattr(w, "probability") and w.probability is not None:
                    if w.probability < 0.35:
                        continue
                words.append({
                    "word": text,
                    "start": float(w.start),
                    "end": float(w.end),
                })

    cleaned = []
    for w in words:
        dur = w["end"] - w["start"]
        if dur <= 0:
            w["end"] = w["start"] + 0.12
        elif dur > 2.5:
            w["end"] = w["start"] + 0.45
        cleaned.append(w)

    # Preview de lo que entendió
    preview = " ".join(w["word"] for w in cleaned[:25])
    if preview:
        print(f"     Preview: {preview}{'...' if len(cleaned) > 25 else ''}")
    print(f"  ✅ {len(cleaned)} palabras detectadas")
    return cleaned


def create_ass_file(words, ass_path: Path, video_w: int, video_h: int, text_y: int):
    """
    Subtítulos palabra por palabra, SIN efectos.
    Cada palabra vive solo entre su start y end (no queda flotando en silencios).
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
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    events = []
    center_x = video_w // 2
    # Posición fija, sin fade/scale
    pos = r"{\pos(" + str(center_x) + "," + str(text_y) + r")}"

    i = 0
    while i < len(words):
        group = words[i:i + SUB_MAX_WORDS]
        start = float(group[0]["start"])
        end = float(group[-1]["end"])

        # Duración mínima legible, pero sin extenderse al silencio
        if end - start < 0.08:
            end = start + 0.08
        # No solapar mucho con la siguiente palabra
        if i + SUB_MAX_WORDS < len(words):
            next_start = float(words[i + SUB_MAX_WORDS]["start"])
            if end > next_start - 0.02:
                end = max(start + 0.06, next_start - 0.02)

        full_text = " ".join(w["word"] for w in group)
        line = f"Dialogue: 0,{sec_to_ass(start)},{sec_to_ass(end)},Default,,0,0,0,,{pos}{full_text}\n"
        events.append(line)
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


def process_one_clip(video_path: Path, interactive: bool = True):
    """
    Procesa un solo clip de principio a fin.
    """
    print(f"\n{'='*60}")
    print(f"🎬 Procesando: {video_path.name}")
    print(f"{'='*60}")

    orig_w, orig_h, fps, duration = get_video_info(video_path)
    print(f"  Resolución original: {orig_w}x{orig_h} | {duration:.1f}s | {fps:.2f} fps")

    # ---- 1. Seleccionar facecam ----
    box = None
    if interactive:
        print("\n  📷 Selección de FACECAM")
        print("  1 = Manual")
        print("  2 = Automática (MediaPipe)")
        print("  3 = LLM (Kimi → DiffusionGemma)  ← recomendado")
        print("  S = Saltar este clip")
        choice = input("  Elegí [1/2/3/S] (default 3): ").strip().lower() or "3"

        if choice in ("s",):
            print("  ⏭️  Saltado.")
            return False
        elif choice == "2":
            print("  🔍 Buscando facecam con MediaPipe...")
            box = detect_facecam_auto(video_path, orig_w, orig_h)
            if box is None:
                print("  ⚠️  No se detectó. Pasando a manual...")
                box = manual_select_facecam(video_path)
        elif choice == "3":
            box = detect_facecam_llm(video_path, orig_w, orig_h)
            if box is None:
                print("  ⚠️  LLMs fallaron. Intentando MediaPipe...")
                box = detect_facecam_auto(video_path, orig_w, orig_h)
            if box is None:
                print("  ⚠️  Nada funcionó. Pasando a manual...")
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
        # Modo no-interactivo: intenta LLM → MediaPipe
        box = detect_facecam_llm(video_path, orig_w, orig_h)
        if box is None:
            print("  🔍 Fallback MediaPipe...")
            box = detect_facecam_auto(video_path, orig_w, orig_h)
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

    # ---- 3. Trim (auto Kimi + opcional ajuste manual) ----
    start_sec = 0.0
    end_sec = duration

    suggested = suggest_trim_llm(words, duration, video_path=video_path)
    if suggested:
        start_sec, end_sec = suggested

    if interactive:
        if suggested:
            print(f"\n  ⏱️  Gemma sugiere: {start_sec:.1f}s → {end_sec:.1f}s ({end_sec-start_sec:.1f}s)")
            print("  Opciones:")
            print("    Enter = usar sugerencia de Gemma")
            print("    M     = ajustar manualmente en el player")
            print("    T     = usar el clip completo")
            choice = input("  Elegí [Enter/M/T]: ").strip().lower()
            if choice == "m":
                start_sec, end_sec = interactive_trim(video_path, duration, words=words)
            elif choice == "t":
                start_sec, end_sec = 0.0, duration
                print("  → Clip completo")
            else:
                print(f"  → Usando Gemma: {start_sec:.1f}s → {end_sec:.1f}s")
        else:
            print("\n  ⏱️  Gemma no pudo sugerir trim. Opciones:")
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
            print(f"  → Auto-trim Gemma: {start_sec:.1f}s → {end_sec:.1f}s")
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
        try:
            USED_DIR.mkdir(parents=True, exist_ok=True)
            dest = USED_DIR / video_path.name
            if dest.exists():
                stem, suf = video_path.stem, video_path.suffix
                dest = USED_DIR / f"{stem}_done{suf}"
            shutil.move(str(video_path), str(dest))
            print(f"  📦 Original movido a: {dest}")
        except Exception as e:
            print(f"  ⚠️  No se pudo mover el original a Usados: {e}")

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

def load_seen_clips() -> set:
    if SEEN_CLIPS_FILE.exists():
        try:
            with open(SEEN_CLIPS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_clips(seen: set):
    SEEN_CLIPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_CLIPS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)


def fetch_kick_clips(channel: str = KICK_CHANNEL, limit: int = 20) -> list:
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

    # Nombre de archivo seguro
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:40].strip()
    created = (clip.get("created_at") or "")[:10]
    short_id = clip_id.replace("clip_", "")[-12:]
    out_name = f"{created} {safe_title} {short_id}.mp4".strip()
    out_path = dest_dir / out_name

    if out_path.exists():
        print(f"  ⏭️  Ya existe: {out_name}")
        return out_path

    print(f"  ⬇️  Descargando: {title} ({clip.get('duration', '?')}s)")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        str(out_path)
    ]
    try:
        subprocess.run(cmd, check=True, timeout=180)
        print(f"  ✅ Descargado: {out_path.name}")
        return out_path
    except Exception as e:
        print(f"  ❌ Error descargando: {e}")
        if out_path.exists():
            out_path.unlink(missing_ok=True)
        return None


def watch_kick_clips():
    """
    Loop infinito:
    - Cada KICK_POLL_SECONDS consulta la API de clips
    - Si hay clip nuevo → descarga → cooldown 45s → procesa
    """
    print("=" * 60)
    print(f"  WATCHER Kick → /{KICK_CHANNEL}")
    print(f"  Poll cada {KICK_POLL_SECONDS}s | Cooldown descarga {KICK_DOWNLOAD_COOLDOWN}s")
    print("=" * 60)
    print("  Ctrl+C para detener.\n")

    seen = load_seen_clips()
    print(f"  Clips ya conocidos: {len(seen)}")

    # Primera pasada: marcar los actuales como vistos (no re-procesar histórico)
    try:
        current = fetch_kick_clips()
        for c in current:
            cid = c.get("id")
            if cid:
                seen.add(cid)
        save_seen_clips(seen)
        print(f"  Marcados {len(current)} clips actuales como ya vistos (solo procesará los NUEVOS).\n")
    except Exception as e:
        print(f"  ⚠️  No se pudo hacer el seed inicial: {e}")

    while True:
        try:
            clips = fetch_kick_clips()
            new_ones = [c for c in clips if c.get("id") and c["id"] not in seen]

            if new_ones:
                # Los más nuevos primero (API suele devolver recientes arriba)
                for clip in new_ones:
                    cid = clip["id"]
                    print(f"\n🆕 Clip nuevo: {clip.get('title')} ({cid})")
                    seen.add(cid)
                    save_seen_clips(seen)

                    path = download_kick_clip(clip, CLIPS_DIR)
                    print(f"  ⏳ Cooldown {KICK_DOWNLOAD_COOLDOWN}s (anti-duplicados)...")
                    time.sleep(KICK_DOWNLOAD_COOLDOWN)

                    if path and path.exists():
                        print(f"  🎬 Procesando automáticamente...")
                        try:
                            # Modo no-interactivo: Kimi facecam + trim completo por ahora
                            # (cuando tengamos LLM-trim, acá se usa solo)
                            process_one_clip(path, interactive=False)
                        except Exception as e:
                            print(f"  ❌ Error procesando: {e}")
            else:
                print(f"  [{time.strftime('%H:%M:%S')}] Sin clips nuevos...", end="\r")

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
