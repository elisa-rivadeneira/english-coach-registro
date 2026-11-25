"""Voz gratuita (Kokoro, código abierto) con caché por frase. Requiere ffmpeg instalado."""
import base64
import hashlib
import os
import queue
import re
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


# ---------- Preparar audios en segundo plano (cuando se crea la lección) ----------
_cola = queue.Queue()
_hilo = None
_hilo_lock = threading.Lock()
MAX_FRASES_LOTE = 80


def _trozos(texto):
    """Mismo corte que hace la app (frases de hasta ~280 caracteres), para que coincida el caché."""
    t = " ".join(str(texto or "").split())
    if len(t) <= 280:
        return [t] if t else []
    partes = re.findall(r"[^.!?…]+[.!?…]*\s*", t) or [t]
    out, cur = [], ""
    for x in partes:
        if len(cur + x) > 280 and cur:
            out.append(cur.strip())
            cur = x
        else:
            cur += x
    if cur.strip():
        out.append(cur.strip())
    res = []
    for x in out:
        if len(x) <= 300:
            res.append(x)
        else:
            res += [y.strip() for y in re.findall(r".{1,280}(?:\s|$)", x) if y.strip()]
    return res


def _ruta(texto, voz="af_heart", velocidad=0.95):
    clave = hashlib.sha256(f"{voz}|{velocidad}|{texto}".encode()).hexdigest()
    return os.path.join(CACHE, clave + ".mp3")


def _trabajador():
    while True:
        t = _cola.get()
        try:
            audio_mp3(t, "af_heart", 0.95)
        except Exception:
            pass
        finally:
            _cola.task_done()


def precalentar(frases):
    """Encola la generación de audios (sin esperar). Devuelve (pedidos_nuevos, ya_listos)."""
    global _hilo
    vistos, nuevos, listos = set(), 0, 0
    for f in list(frases or [])[:MAX_FRASES_LOTE]:
        if not isinstance(f, str) or "__" in f:
            continue
        for t in _trozos(f):
            if t in vistos or len(t) > MAX_CARACTERES:
                continue
            vistos.add(t)
            if os.path.exists(_ruta(t)):
                listos += 1
            else:
                _cola.put(t)
                nuevos += 1
    if nuevos:
        with _hilo_lock:
            if _hilo is None or not _hilo.is_alive():
                _hilo = threading.Thread(target=_trabajador, daemon=True)
                _hilo.start()
    return nuevos, listos
