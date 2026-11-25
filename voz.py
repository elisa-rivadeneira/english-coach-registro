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
GOOGLE_KEY = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()   # secreto: solo en Easypanel
# Voz de Google por defecto para cada voz de Kokoro (se puede cambiar con GOOGLE_VOZ_DEFECTO, p. ej. en-US-Neural2-C)
GOOGLE_VOCES = {
    "af_heart": "en-US-Neural2-F", "af_bella": "en-US-Neural2-H", "af_nicole": "en-US-Neural2-C",
    "am_adam": "en-US-Neural2-D", "am_michael": "en-US-Neural2-J",
    "bf_emma": "en-GB-Neural2-A", "bm_george": "en-GB-Neural2-B",
}
# ---------- Configuración: se cambia en el panel (Configuración); si no hay valor guardado, se usa la variable
# de entorno y, si tampoco, el valor por defecto de esta tabla. Los secretos (la clave de Google) NO van aquí. ----------
MEZCLA_DEFECTO = "en-US-Neural2-F,en-US-Neural2-D,en-US-Neural2-C,en-US-Neural2-J,en-GB-Neural2-B"
CONFIG = {
    # clave: (variable de entorno opcional, valor por defecto)
    "motor": ("TTS_MOTOR", "kokoro"),
    "tope_caracteres_mes": ("GOOGLE_TOPE_CARACTERES_MES", "2000000"),
    "voces_mezcla": ("GOOGLE_VOCES_MEZCLA", MEZCLA_DEFECTO),
    "voz_unica": ("GOOGLE_VOZ_DEFECTO", ""),
    "google_hilos": ("GOOGLE_HILOS", "4"),
    "motores_kokoro": ("MOTORES", "1"),
    "gratis_caracteres": (None, "1000000"),
    "precio_millon_usd": (None, "16"),
    "aviso_pct": (None, "80"),
}
_memo = {}


def cfg(clave):
    """Valor de la configuración (panel > variable de entorno > defecto). Se relee cada 5 segundos."""
    ahora = __import__("time").time()
    h = _memo.get(clave)
    if h and ahora - h[0] < 5:
        return h[1]
    env, defecto = CONFIG[clave]
    val = None
    try:
        import db as _db
        val = _db.ajuste_get(clave)
    except Exception:
        val = None
    if val is None and env:
        val = os.environ.get(env)
    if val is None or str(val).strip() == "":
        val = defecto
    val = str(val).strip()
    _memo[clave] = (ahora, val)
    return val


def olvidar_cache_config():
    _memo.clear()


def _entero(clave, minimo=0):
    try:
        return max(minimo, int(float(cfg(clave))))
    except ValueError:
        return max(minimo, int(float(CONFIG[clave][1])))


def motor_activo():
    m = cfg("motor").lower()
    return "google" if m == "google" else "kokoro"


def tope_caracteres():
    return _entero("tope_caracteres_mes")


def voces_mezcla():
    return [x.strip() for x in cfg("voces_mezcla").split(",") if x.strip()]


VOCES_OK = {"af_heart", "af_bella", "af_nicole", "am_adam", "am_michael", "bf_emma", "bm_george"}

# Motores de voz en paralelo (MOTORES en el entorno; 1 por defecto). Cada motor ocupa RAM y CPU:
# súbelo solo si la prueba de carga (prueba_carga.py) muestra CPU libre.
_motores = []
_libres = []
_cv = threading.Condition()


def _tomar_motor():
    """Toma un motor libre (crea uno nuevo si aún no se llegó al máximo); si no, espera en fila."""
    with _cv:
        while True:
            if _libres:
                return _libres.pop()
            if len(_motores) < _entero("motores_kokoro", 1):
                from kokoro_onnx import Kokoro
                m = Kokoro(MODELO, VOCES)
                _motores.append(m)
                return m
            _cv.wait()


def _motor():
    """Calienta el primer motor de Kokoro al arrancar (no hace nada si se usa Google)."""
    if motor_activo() == "google" or not os.path.exists(MODELO):
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
    unica = cfg("voz_unica")
    if unica:
        return unica
    mezcla = voces_mezcla()
    if voz == "af_heart" and mezcla and texto:   # voz por defecto de la app: se mezcla
        n = int(hashlib.sha256(texto.encode()).hexdigest(), 16)
        return mezcla[n % len(mezcla)]
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
    motor = (motor or motor_activo())
    if motor == "google":
        nombre = nombre_google or _voz_google(voz, texto)
        ruta = _ruta_clave(f"g|{nombre}|{velocidad}|{texto}")
        if usar_cache and os.path.exists(ruta):
            with open(ruta, "rb") as f:
                return f.read()
        try:
            import db as _db
            usado = _db.uso_voz_mes()[0]
        except Exception:  # si el contador falla, la voz sigue funcionando
            _db, usado = None, 0
        try:
            tope = tope_caracteres()
            if tope and usado + len(texto) > tope:
                raise RuntimeError("tope mensual de caracteres alcanzado")
            datos = _google_mp3(texto, nombre, velocidad)
        except Exception as e:
            if nombre_google or not os.path.exists(MODELO):
                raise ValueError(f"No se pudo generar con Google: {e}")
            # respaldo: Kokoro local (más lento) si hay modelo instalado
            return audio_mp3(texto, voz, velocidad, motor="kokoro")
        if _db:
            try:
                _db.sumar_uso_voz(len(texto))
            except Exception:
                pass
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
    if motor_activo() == "google":
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
            n = _entero("google_hilos", 1) if motor_activo() == "google" else 1
            while len(_hilos) < n:
                h = threading.Thread(target=_trabajador, daemon=True)
                h.start()
                _hilos.append(h)
    return nuevos, listos
