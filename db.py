"""Base de datos del registro de práctica (SQLite) y cálculos de reporte."""
import datetime as dt
import hashlib
import hmac
import os
import secrets
import sqlite3

DB_PATH = os.environ.get("REGISTRO_DB", "registro.db")
LIMA = dt.timezone(dt.timedelta(hours=-5))
ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I para evitar confusiones
MAX_MIN_SESION = 480  # solo un límite técnico (8 h) para evitar errores de tipeo; no hay tope diario
PAUSA_MAX_SEG = 600  # más de 10 min entre avisos = otra sesión


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def iniciar():
    with conectar() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS empresas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                prefijo TEXT NOT NULL UNIQUE,
                token_gerente TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS alumnos (
                codigo TEXT PRIMARY KEY,
                empresa_id INTEGER NOT NULL REFERENCES empresas(id),
                nombre TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL REFERENCES alumnos(codigo),
                ts INTEGER NOT NULL,
                tipo TEXT NOT NULL,
                aciertos INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                pronunciacion INTEGER NOT NULL DEFAULT 0,
                comprension INTEGER NOT NULL DEFAULT 0,
                nota TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_eventos_codigo_ts ON eventos(codigo, ts);
            CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS uso_voz (mes TEXT PRIMARY KEY, caracteres INTEGER NOT NULL DEFAULT 0, pedidos INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS duenos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa_id INTEGER NOT NULL REFERENCES empresas(id),
                nombre TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                clave_hash TEXT NOT NULL,
                creado_ts INTEGER NOT NULL,
                ultimo_ingreso_ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS evaluaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL REFERENCES alumnos(codigo),
                ts INTEGER NOT NULL,
                nivel TEXT NOT NULL,
                fortalezas TEXT NOT NULL DEFAULT '',
                reforzar TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sesiones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL REFERENCES alumnos(codigo),
                inicio_ts INTEGER NOT NULL,
                minutos INTEGER NOT NULL,
                tema TEXT NOT NULL DEFAULT '',
                fin_ts INTEGER
            );
            """
        )
        # Columnas añadidas después (bases ya creadas): correo y fecha de envío
        for tabla, col in (
            ("alumnos", "email TEXT NOT NULL DEFAULT ''"),
            ("alumnos", "enviado_ts INTEGER"),
            ("empresas", "minutos_sesion INTEGER NOT NULL DEFAULT 15"),
            ("alumnos", "compromiso_min INTEGER NOT NULL DEFAULT 0"),
            ("alumnos", "creado_ts INTEGER"),
        ):
            try:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        # Alumnos anteriores: su inscripción es su primera actividad (o hoy si no tienen)
        con.execute(
            "UPDATE alumnos SET creado_ts = COALESCE((SELECT MIN(ts) FROM eventos e WHERE e.codigo=alumnos.codigo),"
            " CAST(strftime('%s','now') AS INTEGER)) WHERE creado_ts IS NULL"
        )


def _codigo_nuevo(prefijo):
    return prefijo + "-" + "".join(secrets.choice(ALFABETO) for _ in range(4))


def crear_empresa(nombre, prefijo, cantidad):
    """Crea la empresa y 'cantidad' códigos aleatorios. Devuelve (token_gerente, [códigos])."""
    prefijo = prefijo.strip().upper()
    token = secrets.token_urlsafe(18)
    codigos = []
    with conectar() as con:
        cur = con.execute(
            "INSERT INTO empresas (nombre, prefijo, token_gerente) VALUES (?,?,?)",
            (nombre, prefijo, token),
        )
        empresa_id = cur.lastrowid
        while len(codigos) < cantidad:
            c = _codigo_nuevo(prefijo)
            try:
                con.execute(
                    "INSERT INTO alumnos (codigo, empresa_id, creado_ts) VALUES (?,?,?)", (c, empresa_id, _ahora())
                )
                codigos.append(c)
            except sqlite3.IntegrityError:
                continue  # código repetido: se genera otro
    return token, codigos


def alumno_existe(codigo):
    with conectar() as con:
        return con.execute(
            "SELECT 1 FROM alumnos WHERE codigo=?", (codigo,)
        ).fetchone() is not None


def _acotar(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def registrar(codigo, tipo, aciertos=0, total=0, pronunciacion=0, comprension=0, nota=""):
    """Guarda un evento con la hora del servidor (no la que diga el cliente)."""
    ahora = int(dt.datetime.now(dt.timezone.utc).timestamp())
    with conectar() as con:
        con.execute(
            "INSERT INTO eventos (codigo, ts, tipo, aciertos, total, pronunciacion, comprension, nota)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                codigo,
                ahora,
                str(tipo)[:40],
                _acotar(aciertos, 0, 100),
                _acotar(total, 0, 100),
                _acotar(pronunciacion, 0, 5),
                _acotar(comprension, 0, 5),
                str(nota)[:300],
            ),
        )
    return ahora


def _dia(ts):
    return dt.datetime.fromtimestamp(ts, LIMA).date()


def minutos(tiempos):
    """Minutos de práctica medidos por el servidor: avisos separados por <=10 min son una sesión."""
    total = 0.0
    inicio = prev = None
    for t in sorted(tiempos):
        if prev is None or t - prev > PAUSA_MAX_SEG:
            if inicio is not None:
                total += (prev - inicio) / 60 + 1
            inicio = t
        prev = t
    if inicio is not None:
        total += (prev - inicio) / 60 + 1
    return round(total)


def racha(dias, hoy):
    """Días seguidos practicados, contando hasta hoy o ayer."""
    dias = set(dias)
    d = hoy if hoy in dias else hoy - dt.timedelta(days=1)
    n = 0
    while d in dias:
        n += 1
        d -= dt.timedelta(days=1)
    return n


def resumen_alumno(con, codigo, hoy=None):
    hoy = hoy or dt.datetime.now(LIMA).date()
    desde = int(dt.datetime.combine(hoy - dt.timedelta(days=29), dt.time(), LIMA).timestamp())
    filas = con.execute(
        "SELECT ts, pronunciacion, comprension FROM eventos WHERE codigo=? AND ts>=? ORDER BY ts",
        (codigo, desde),
    ).fetchall()
    dias = [_dia(f["ts"]) for f in filas]
    semana = hoy - dt.timedelta(days=6)
    t_semana = [f["ts"] for f in filas if _dia(f["ts"]) >= semana]
    pron = [f["pronunciacion"] for f in filas if f["pronunciacion"]]
    comp = [f["comprension"] for f in filas if f["comprension"]]
    ultima = max(dias) if dias else None
    fila = con.execute("SELECT nombre, compromiso_min FROM alumnos WHERE codigo=?", (codigo,)).fetchone()
    practicados = set(dias)
    base = {
        "codigo": codigo,
        "nombre": (fila["nombre"] if fila else "") or "",
        "compromiso": (fila["compromiso_min"] if fila else 0) or 0,
        "dias7": [(hoy - dt.timedelta(days=k)) in practicados for k in range(6, -1, -1)],
        "dias_30": len(set(dias)),
        "racha": racha(dias, hoy),
        "min_7d": minutos(t_semana),
        "ultima": ultima,
        "sin_practicar": (hoy - ultima).days if ultima else None,
        "pron": round(sum(pron) / len(pron), 1) if pron else None,
        "comp": round(sum(comp) / len(comp), 1) if comp else None,
    }
    base.update(metricas(con, codigo, hoy))
    return base


CONV = ("conversacion_inicio", "conversacion", "pulso")


def _min_sesion(f, ahora):
    fin = min(f["fin_ts"] or ahora, f["inicio_ts"] + f["minutos"] * 60)
    return max(0, fin - f["inicio_ts"]) / 60


def metricas(con, codigo, hoy):
    """Asistencia = días en que habló con el coach. Los ejercicios son complementarios."""
    ahora = _ahora()
    ev = con.execute(
        "SELECT ts, tipo, aciertos, total, pronunciacion, comprension, nota FROM eventos WHERE codigo=? ORDER BY ts",
        (codigo,),
    ).fetchall()
    fila_a = con.execute("SELECT creado_ts FROM alumnos WHERE codigo=?", (codigo,)).fetchone()
    primeros = [x for x in (fila_a["creado_ts"] if fila_a else None, ev[0]["ts"] if ev else None) if x]
    inicio = _dia(min(primeros)) if primeros else hoy
    transcurridos = (hoy - inicio).days + 1
    ventana = min(transcurridos, 30)
    desde = hoy - dt.timedelta(days=ventana - 1)
    d_habla = {_dia(e["ts"]) for e in ev if e["tipo"] in CONV}
    d_ej = {_dia(e["ts"]) for e in ev if e["tipo"] not in CONV}
    habla_v = {d for d in d_habla if d >= desde}
    ej_v = {d for d in d_ej if d >= desde}
    ses = con.execute("SELECT inicio_ts, minutos, fin_ts FROM sesiones WHERE codigo=?", (codigo,)).fetchall()
    sem = hoy - dt.timedelta(days=6)
    min_total = sum(_min_sesion(f, ahora) for f in ses if _dia(f["inicio_ts"]) >= desde)
    min_7 = sum(_min_sesion(f, ahora) for f in ses if _dia(f["inicio_ts"]) >= sem)
    comp = con.execute("SELECT compromiso_min FROM alumnos WHERE codigo=?", (codigo,)).fetchone()["compromiso_min"]
    cumpl = round(100 * min_total / (comp * ventana)) if comp else None

    ej = [e for e in ev if e["tipo"] not in CONV and e["total"] > 0]
    def pct(lista):
        t = sum(e["total"] for e in lista)
        return round(100 * sum(e["aciertos"] for e in lista) / t) if t else None
    a7 = pct([e for e in ej if _dia(e["ts"]) >= sem])
    a_prev = pct([e for e in ej if hoy - dt.timedelta(days=13) <= _dia(e["ts"]) < sem])
    tend = (a7 - a_prev) if (a7 is not None and a_prev is not None) else None
    temas = {}
    for e in ej:
        if e["nota"]:
            a, t = temas.get(e["nota"], (0, 0))
            temas[e["nota"]] = (a + e["aciertos"], t + e["total"])
    lista = [(n, round(100 * a / t), t) for n, (a, t) in temas.items() if t >= 4]
    debiles = [x for x in sorted(lista, key=lambda x: x[1]) if x[1] < 60][:3]
    fuertes = [x for x in sorted(lista, key=lambda x: -x[1]) if x[1] >= 80][:3]
    # Calendario desde el día de inscripción: 30 casillas (Día 1 = inscripción); si ya pasaron más
    # de 30 días se muestran los últimos 30.
    corrim = max(0, transcurridos - 30)
    cal30 = []
    for k in range(30):
        dia = inicio + dt.timedelta(days=corrim + k)
        cal30.append(None if dia > hoy else (2 if dia in d_habla else (1 if dia in d_ej else 0)))
    semanas = []
    for w in range(3, -1, -1):
        fin_w = hoy - dt.timedelta(days=7 * w)
        ini_w = fin_w - dt.timedelta(days=6)
        if fin_w < inicio:
            continue
        sem_ej = [e for e in ej if ini_w <= _dia(e["ts"]) <= fin_w]
        semanas.append({
            "hasta": fin_w,
            "dias_habla": sum(1 for x in d_habla if ini_w <= x <= fin_w),
            "dias_ej": sum(1 for x in d_ej if ini_w <= x <= fin_w),
            "min": round(sum(_min_sesion(f, ahora) for f in ses if ini_w <= _dia(f["inicio_ts"]) <= fin_w)),
            "acierto": pct(sem_ej),
        })
    mejora = []
    for nombre in temas:
        lst = [e for e in ej if e["nota"] == nombre]
        if sum(e["total"] for e in lst) < 8:
            continue
        mid = len(lst) // 2
        antes, ahora_ = pct(lst[:mid]), pct(lst[mid:])
        if antes is not None and ahora_ is not None and ahora_ - antes >= 15:
            mejora.append((nombre, antes, ahora_))
    mejora.sort(key=lambda x: x[1] - x[2])
    pr = [e["pronunciacion"] for e in ev if e["pronunciacion"]]
    cp = [e["comprension"] for e in ev if e["comprension"]]
    nv = con.execute("SELECT nivel FROM evaluaciones WHERE codigo=? ORDER BY ts", (codigo,)).fetchall()
    return {
        "inicio": inicio, "transcurridos": transcurridos, "dias_habla": len(habla_v),
        "asistencia": round(100 * len(habla_v) / ventana) if ventana else 0, "racha": racha(d_habla, hoy),
        "dias7": [(hoy - dt.timedelta(days=k)) in d_habla for k in range(6, -1, -1)],
        "dias7_ej": [(hoy - dt.timedelta(days=k)) in d_ej for k in range(6, -1, -1)],
        "min_habla_7d": round(min_7), "min_habla_total": round(min_total), "cumplimiento": cumpl,
        "dias_ej": len(ej_v), "acierto": pct(ej), "acierto_tend": tend,
        "temas_debiles": debiles, "temas_fuertes": fuertes,
        "pron": round(sum(pr) / len(pr), 1) if pr else None, "comp": round(sum(cp) / len(cp), 1) if cp else None,
        "nivel_ini": nv[0]["nivel"] if nv else None, "nivel_act": nv[-1]["nivel"] if nv else None,
        "habla_hoy": hoy in d_habla,
        "sin_hablar": (hoy - max(d_habla)).days if d_habla else None,
        "cal30": cal30, "cal_desde": corrim + 1, "empezo": bool(ev), "inscrito": inicio, "semanas": semanas, "temas_mejora": mejora[:3],
    }


def registrar_evaluacion(codigo, nivel, fortalezas="", reforzar=""):
    with conectar() as con:
        con.execute(
            "INSERT INTO evaluaciones (codigo, ts, nivel, fortalezas, reforzar) VALUES (?,?,?,?,?)",
            (codigo, _ahora(), str(nivel).strip()[:20], str(fortalezas)[:200], str(reforzar)[:200]),
        )


def resumen_empresa(token):
    with conectar() as con:
        emp = con.execute(
            "SELECT * FROM empresas WHERE token_gerente=?", (token,)
        ).fetchone()
    return _resumen(emp)


def resumen_empresa_id(empresa_id):
    with conectar() as con:
        emp = con.execute("SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone()
    return _resumen(emp)


def _resumen(emp):
    if not emp:
        return None
    with conectar() as con:
        codigos = [
            r["codigo"]
            for r in con.execute(
                "SELECT codigo FROM alumnos WHERE empresa_id=? ORDER BY codigo", (emp["id"],)
            )
        ]
        return {
            "empresa": emp["nombre"],
            "alumnos": [resumen_alumno(con, c) for c in codigos],
        }


# ---------- Administración (panel web) ----------

def crear_empresa_vacia(nombre, prefijo):
    """Crea una empresa sin alumnos. Devuelve su id."""
    prefijo = prefijo.strip().upper()
    with conectar() as con:
        cur = con.execute(
            "INSERT INTO empresas (nombre, prefijo, token_gerente) VALUES (?,?,?)",
            (nombre.strip(), prefijo, secrets.token_urlsafe(18)),
        )
        return cur.lastrowid


def listar_empresas():
    with conectar() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT e.id, e.nombre, e.prefijo, e.token_gerente,"
                " (SELECT COUNT(*) FROM alumnos a WHERE a.empresa_id=e.id) AS n"
                " FROM empresas e ORDER BY e.id DESC"
            )
        ]


def empresa_por_id(empresa_id):
    with conectar() as con:
        r = con.execute("SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone()
        return dict(r) if r else None


def agregar_alumno(empresa_id, nombre, email):
    """Crea un alumno con código nuevo. Devuelve el código."""
    emp = empresa_por_id(empresa_id)
    if not emp:
        raise ValueError("La empresa no existe")
    with conectar() as con:
        while True:
            c = _codigo_nuevo(emp["prefijo"])
            try:
                con.execute(
                    "INSERT INTO alumnos (codigo, empresa_id, nombre, email, creado_ts) VALUES (?,?,?,?,?)",
                    (c, empresa_id, nombre.strip()[:80], email.strip()[:120], _ahora()),
                )
                return c
            except sqlite3.IntegrityError:
                continue


def alumnos_de_empresa(empresa_id):
    """Alumnos con su resumen de práctica, correo y fecha de envío."""
    with conectar() as con:
        filas = con.execute(
            "SELECT codigo, nombre, email, enviado_ts, compromiso_min FROM alumnos WHERE empresa_id=? ORDER BY nombre, codigo",
            (empresa_id,),
        ).fetchall()
        salida = []
        for f in filas:
            r = resumen_alumno(con, f["codigo"])
            r["email"] = f["email"]
            r["enviado_ts"] = f["enviado_ts"]
            r["compromiso"] = f["compromiso_min"]
            salida.append(r)
        return salida


def alumno(codigo):
    with conectar() as con:
        r = con.execute(
            "SELECT a.codigo, a.nombre, a.email, e.nombre AS empresa FROM alumnos a"
            " JOIN empresas e ON e.id=a.empresa_id WHERE a.codigo=?",
            (codigo,),
        ).fetchone()
        return dict(r) if r else None


def marcar_enviado(codigo):
    ahora = int(dt.datetime.now(dt.timezone.utc).timestamp())
    with conectar() as con:
        con.execute("UPDATE alumnos SET enviado_ts=? WHERE codigo=?", (ahora, codigo))


def quitar_alumno(codigo):
    with conectar() as con:
        con.execute("DELETE FROM eventos WHERE codigo=?", (codigo,))
        con.execute("DELETE FROM alumnos WHERE codigo=?", (codigo,))


# ---------- Conversaciones por voz con duración programada ----------

def _ahora():
    return int(dt.datetime.now(dt.timezone.utc).timestamp())


def minutos_programados(codigo):
    """Duración por defecto: el compromiso del alumno; si no lo dijo, el de su empresa."""
    with conectar() as con:
        r = con.execute(
            "SELECT a.compromiso_min, e.minutos_sesion FROM alumnos a JOIN empresas e ON e.id=a.empresa_id"
            " WHERE a.codigo=?",
            (codigo,),
        ).fetchone()
    if not r:
        return 15
    return int(r["compromiso_min"] or r["minutos_sesion"] or 15)


def fijar_compromiso(codigo, minutos):
    """Minutos diarios que el alumno dijo en su entrevista (mínimo 5, sin tope diario)."""
    m = _acotar(minutos, 5, MAX_MIN_SESION)
    with conectar() as con:
        con.execute("UPDATE alumnos SET compromiso_min=? WHERE codigo=?", (m, codigo))
    return m


def fijar_minutos_sesion(empresa_id, minutos):
    with conectar() as con:
        con.execute(
            "UPDATE empresas SET minutos_sesion=? WHERE id=?", (_acotar(minutos, 5, MAX_MIN_SESION), empresa_id)
        )


def minutos_usados_hoy(codigo):
    """Minutos de conversación ya usados hoy (hora de Lima), sin pasar de lo programado."""
    hoy = dt.datetime.now(LIMA).date()
    desde = int(dt.datetime.combine(hoy, dt.time(), LIMA).timestamp())
    ahora = _ahora()
    with conectar() as con:
        filas = con.execute(
            "SELECT inicio_ts, minutos, fin_ts FROM sesiones WHERE codigo=? AND inicio_ts>=?",
            (codigo, desde),
        ).fetchall()
    total = 0.0
    for f in filas:
        fin = min(f["fin_ts"] or ahora, f["inicio_ts"] + f["minutos"] * 60)
        total += max(0, fin - f["inicio_ts"]) / 60
    return round(total)


def iniciar_sesion(codigo, minutos, tema=""):
    """Abre una conversación (cierra las abiertas). Devuelve (minutos, False).
    No hay tope diario: la persona practica lo que quiera."""
    m = _acotar(minutos, 5, MAX_MIN_SESION) if minutos else minutos_programados(codigo)
    with conectar() as con:
        con.execute("UPDATE sesiones SET fin_ts=? WHERE codigo=? AND fin_ts IS NULL", (_ahora(), codigo))
        con.execute(
            "INSERT INTO sesiones (codigo, inicio_ts, minutos, tema) VALUES (?,?,?,?)",
            (codigo, _ahora(), m, str(tema)[:80]),
        )
    registrar(codigo, "conversacion_inicio")
    return m, False


def sesion_abierta(codigo):
    """Última conversación sin cerrar y con menos de 3 horas, o None."""
    with conectar() as con:
        r = con.execute(
            "SELECT * FROM sesiones WHERE codigo=? AND fin_ts IS NULL ORDER BY id DESC LIMIT 1",
            (codigo,),
        ).fetchone()
    if r and _ahora() - r["inicio_ts"] < 3 * 3600:
        return dict(r)
    return None


def cerrar_sesion(codigo):
    with conectar() as con:
        con.execute("UPDATE sesiones SET fin_ts=? WHERE codigo=? AND fin_ts IS NULL", (_ahora(), codigo))


# ---------- Cuentas de los dueños de empresa ----------

_ALFABETO = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin letras ambiguas


def clave_temporal():
    return "".join(secrets.choice(_ALFABETO) for _ in range(10))


def _hash_clave(clave, salt=None, iters=200_000):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode(), salt.encode(), iters).hex()
    return f"pbkdf2${iters}${salt}${h}"


def _verifica_clave(clave, guardado):
    try:
        _, iters, salt, h = guardado.split("$")
        return hmac.compare_digest(_hash_clave(clave, salt, int(iters)).split("$")[3], h)
    except Exception:
        return False


def secreto_sesion():
    """Clave para firmar las sesiones; se crea una vez y se guarda en la base."""
    with conectar() as con:
        r = con.execute("SELECT valor FROM ajustes WHERE clave='secreto'").fetchone()
        if r:
            return r["valor"]
        s = secrets.token_hex(32)
        con.execute("INSERT INTO ajustes (clave, valor) VALUES ('secreto', ?)", (s,))
        return s


def crear_dueno(empresa_id, nombre, email):
    """Crea la cuenta de un dueño. Devuelve (id, clave temporal)."""
    clave = clave_temporal()
    with conectar() as con:
        try:
            cur = con.execute(
                "INSERT INTO duenos (empresa_id, nombre, email, clave_hash, creado_ts) VALUES (?,?,?,?,?)",
                (empresa_id, nombre.strip()[:80], email.strip()[:120], _hash_clave(clave), _ahora()),
            )
        except sqlite3.IntegrityError:
            raise ValueError("Ese correo ya tiene una cuenta")
        return cur.lastrowid, clave


def reiniciar_clave_dueno(dueno_id):
    clave = clave_temporal()
    with conectar() as con:
        con.execute("UPDATE duenos SET clave_hash=? WHERE id=?", (_hash_clave(clave), dueno_id))
    return clave


def cambiar_clave_dueno(dueno_id, nueva):
    with conectar() as con:
        con.execute("UPDATE duenos SET clave_hash=? WHERE id=?", (_hash_clave(nueva), dueno_id))


def autenticar_dueno(email, clave):
    with conectar() as con:
        r = con.execute("SELECT * FROM duenos WHERE email=?", (email.strip(),)).fetchone()
        if not r:
            _hash_clave(clave)  # gasta el mismo tiempo para no revelar si el correo existe
            return None
        if not _verifica_clave(clave, r["clave_hash"]):
            return None
        con.execute("UPDATE duenos SET ultimo_ingreso_ts=? WHERE id=?", (_ahora(), r["id"]))
        return dict(r)


def dueno_por_id(dueno_id):
    with conectar() as con:
        r = con.execute(
            "SELECT d.*, e.nombre AS empresa FROM duenos d JOIN empresas e ON e.id=d.empresa_id WHERE d.id=?",
            (dueno_id,),
        ).fetchone()
        return dict(r) if r else None


def listar_duenos(empresa_id):
    with conectar() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT id, nombre, email, ultimo_ingreso_ts FROM duenos WHERE empresa_id=? ORDER BY id",
                (empresa_id,),
            )
        ]


def quitar_dueno(dueno_id):
    with conectar() as con:
        con.execute("DELETE FROM duenos WHERE id=?", (dueno_id,))


# ---------- Uso de la voz de Google (caracteres enviados) ----------

def _mes_actual():
    return dt.datetime.now(LIMA).strftime("%Y-%m")


def sumar_uso_voz(caracteres):
    with conectar() as con:
        con.execute(
            "INSERT INTO uso_voz (mes, caracteres, pedidos) VALUES (?,?,1)"
            " ON CONFLICT(mes) DO UPDATE SET caracteres=caracteres+excluded.caracteres, pedidos=pedidos+1",
            (_mes_actual(), int(caracteres)),
        )


def uso_voz_mes():
    """(caracteres, pedidos) enviados a Google en el mes actual."""
    with conectar() as con:
        r = con.execute("SELECT caracteres, pedidos FROM uso_voz WHERE mes=?", (_mes_actual(),)).fetchone()
    return (int(r["caracteres"]), int(r["pedidos"])) if r else (0, 0)
