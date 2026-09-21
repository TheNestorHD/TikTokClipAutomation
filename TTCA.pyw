"""Lanzador sin consola para TikTok Clip Automation."""

from pathlib import Path
import os
import sys
import traceback
import tkinter as tk
from tkinter import messagebox

APP_DIR = Path(__file__).resolve().parent
os.chdir(APP_DIR)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    from tiktok_clip_automation import main
    main()
except Exception:
    APP_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
    error_path = APP_DIR / "data" / "ttca_startup_error.log"
    try:
        error_path.write_text(
            traceback.format_exc(),
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "TikTok Clip Automation",
            "TTCA no pudo iniciarse.\n\n"
            f"El detalle quedó guardado en:\n{error_path}",
        )
        root.destroy()
    except Exception:
        pass
