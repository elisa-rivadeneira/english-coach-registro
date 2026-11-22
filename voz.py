"""Voz gratuita (Kokoro, código abierto) con caché por frase. Requiere ffmpeg instalado."""
import base64
import hashlib
import os
import subprocess
import threading

import soundfile as sf

DIR = os.path.dirname(os.path.abspath(__file__))
MODELO = os.path.join(DIR, "modelos", "kokoro-int8.onnx")
VOCES = os.path.join(DIR, "modelos", "voices.bin")
CACHE = os.environ.get("CACHE_DIR", os.path.join(DIR, "cache_audio"))
MAX_CARACTERES = 300
VOCES_OK = {"af_heart", "af_bella", "af_nicole", "am_adam", "am_michael", "bf_emma", "bm_george"}

_kokoro = None
_lock = threading.Lock()


def _motor():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(MODELO, VOCES)
    return _kokoro


def audio_mp3(texto: str, voz: str = "af_heart", velocidad: float = 0.95) -> bytes:
    texto = " ".join(texto.split())
    if not texto:
        raise ValueError("Texto vacío")
    if len(texto) > MAX_CARACTERES:
        raise ValueError(f"Texto demasiado largo (máximo {MAX_CARACTERES} caracteres)")
    if voz not in VOCES_OK:
        voz = "af_heart"
    velocidad = min(1.3, max(0.7, velocidad))
    clave = hashlib.sha256(f"{voz}|{velocidad}|{texto}".encode()).hexdigest()
    ruta = os.path.join(CACHE, clave + ".mp3")
    if os.path.exists(ruta):
        with open(ruta, "rb") as f:
            return f.read()
    os.makedirs(CACHE, exist_ok=True)
    with _lock:  # un solo motor a la vez
        muestras, sr = _motor().create(texto, voice=voz, speed=velocidad, lang="en-us")
    tmp_wav = ruta + ".wav"
    sf.write(tmp_wav, muestras, sr)
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", tmp_wav, "-b:a", "48k", "-ac", "1", ruta],
            check=True,
        )
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)
    with open(ruta, "rb") as f:
        return f.read()


def audio_base64(texto: str, voz: str = "af_heart", velocidad: float = 0.95) -> str:
    return base64.b64encode(audio_mp3(texto, voz, velocidad)).decode()
