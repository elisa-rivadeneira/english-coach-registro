"""Base de datos del registro de práctica (SQLite) y cálculos de reporte."""
import datetime as dt
import os
import secrets
import sqlite3

DB_PATH = os.environ.get("REGISTRO_DB", "registro.db")
LIMA = dt.timezone(dt.timedelta(hours=-5))
ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I para evitar confusiones
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
        ):
            try:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass


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
                    "INSERT INTO alumnos (codigo, empresa_id) VALUES (?,?)", (c, empresa_id)
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
    fila = con.execute("SELECT nombre FROM alumnos WHERE codigo=?", (codigo,)).fetchone()
    practicados = set(dias)
    return {
        "codigo": codigo,
        "nombre": (fila["nombre"] if fila else "") or "",
        "dias7": [(hoy - dt.timedelta(days=k)) in practicados for k in range(6, -1, -1)],
        "dias_30": len(set(dias)),
        "racha": racha(dias, hoy),
        "min_7d": minutos(t_semana),
        "ultima": ultima,
        "sin_practicar": (hoy - ultima).days if ultima else None,
        "pron": round(sum(pron) / len(pron), 1) if pron else None,
        "comp": round(sum(comp) / len(comp), 1) if comp else None,
    }


def resumen_empresa(token):
    with conectar() as con:
        emp = con.execute(
            "SELECT * FROM empresas WHERE token_gerente=?", (token,)
        ).fetchone()
        if not emp:
            return None
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
                    "INSERT INTO alumnos (codigo, empresa_id, nombre, email) VALUES (?,?,?,?)",
                    (c, empresa_id, nombre.strip()[:80], email.strip()[:120]),
                )
                return c
            except sqlite3.IntegrityError:
                continue


def alumnos_de_empresa(empresa_id):
    """Alumnos con su resumen de práctica, correo y fecha de envío."""
    with conectar() as con:
        filas = con.execute(
            "SELECT codigo, nombre, email, enviado_ts FROM alumnos WHERE empresa_id=? ORDER BY nombre, codigo",
            (empresa_id,),
        ).fetchall()
        salida = []
        for f in filas:
            r = resumen_alumno(con, f["codigo"])
            r["email"] = f["email"]
            r["enviado_ts"] = f["enviado_ts"]
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
    """Duración de conversación configurada para la empresa del alumno."""
    with conectar() as con:
        r = con.execute(
            "SELECT e.minutos_sesion FROM alumnos a JOIN empresas e ON e.id=a.empresa_id WHERE a.codigo=?",
            (codigo,),
        ).fetchone()
        return int(r["minutos_sesion"]) if r else 15


def fijar_minutos_sesion(empresa_id, minutos):
    with conectar() as con:
        con.execute(
            "UPDATE empresas SET minutos_sesion=? WHERE id=?", (_acotar(minutos, 5, 90), empresa_id)
        )


def iniciar_sesion(codigo, minutos, tema=""):
    """Abre una conversación (cierra las abiertas anteriores). Devuelve los minutos reales."""
    minutos = _acotar(minutos, 5, 90) if minutos else minutos_programados(codigo)
    ahora = _ahora()
    with conectar() as con:
        con.execute("UPDATE sesiones SET fin_ts=? WHERE codigo=? AND fin_ts IS NULL", (ahora, codigo))
        con.execute(
            "INSERT INTO sesiones (codigo, inicio_ts, minutos, tema) VALUES (?,?,?,?)",
            (codigo, ahora, minutos, str(tema)[:80]),
        )
    registrar(codigo, "conversacion_inicio")
    return minutos


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
