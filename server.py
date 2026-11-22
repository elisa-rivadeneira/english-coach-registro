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
    return "Práctica registrada."


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
    filas = []
    for a in datos["alumnos"]:
        alerta = a["sin_practicar"] is None or a["sin_practicar"] >= 2
        filas.append(
            "<tr class='{}'><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td></tr>".format(
                "alerta" if alerta else "",
                html.escape(a["nombre"] or a["codigo"]) + "<br><small>" + html.escape(a["codigo"]) + "</small>",
                "".join("<span class='d{}'></span>".format(" on" if d else "") for d in a["dias7"]),
                _celda(a["dias_30"]),
                _celda(a["racha"]),
                _celda(a["min_7d"]),
                "sin práctica" if a["ultima"] is None else _celda(a["ultima"]),
                _celda(a["pron"]),
                _celda(a["comp"]),
            )
        )
    pagina = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Práctica de inglés · {html.escape(datos['empresa'])}</title>
<style>
body{{font:16px system-ui,sans-serif;margin:24px;color:#1f1d1a;background:#faf8f4}}
table{{border-collapse:collapse;width:100%;max-width:900px}}
th,td{{padding:8px 10px;border-bottom:1px solid #e3ded4;text-align:left}}
th{{font-size:13px;color:#6b665e}}
tr.alerta td{{background:#fbe9e5}}
.d{{display:inline-block;width:13px;height:13px;border-radius:3px;background:#ddd6c8;margin-right:2px}}.d.on{{background:#2f7d32}}
small{{color:#8a8479}}
.nota{{color:#6b665e;font-size:14px;max-width:700px}}
</style></head><body>
<h1>{html.escape(datos['empresa'])}: práctica de inglés</h1>
<p class="nota">La constancia (días y minutos) es lo principal. Las notas de pronunciación y
comprensión van de 1 a 5 y son orientativas: las estima la IA durante la práctica y no son una
calificación oficial de nivel. En rojo, quienes llevan 2 días o más sin practicar.</p>
<table><tr><th>Alumno</th><th>Últimos 7 días</th><th>Días (30 d)</th><th>Racha</th><th>Min. (7 d)</th>
<th>Última práctica</th><th>Pronunc.</th><th>Compr.</th></tr>
{''.join(filas)}</table></body></html>"""
    return HTMLResponse(pagina)


import panel  # noqa: E402

panel.registrar_rutas(mcp)

# Cargar el modelo de voz al arrancar, para que la primera frase no espere la carga
threading.Thread(target=lambda: voz._motor(), daemon=True).start()

app = mcp.streamable_http_app()
