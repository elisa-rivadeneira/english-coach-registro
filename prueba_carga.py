"""Prueba de carga del motor de voz. Córrela EN EL SERVIDOR (terminal de Easypanel):
    python prueba_carga.py
Mide cuánto tarda una frase nueva, cuántas frases por minuto aguanta con 1, 2 y 4 pedidos a la vez,
y con eso calcula cuántos alumnos/lecciones soporta. No toca la base de datos ni el caché real."""
import os, sys, tempfile, time, threading, uuid

os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="prueba_voz_")  # caché temporal: no ensucia el real
import voz

FRASES = ["Could you tell me your name and your room number, please?",
          "I would like to book a table for four people tonight.",
          "We are fully booked this weekend, but I can check next week.",
          "Let me check and get back to you by Friday afternoon."]
FRASES_POR_LECCION = int(os.environ.get("FRASES_LECCION", "30"))

def unica(i):  # frase distinta cada vez, para que nunca salga del caché
    return f"{FRASES[i % len(FRASES)]} Option {uuid.uuid4().hex[:6]}"

def ronda(hilos, total):
    t0 = time.time(); cola = list(range(total)); lock = threading.Lock(); lat = []
    def w():
        while True:
            with lock:
                if not cola: return
                i = cola.pop()
            a = time.time(); voz.audio_mp3(unica(i)); lat.append(time.time() - a)
    ts = [threading.Thread(target=w) for _ in range(hilos)]
    [t.start() for t in ts]; [t.join() for t in ts]
    dur = time.time() - t0
    return total / dur * 60, sum(lat) / len(lat), max(lat)

print("CPUs:", os.cpu_count(), "| MOTORES =", voz.cfg("motores_kokoro"))
try:
    mem = [l for l in open("/proc/meminfo") if l.startswith(("MemTotal", "MemAvailable"))]
    print(" ".join(x.strip() for x in mem))
except Exception: pass
print("Calentando el motor (primera carga)...")
a = time.time(); voz.audio_mp3(unica(0)); print(f"primera frase (incluye cargar el modelo): {time.time()-a:.1f} s")
mejor = 0
for hilos in (1, 2, 4):
    fpm, media, peor = ronda(hilos, 8 if hilos == 1 else 12)
    mejor = max(mejor, fpm)
    print(f"{hilos} pedido(s) a la vez: {fpm:.0f} frases/min · espera media {media:.1f} s · peor {peor:.1f} s")
lec_h = mejor * 60 / FRASES_POR_LECCION
print(f"\nCapacidad aprox.: {mejor:.0f} frases nuevas/min = {lec_h:.0f} lecciones nuevas por hora (con {FRASES_POR_LECCION} frases por lección)")
print("Esto es el peor caso (nada en caché). Con frases repetidas la capacidad real es mucho mayor.")
print(f"Si 10 personas abren su lección a la vez, la última espera ~{10*FRASES_POR_LECCION/max(mejor,1):.1f} min.")
