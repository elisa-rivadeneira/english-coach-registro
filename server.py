"""Servidor de registro de práctica: conector MCP + panel del gerente.

Ejecutar:  uvicorn server:app --host 0.0.0.0 --port 8000
El conector queda en  /mcp  y el panel del gerente en  /gerente?token=...
"""
import html
import threading

import anyio

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

import db
import voz
from mcp.types import AudioContent

db.iniciar()

mcp = FastMCP(
    "Registro de práctica de inglés",
    host="0.0.0.0",
    stateless_http=True,
    json_response=True,
)


def _codigo(ctx: Context) -> str:
    """El código del alumno viaja en el encabezado X-Codigo (o Authorization: Bearer)."""
    req = ctx.request_context.request
    headers = req.headers if req is not None else {}
    codigo = (headers.get("x-codigo") or "").strip()
    if not codigo and req is not None:
        codigo = (req.query_params.get("codigo") or "").strip()
    if not codigo:
        auth = headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            codigo = auth[7:].strip()
    return codigo.upper()


MENSAJE_SIN_CODIGO = (
    "No se pudo registrar: el código del alumno no es válido. "
    "Dile al alumno que revise el código en la configuración del conector."
)


@mcp.tool()
def registrar_practica(
    ctx: Context,
    tipo: str,
    aciertos: int = 0,
    total: int = 0,
    pronunciacion: int = 0,
    comprension: int = 0,
    nota: str = "",
) -> str:
    """Registra un bloque de práctica terminado. Llámala al final de CADA ejercicio o
    actividad de la sesión, con valores reales y sin inventar nada.

    tipo: nombre corto del ejercicio (por ejemplo "listening", "roleplay", "vocabulario").
    aciertos y total: respuestas correctas y total de preguntas, si aplica (si no, 0).
    pronunciacion y comprension: nota de 1 a 5 de lo observado en este bloque (0 si no se evaluó).
    nota: una frase corta sobre lo que costó, sin datos personales ni de la empresa.
    """
    codigo = _codigo(ctx)
    if not codigo or not db.alumno_existe(codigo):
        return MENSAJE_SIN_CODIGO
    db.registrar(codigo, tipo, aciertos, total, pronunciacion, comprension, nota)
    if str(tipo).lower().startswith("conv"):
        db.cerrar_sesion(codigo)
    return "Práctica registrada."


def _fases(minutos):
    """Agenda orientativa de la conversación, repartida según la duración."""
    p = lambda f: max(1, round(minutos * f))
    return [
        (p(0.10), "calentamiento: preguntas sencillas sobre su día o su trabajo, en inglés"),
        (p(0.35), "conversación sobre el tema del día; corrige con amabilidad los errores de gramática que se repiten"),
        (p(0.30), "role-play de una situación real de su trabajo (cliente, llamada, reunión)"),
        (p(0.15), "repaso: repetir bien las frases en las que falló y nuevas variantes"),
        (p(0.10), "cierre: resumen de lo aprendido, 2 o 3 puntos a reforzar y registrar la práctica"),
    ]


REGLAS_SESION = (
    "Reglas: NO preguntes «¿quieres algo más?» ni te despidas antes de que se cumpla el tiempo. "
    "Si la persona dice que ya terminó, o que no se le ocurre más, cambia de actividad en vez de cerrar: "
    "propón un role-play, un juego de preguntas rápidas o repasar sus errores. Solo cierra antes si lo pide "
    "dos veces o dice que tiene que irse. Llama a tiempo_restante cada 4 o 5 minutos."
)


@mcp.tool()
def fijar_compromiso(ctx: Context, minutos: int) -> str:
    """Guarda cuántos minutos al día se comprometió a practicar la persona (lo dice en la entrevista
    inicial). El máximo es 60. Después, iniciar_sesion usa este tiempo por defecto."""
    codigo = _codigo(ctx)
    if not codigo or not db.alumno_existe(codigo):
        return MENSAJE_SIN_CODIGO
    m = db.fijar_compromiso(codigo, minutos)
    aviso = f" (pidió {minutos}; el máximo es {db.max_min_dia()})" if minutos > db.max_min_dia() else ""
    return f"Compromiso guardado: {m} minutos al día{aviso}."


@mcp.tool()
def iniciar_sesion(ctx: Context, minutos: int = 0, tema: str = "") -> str:
    """Llámala AL EMPEZAR una conversación por voz. Abre un reloj en el servidor y devuelve la
    duración programada y la agenda. minutos: duración pedida por la persona (por ejemplo 15, 30
    o 60); si es 0 se usa el tiempo que ella se comprometió a practicar cada día (dicho en su
    entrevista). El servidor aplica un tope diario de 60 minutos que no se puede superar. tema: tema de la conversación."""
    codigo = _codigo(ctx)
    if not codigo or not db.alumno_existe(codigo):
        return MENSAJE_SIN_CODIGO
    m, topado = db.iniciar_sesion(codigo, minutos, tema)
    if m == 0:
        return (
            f"La persona ya cumplió su tope de {db.max_min_dia()} minutos de conversación por hoy. "
            "No inicies otra conversación: felicítala por su constancia, dile que mañana sigue y, si quiere, "
            "ofrécele repasar con los ejercicios de su entrenador."
        )
    nota = (
        f" (el máximo diario es {db.max_min_dia()} min en total; ya usó {db.minutos_usados_hoy(codigo)} hoy)"
        if topado else ""
    )
    agenda = "\n".join(f"- {d} min: {t}" for d, t in _fases(m))
    return (
        f"Conversación programada de {m} minutos{nota}. El reloj ya corre en el servidor.\n"
        f"Agenda sugerida:\n{agenda}\n{REGLAS_SESION}"
    )


@mcp.tool()
def tiempo_restante(ctx: Context) -> str:
    """Dice cuántos minutos faltan de la conversación y qué hacer ahora. Llámala cada 4 o 5
    minutos y SIEMPRE antes de despedirte: si faltan minutos, no cierres la conversación."""
    codigo = _codigo(ctx)
    if not codigo or not db.alumno_existe(codigo):
        return MENSAJE_SIN_CODIGO
    s = db.sesion_abierta(codigo)
    if not s:
        return "No hay una conversación abierta. Llama primero a iniciar_sesion."
    db.registrar(codigo, "pulso")  # sirve para medir los minutos reales de práctica
    transcurrido = (db._ahora() - s["inicio_ts"]) / 60
    resto = s["minutos"] - transcurrido
    if resto <= 0.5:
        return (
            f"Se cumplieron los {s['minutos']} minutos. Haz el cierre: resume lo aprendido, nombra 2 o 3 "
            "puntos de gramática a reforzar, llama a registrar_practica (tipo «conversacion») con valores "
            "reales y despídete con ánimo."
        )
    fase, acum = "", 0
    for d, t in _fases(s["minutos"]):
        acum += d
        if transcurrido < acum:
            fase = t
            break
    fase = fase or "cierre: resumen y registro"
    if resto <= 3:
        return f"Quedan unos {round(resto)} min. Empieza el cierre: {fase}. Después llama a registrar_practica."
    return (
        f"Han pasado {round(transcurrido)} min; quedan {round(resto)} de {s['minutos']}. "
        f"Ahora toca: {fase}. {REGLAS_SESION}"
    )


@mcp.tool()
def registrar_evaluacion(ctx: Context, nivel: str, fortalezas: str = "", reforzar: str = "") -> str:
    """Registra tu estimación del nivel de inglés de la persona (A1, A2, B1 o B2). Llámala al terminar
    la entrevista inicial y luego una vez por semana, con honestidad: es una estimación orientativa,
    no una certificación. fortalezas y reforzar: frases cortas (por ejemplo «pasado simple»)."""
    codigo = _codigo(ctx)
    if not codigo or not db.alumno_existe(codigo):
        return MENSAJE_SIN_CODIGO
    db.registrar_evaluacion(codigo, nivel, fortalezas, reforzar)
    return "Evaluación registrada."


@mcp.tool()
def mi_progreso(ctx: Context) -> str:
    """Devuelve el progreso del alumno en los últimos 30 días: días practicados, racha,
    minutos de la última semana y días sin practicar. Úsala si el alumno pregunta cómo va."""
    codigo = _codigo(ctx)
    if not codigo or not db.alumno_existe(codigo):
        return MENSAJE_SIN_CODIGO
    with db.conectar() as con:
        r = db.resumen_alumno(con, codigo)
    return (
        f"Días practicados en 30 días: {r['dias_30']}. Racha actual: {r['racha']} días. "
        f"Minutos esta semana: {r['min_7d']}. "
        + (
            f"Última práctica hace {r['sin_practicar']} días."
            if r["sin_practicar"] is not None
            else "Todavía no hay prácticas registradas."
        )
    )


@mcp.tool()
async def generar_audio(ctx: Context, texto: str, voz_id: str = "af_heart", velocidad: float = 0.95):
    """Convierte una frase corta en inglés (máximo 300 caracteres) en audio MP3 para que
    el alumno la escuche. Úsala para los ejercicios de escucha. Voces: af_heart, af_bella,
    af_nicole (mujer, EE. UU.), am_adam, am_michael (hombre, EE. UU.), bf_emma (mujer,
    británica), bm_george (hombre, británico). velocidad entre 0.7 y 1.3."""
    codigo = _codigo(ctx)
    if not codigo or not db.alumno_existe(codigo):
        return MENSAJE_SIN_CODIGO
    try:
        # En un hilo aparte: generar la voz tarda segundos y no debe bloquear el servidor
        datos = await anyio.to_thread.run_sync(voz.audio_base64, texto, voz_id, velocidad)
    except ValueError as e:
        return f"No se pudo generar el audio: {e}"
    return [AudioContent(type="audio", data=datos, mimeType="audio/mpeg")]


@mcp.custom_route("/salud", methods=["GET"])
async def salud(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _celda(valor):
    return "–" if valor is None else html.escape(str(valor))


@mcp.custom_route("/gerente", methods=["GET"])
async def gerente(request: Request) -> HTMLResponse:
    token = request.query_params.get("token", "")
    datos = db.resumen_empresa(token) if token else None
    if not datos:
        return HTMLResponse("<p>Enlace no válido.</p>", status_code=404)
    import panel as _panel

    return _panel.pagina_gerente(datos)


import panel  # noqa: E402

panel.registrar_rutas(mcp)
import cuenta  # noqa: E402

cuenta.registrar_rutas(mcp)

# Cargar el modelo de voz al arrancar, para que la primera frase no espere la carga
threading.Thread(target=lambda: voz._motor(), daemon=True).start()

app = mcp.streamable_http_app()
