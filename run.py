"""Avvio in sviluppo: API + worker embedded su http://localhost:8000

    python run.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

if __name__ == "__main__":
    import shutil

    if shutil.which("ffmpeg") is None:
        print("ERRORE: ffmpeg non trovato nel PATH.")
        print("Windows:  winget install Gyan.FFmpeg   (poi riapri il terminale)")
        print("Linux:    sudo apt install ffmpeg")
        sys.exit(1)

    if shutil.which("ffprobe") is None:
        print("ERRORE: ffprobe non trovato nel PATH (di norma arriva con ffmpeg).")
        print("Windows:  winget install Gyan.FFmpeg   (poi riapri il terminale)")
        print("Linux:    sudo apt install ffmpeg")
        sys.exit(1)

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"EditVideo → http://localhost:{port}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
