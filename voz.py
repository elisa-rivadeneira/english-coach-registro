"""Voz gratuita (Kokoro, código abierto) con caché por frase. Requiere ffmpeg instalado."""
import base64
import json
import urllib.error
import urllib.request
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
# Motor de voz: "kokoro" (local, gratis, lento en CPU chico) o "google" (Google Cloud Text-to-Speech, por uso).
MOTOR = os.environ.get("TTS_MOTOR", "kokoro").strip().lower()
GOOGLE_KEY = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()
# Voz de Google por defecto para cada voz de Kokoro (se puede cambiar con GOOGLE_VOZ_DEFECTO, p. ej. en-US-Neural2-C)
GOOGLE_VOCES = {
    "af_heart": "en-US-Neural2-F", "af_bella": "en-US-Neural2-H", "af_nicole": "en-US-Neural2-C",
    "am_adam": "en-US-Neural2-D", "am_michael": "en-US-Neural2-J",
    "bf_emma": "en-GB-Neural2-A", "bm_george": "en-GB-Neural2-B",
}
GOOGLE_VOZ_DEFECTO = os.environ.get("GOOGLE_VOZ_DEFECTO", "").strip()
# Mezcla de voces: cada frase recibe siempre la misma voz (según su texto), pero distintas frases suenan con
# voces distintas, para acostumbrar el oído. GOOGLE_VOCES_MEZCLA = lista separada por comas; vacío = sin mezcla.
GOOGLE_VOCES_MEZCLA = [x.strip() for x in os.environ.get(
    "GOOGLE_VOCES_MEZCLA",
    "en-US-Neural2-F,en-US-Neural2-D,en-US-Neural2-C,en-US-Neural2-J,en-GB-Neural2-B",
).split(",") if x.strip()]
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


def _motor():
    """Calienta el primer motor de Kokoro al arrancar (no hace nada si se usa Google)."""
    if MOTOR == "google" or not os.path.exists(MODELO):
        return
    m = _tomar_motor()
    _soltar_motor(m)


def _soltar_motor(m):
    with _cv:
        _libres.append(m)
        _cv.notify()


def _google_mp3(texto, nombre, velocidad):
    """Pide el audio a Google Cloud Text-to-Speech (REST). Devuelve bytes mp3."""
    if not GOOGLE_KEY:
        raise RuntimeError("Falta GOOGLE_TTS_API_KEY")
    idioma = "-".join(nombre.split("-")[:2])
    cuerpo = json.dumps({
        "input": {"text": texto},
        "voice": {"languageCode": idioma, "name": nombre},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": velocidad, "sampleRateHertz": 24000},
    }).encode()
    req = urllib.request.Request(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        data=cuerpo,
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": GOOGLE_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            datos = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"Google TTS {e.code}: {msg[:200]}")
    return base64.b64decode(datos["audioContent"])


def _voz_google(voz, texto=""):
    if GOOGLE_VOZ_DEFECTO:
        return GOOGLE_VOZ_DEFECTO
    if voz == "af_heart" and GOOGLE_VOCES_MEZCLA and texto:   # voz por defecto de la app: se mezcla
        n = int(hashlib.sha256(texto.encode()).hexdigest(), 16)
        return GOOGLE_VOCES_MEZCLA[n % len(GOOGLE_VOCES_MEZCLA)]
    return GOOGLE_VOCES.get(voz, "en-US-Neural2-F")


def _guardar(ruta, datos):
    os.makedirs(CACHE, exist_ok=True)
    tmp = ruta + "." + uuid.uuid4().hex[:8] + ".tmp"
    with open(tmp, "wb") as f:
        f.write(datos)
    os.replace(tmp, ruta)  # escritura atómica


def audio_mp3(texto: str, voz: str = "af_heart", velocidad: float = 0.95, motor: str = None,
              nombre_google: str = None, usar_cache: bool = True) -> bytes:
    texto = " ".join(texto.split())
    if not texto:
        raise ValueError("Texto vacío")
    if len(texto) > MAX_CARACTERES:
        raise ValueError(f"Texto demasiado largo (máximo {MAX_CARACTERES} caracteres)")
    if voz not in VOCES_OK:
        voz = "af_heart"
    velocidad = min(1.3, max(0.7, velocidad))
    motor = (motor or MOTOR)
    if motor == "google":
        nombre = nombre_google or _voz_google(voz, texto)
        ruta = _ruta_clave(f"g|{nombre}|{velocidad}|{texto}")
        if usar_cache and os.path.exists(ruta):
            with open(ruta, "rb") as f:
                return f.read()
        try:
            datos = _google_mp3(texto, nombre, velocidad)
        except Exception as e:
            if nombre_google or not os.path.exists(MODELO):
                raise ValueError(f"No se pudo generar con Google: {e}")
            # respaldo: Kokoro local (más lento) si hay modelo instalado
            return audio_mp3(texto, voz, velocidad, motor="kokoro")
        if usar_cache:
            _guardar(ruta, datos)
        return datos
    return _kokoro_mp3(texto, voz, velocidad)


def _ruta_clave(clave):
    return os.path.join(CACHE, hashlib.sha256(clave.encode()).hexdigest() + ".mp3")


def _kokoro_mp3(texto, voz, velocidad):
    ruta = _ruta_clave(f"{voz}|{velocidad}|{texto}")
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
_hilos = []
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
    if MOTOR == "google":
        return _ruta_clave(f"g|{_voz_google(voz, texto)}|{velocidad}|{texto}")
    return _ruta_clave(f"{voz}|{velocidad}|{texto}")


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
            n = int(os.environ.get("GOOGLE_HILOS", "4")) if MOTOR == "google" else 1
            while len(_hilos) < n:
                h = threading.Thread(target=_trabajador, daemon=True)
                h.start()
                _hilos.append(h)
    return nuevos, listos
