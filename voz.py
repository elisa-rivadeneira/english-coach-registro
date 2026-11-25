"""Voz gratuita (Kokoro, código abierto) con caché por frase. Requiere ffmpeg instalado."""
import base64
import hashlib
import os
import subprocess
import threading
import uuid

import soundfile as sf

DIR = os.path.dirname(os.path.abspath(__file__))
MODELO = os.path.join(DIR, "modelos", "kokoro-int8.onnx")
VOCES = os.path.join(DIR, "modelos", "voices.bin")
CACHE = os.environ.get("CACHE_DIR", os.path.join(DIR, "cache_audio"))
MAX_CARACTERES = 300
VOCES_OK = {"af_heart", "af_bella", "af_nicole", "am_adam", "am_michael", "bf_emma", "bm_george"}

# Motores de voz en paralelo (MOTORES en el entorno; 1 por defecto). Cada motor ocupa RAM y CPU:
# súbelo solo si la prueba de carga (prueba_carga.py) muestra CPU libre.
N_MOTORES = max(1, int(os.environ.get("MOTORES", "1")))
_motores = []
_libres = []
_cv = threading.Condition()


def _tomar_motor():
    """Toma un motor libre (crea uno nuevo si aún no se llegó al máximo); si no, espera en fila."""
    with _cv:
        while True:
            if _libres:
                return _libres.pop()
            if len(_motores) < N_MOTORES:
                from kokoro_onnx import Kokoro
                m = Kokoro(MODELO, VOCES)
                _motores.append(m)
                return m
            _cv.wait()


def _soltar_motor(m):
    with _cv:
        _libres.append(m)
        _cv.notify()


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
    motor = _tomar_motor()  # en fila si todos los motores están ocupados
    try:
        if os.path.exists(ruta):  # otro pedido igual terminó mientras esperábamos
            with open(ruta, "rb") as f:
                return f.read()
        muestras, sr = motor.create(texto, voice=voz, speed=velocidad, lang="en-us")
    finally:
        _soltar_motor(motor)
    sufijo = uuid.uuid4().hex[:8]
    tmp_wav = ruta + "." + sufijo + ".wav"
    tmp_mp3 = ruta + "." + sufijo + ".mp3"
    sf.write(tmp_wav, muestras, sr)
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", tmp_wav, "-b:a", "48k", "-ac", "1", tmp_mp3],
            check=True,
        )
        os.replace(tmp_mp3, ruta)  # escritura atómica: nadie lee un mp3 a medias
    finally:
        for t in (tmp_wav, tmp_mp3):
            if os.path.exists(t):
                os.remove(t)
    with open(ruta, "rb") as f:
        return f.read()


def audio_base64(texto: str, voz: str = "af_heart", velocidad: float = 0.95) -> str:
    return base64.b64encode(audio_mp3(texto, voz, velocidad)).decode()
