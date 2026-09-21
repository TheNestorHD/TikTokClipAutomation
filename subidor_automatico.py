import json
import time
import random
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ====================== CONFIGURACIÓN ======================
CARPETA_VIDEOS = Path(r"G:\Tiktok\tiktok_videos\eskrotos_")
ARCHIVO_JSON = CARPETA_VIDEOS / "videos.json"
ARCHIVO_SUBIDOS = CARPETA_VIDEOS / "subidos.json"
COOKIES = Path(r"G:\Tiktok\cookies.txt")

HORA_INICIO = 00
HORA_FIN = 23
MAX_POR_DIA = 15
VARIACION_MINUTOS = 5

# Minutos de espera entre subidas. None = reparte en horarios a lo largo del
# día (comportamiento original con VARIACION_MINUTOS).
INTERVALO_MINUTOS = 60

# True = navegador invisible | False = visible (más confiable)
HEADLESS = True

# True = abrir el navegador minimizado (solo aplica en modo visible)
MINIMIZADO = True
# ===========================================================


def cargar_json(path: Path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def guardar_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def obtener_videos_pendientes():
    videos = cargar_json(ARCHIVO_JSON, {})
    subidos = cargar_json(ARCHIVO_SUBIDOS, {})

    pendientes = []
    for nombre, caption in videos.items():
        if nombre not in subidos:
            ruta = CARPETA_VIDEOS / nombre
            if ruta.exists():
                pendientes.append((nombre, caption, ruta))

    pendientes.sort(key=lambda x: x[0])
    return pendientes


def ya_subi_hoy(subidos: dict) -> int:
    hoy = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for info in subidos.values() if info.get("fecha") == hoy)


def proximo_horario():
    ahora = datetime.now()
    hoy = ahora.date()

    horarios = []
    for h in range(HORA_INICIO, HORA_FIN):
        base = datetime.combine(hoy, datetime.min.time()).replace(hour=h, minute=0)
        offset = random.randint(-VARIACION_MINUTOS, VARIACION_MINUTOS)
        horarios.append(base + timedelta(minutes=offset))

    for horario in horarios:
        if horario > ahora:
            return horario

    manana = hoy + timedelta(days=1)
    base = datetime.combine(manana, datetime.min.time()).replace(hour=HORA_INICIO, minute=0)
    offset = random.randint(-VARIACION_MINUTOS, VARIACION_MINUTOS)
    return base + timedelta(minutes=offset)


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
    intento = 1
    return _subir_video_intento(ruta_video, caption, intento)


def subir_video_con_reintento(ruta_video: Path, caption: str, max_intentos: int = 3) -> bool:
    """Reintenta la subida cada 60 segundos si falla."""
    for intento in range(1, max_intentos + 1):
        if intento > 1:
            print(f"\n↻ Reintento {intento}/{max_intentos} en 60 segundos...")
            time.sleep(60)
        if _subir_video_intento(ruta_video, caption, intento):
            return True
    print(f"✗ Falló después de {max_intentos} intentos.")
    return False


def _subir_video_intento(ruta_video: Path, caption: str, intento: int = 1) -> bool:
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Subiendo (intento {intento}): {ruta_video.name}")
    print(f"Caption: {caption[:80]}{'...' if len(caption) > 80 else ''}")

    with sync_playwright() as p:
        browser_args = ["--disable-blink-features=AutomationControlled"]
        if not HEADLESS and MINIMIZADO:
            # --start-minimized lo ignora Chromium en modo automatizado.
            # Truco que sí funciona: mover la ventana fuera de la pantalla.
            browser_args.append("--window-position=-32000,-32000")

        browser = p.chromium.launch(
            headless=HEADLESS,
            args=browser_args
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        )

        try:
            cargar_cookies(context, COOKIES)
            page = context.new_page()

            print("→ Navegando a TikTok Studio Upload...")
            page.goto("https://www.tiktok.com/tiktokstudio/upload?lang=en", timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)

            cerrar_popups(page)

            # Subir archivo
            print("→ Subiendo archivo...")
            file_input = page.locator('input[type="file"]').first
            file_input.wait_for(state="attached", timeout=15000)
            file_input.set_input_files(str(ruta_video))
            time.sleep(2)

            # ---- Esperar a que el video se procese de verdad ----
            print("→ Esperando a que el video se procese (Post habilitado)...")
            procesado = False
            deadline = time.time() + 180  # 3 min máximo
            while time.time() < deadline:
                try:
                    btn = page.locator(
                        'button[data-e2e="post_video_button"], '
                        'button:has-text("Post")'
                    ).first
                    if btn.is_visible(timeout=800):
                        disabled = btn.get_attribute("disabled") or btn.get_attribute("aria-disabled")
                        clases = (btn.get_attribute("class") or "").lower()
                        if disabled not in ["true", "True", True] and "disabled" not in clases:
                            procesado = True
                            break
                except Exception:
                    pass
                time.sleep(2)

            if not procesado:
                print("✗ El video no terminó de procesarse en 3 minutos")
                page.screenshot(path="error_procesamiento.png")
                return False

            print("→ Video procesado, botón Post habilitado")
            time.sleep(1.5)
            cerrar_popups(page)

            # Descripción
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

            # ========== CLICK EN POST ==========
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
                        disabled = btn.get_attribute("disabled") or btn.get_attribute("aria-disabled")
                        if disabled in ["true", "True", True]:
                            continue
                        btn.scroll_into_view_if_needed()
                        time.sleep(0.5)
                        btn.click(timeout=5000)
                        print(f"  → Click correcto en: {sel}")
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                print("✗ No se encontró el botón de Publicar")
                page.screenshot(path="error_no_post_button.png")
                return False

            # ---- Confirmar publicación con señales reales ----
            print("→ Esperando confirmación (hasta 90s)...")
            publicado = False
            deadline = time.time() + 90
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
                if exito:
                    publicado = True
                    break

                # 1) Modal "Continue to post?" → click en "Post now" (con fallback JS)
                post_now = False
                for sel in [
                    'button:has-text("Post now")',
                    'button:has-text("Publicar ahora")',
                    '//button[contains(translate(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "POST NOW", "post now"), "post now")]',
                ]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=600):
                            try:
                                btn.click(timeout=1500)
                            except Exception:
                                btn.evaluate("el => el.click()")
                            print(f'  → Confirmado modal Post now ({sel[:30]})')
                            post_now = True
                            break
                    except Exception:
                        pass
                if post_now:
                    time.sleep(2)
                    continue

                # 2) Diálogo de salida → cancelar y reintentar el click en Post
                if manejar_dialogo_salida(page):
                    print("  → Diálogo de salida cancelado. Reintentando click en Post...")
                    time.sleep(2)
                    for sel in post_selectors:
                        try:
                            btn = page.locator(sel).first
                            if btn.count() > 0 and btn.is_visible(timeout=800):
                                disabled = btn.get_attribute("disabled") or btn.get_attribute("aria-disabled")
                                if disabled in ["true", "True", True]:
                                    continue
                                try:
                                    btn.click(timeout=1500)
                                except Exception:
                                    btn.evaluate("el => el.click()")
                                print(f"  → Re-click en Post: {sel[:40]}")
                                break
                        except Exception:
                            continue
                    time.sleep(2)
                    continue

                time.sleep(2)

            if publicado:
                print("✓ Subido correctamente")
                return True
            else:
                print("✗ No se confirmó la publicación")
                page.screenshot(path="error_post_fallido.png")
                print("  (Se guardó captura: error_post_fallido.png)")
                return False

        except Exception as e:
            print(f"✗ Error inesperado: {e}")
            try:
                page.screenshot(path="error_excepcion.png")
            except:
                pass
            return False
        finally:
            browser.close()


def main():
    print("=" * 60)
    print("  SUBIDOR AUTOMÁTICO DE TIKTOK (Playwright propio)")
    print("  Máx. 10 videos/día | 11:00 a 21:00 | 1 por hora")
    print(f"  Modo: {'Headless' if HEADLESS else 'Navegador visible'}")
    print("=" * 60)

    if not ARCHIVO_JSON.exists():
        print(f"No se encontró {ARCHIVO_JSON}")
        return
    if not COOKIES.exists():
        print(f"No se encontró {COOKIES}")
        return

    # Preguntar una sola vez al inicio si está fuera de horario
    ahora = datetime.now()
    if not (HORA_INICIO <= ahora.hour < HORA_FIN):
        print(f"\nEstás fuera del horario programado ({HORA_INICIO}:00 - {HORA_FIN}:00).")
        respuesta = input("¿Querés subir UN video ahora igual? (s/n): ").strip().lower()
        if respuesta == "s":
            subidos = cargar_json(ARCHIVO_SUBIDOS, {})
            pendientes = obtener_videos_pendientes()
            if pendientes:
                nombre, caption, ruta = pendientes[0]
                print("\nSubiendo un video fuera de horario...")
                exito = subir_video_con_reintento(ruta, caption)
                if exito:
                    subidos[nombre] = {
                        "fecha": ahora.strftime("%Y-%m-%d"),
                        "hora": ahora.strftime("%H:%M:%S"),
                        "caption": caption
                    }
                    guardar_json(ARCHIVO_SUBIDOS, subidos)
                    print("Registrado en subidos.json")
                else:
                    print("Falló la subida fuera de horario.")
            else:
                print("No hay videos pendientes.")
        else:
            print("Ok, esperando al horario normal...")

    while True:
        ahora = datetime.now()
        subidos = cargar_json(ARCHIVO_SUBIDOS, {})
        pendientes = obtener_videos_pendientes()

        print(f"\n[{ahora.strftime('%Y-%m-%d %H:%M:%S')}] Videos pendientes: {len(pendientes)}")
        print(f"Subidos hoy: {ya_subi_hoy(subidos)}/{MAX_POR_DIA}")

        if not pendientes:
            print("No hay más videos pendientes. Esperando 30 minutos...")
            time.sleep(30 * 60)
            continue

        if ya_subi_hoy(subidos) >= MAX_POR_DIA:
            proximo = proximo_horario()
            segundos = (proximo - ahora).total_seconds()
            print(f"Ya se alcanzó el máximo de hoy. Próximo intento: {proximo.strftime('%Y-%m-%d %H:%M')}")
            time.sleep(min(segundos, 30 * 60))
            continue

        if not (HORA_INICIO <= ahora.hour < HORA_FIN):
            proximo = proximo_horario()
            segundos = (proximo - ahora).total_seconds()
            print(f"Fuera de horario. Próxima subida estimada: {proximo.strftime('%H:%M')}")
            time.sleep(min(segundos, 20 * 60))
            continue

        nombre, caption, ruta = pendientes[0]
        exito = subir_video_con_reintento(ruta, caption)

        if exito:
            subidos[nombre] = {
                "fecha": ahora.strftime("%Y-%m-%d"),
                "hora": ahora.strftime("%H:%M:%S"),
                "caption": caption
            }
            guardar_json(ARCHIVO_SUBIDOS, subidos)
            print("Registrado en subidos.json")
        else:
            print("No se registró como subido. Se reintentará más tarde.")

        if INTERVALO_MINUTOS is None:
            proximo = proximo_horario()
            segundos = max(60, (proximo - datetime.now()).total_seconds())
            print(f"\nPróxima subida estimada: {proximo.strftime('%H:%M:%S')} (en {int(segundos/60)} min)")
        else:
            jitter = random.randint(-VARIACION_MINUTOS, VARIACION_MINUTOS)
            segundos = max(60, (INTERVALO_MINUTOS + jitter) * 60)
            print(f"\nPróxima subida en {int(segundos/60)} min (intervalo {INTERVALO_MINUTOS} min ± jitter)")
        time.sleep(segundos)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nScript detenido por el usuario.")
