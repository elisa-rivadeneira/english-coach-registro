"""Panel de administración web: empresas, integrantes (nombre + correo), códigos y envío de correos.

Se protege con la variable ADMIN_PASSWORD. El correo sale por SMTP (SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASS, SMTP_FROM). Si SMTP no está configurado, el panel muestra el mensaje
listo para copiar y un enlace «Abrir en mi correo».
"""
import datetime as dt
import hashlib
import hmac
import html
import os
import smtplib
import urllib.parse
from email.message import EmailMessage

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

import db

REPO_URL = os.environ.get("REPO_URL", "https://github.com/elisa-rivadeneira/english-master-couch")
PLANTILLA_URL = os.environ.get(
    "PLANTILLA_URL",
    "https://github.com/elisa-rivadeneira/english-master-couch/blob/main/language-trainer-plantilla.html",
)
GUIA_URL = os.environ.get("GUIA_URL", REPO_URL + "/blob/main/GUIA-ALUMNO.md")
COACH = os.environ.get("COACH_NOMBRE", "English Master Coach")


def _password():
    return os.environ.get("ADMIN_PASSWORD", "")


def _firma():
    return hmac.new(_password().encode(), b"admin-sesion", hashlib.sha256).hexdigest()


def autenticado(request: Request) -> bool:
    if not _password():
        return False
    return hmac.compare_digest(request.cookies.get("admin", ""), _firma())


def base_url(request: Request) -> str:
    env = os.environ.get("URL_BASE", "").rstrip("/")
    if env:
        return env
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return "https://" + host


def url_conector(request: Request, codigo: str) -> str:
    return f"{base_url(request)}/mcp?codigo={codigo}"


def mensaje(request: Request, a: dict):
    """(asunto, texto) del correo de bienvenida."""
    nombre = (a["nombre"] or "").split(" ")[0] or "hola"
    asunto = f"Tu práctica de inglés con {COACH}: activa tu código"
    texto = f"""Hola {nombre},

{a['empresa']} te invitó a practicar inglés con {COACH}. Activarlo toma unos 5 minutos, una sola vez.

TU CÓDIGO PERSONAL: {a['codigo']}
(es solo tuyo, no lo compartas)

PASO 1 · Agrega tu conector en Claude
En la app de Claude entra a Plugins → Conectores → Agregar conector personalizado y llena:
  Nombre: Registro Ingles   (exactamente así, sin tilde y con la R mayúscula)
  URL: {url_conector(request, a['codigo'])}
Deja marcada la opción «Sin inicio de sesión» y toca Agregar.

PASO 2 · Crea tu entrenador
Abre un chat nuevo en Claude, pega este enlace y escribe «Arma mi entrenador de inglés con esta plantilla»:
  {PLANTILLA_URL}
Claude te hará unas preguntas sobre tu trabajo y creará tu entrenador personal.

PASO 3 · Practica cada día
Habla con Claude por voz y haz la lección de tu entrenador. Arriba debe aparecer «● registro» en verde: eso significa que tu práctica se está contando.

Guía con capturas y solución de problemas: {GUIA_URL}

¡Éxitos!
{COACH}
"""
    return asunto, texto


def _smtp_listo() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def _enviar_smtp(para: str, asunto: str, texto: str):
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = para
    msg.set_content(texto)
    host = os.environ["SMTP_HOST"]
    puerto = int(os.environ.get("SMTP_PORT", "587"))
    usuario = os.environ.get("SMTP_USER", "")
    clave = os.environ.get("SMTP_PASS", "")
    if puerto == 465:
        s = smtplib.SMTP_SSL(host, puerto, timeout=30)
    else:
        s = smtplib.SMTP(host, puerto, timeout=30)
        s.starttls()
    with s:
        if usuario:
            s.login(usuario, clave)
        s.send_message(msg)


ESTILO = """
body{font:16px system-ui,sans-serif;margin:0;color:#1f1d1a;background:#faf8f4}
.w{max-width:980px;margin:0 auto;padding:20px}
h1{font-size:24px}a{color:#0b6bcb}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{padding:8px 10px;border-bottom:1px solid #e3ded4;text-align:left;vertical-align:middle}
th{font-size:13px;color:#6b665e}
input{font:inherit;padding:8px;border:1px solid #cfc9bd;border-radius:8px}
button,.b{font:inherit;font-weight:700;padding:8px 14px;border:0;border-radius:8px;background:#2f7d32;color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
button.g,.b.g{background:#e9e4d9;color:#1f1d1a}
button.r{background:#b3261e}
.f{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;align-items:end}
.ok{background:#e4f4e4;padding:10px 14px;border-radius:8px;margin:10px 0}
.no{background:#fbe9e5;padding:10px 14px;border-radius:8px;margin:10px 0}
.cod{font-family:ui-monospace,monospace;font-weight:700}
.dia{display:inline-block;width:12px;height:12px;border-radius:3px;background:#ddd6c8;margin-right:2px}
.dia.on{background:#2f7d32}
.fine{color:#6b665e;font-size:13px}
details{margin:6px 0}pre{white-space:pre-wrap;background:#f1ede4;padding:10px;border-radius:8px;font-size:13px}
"""


def pagina(titulo, cuerpo):
    return HTMLResponse(
        f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(titulo)}</title><style>{ESTILO}</style></head>"
        f"<body><div class='w'>{cuerpo}</div></body></html>"
    )


def _e(x):
    return html.escape(str(x))


def _login(error=""):
    if not _password():
        return pagina(
            "Admin",
            "<h1>Panel no activado</h1><p>Define la variable de entorno <b>ADMIN_PASSWORD</b> "
            "en Easypanel y vuelve a implementar.</p>",
        )
    aviso = "<div class='no'>Clave incorrecta.</div>" if error else ""
    return pagina(
        "Entrar",
        f"<h1>Panel de administración</h1>{aviso}<form method='post' action='/admin/login' class='f'>"
        "<input type='password' name='clave' placeholder='Clave' autofocus>"
        "<button>Entrar</button></form>",
    )


def _hoy():
    return dt.datetime.now(db.LIMA).date()


def _dias_html(dias7):
    return "".join(f"<span class='dia{' on' if d else ''}'></span>" for d in dias7)


def _enviado(ts):
    if not ts:
        return "<span class='fine'>sin enviar</span>"
    f = dt.datetime.fromtimestamp(ts, db.LIMA).strftime("%d/%m %H:%M")
    return f"enviado {f}"


def registrar_rutas(mcp):
    @mcp.custom_route("/admin/login", methods=["POST"])
    async def login(request: Request):
        datos = await request.form()
        if _password() and hmac.compare_digest(str(datos.get("clave", "")), _password()):
            r = RedirectResponse("/admin", status_code=303)
            r.set_cookie("admin", _firma(), httponly=True, secure=True, samesite="strict", max_age=60 * 60 * 24 * 14)
            return r
        return _login(error=True)

    @mcp.custom_route("/admin/salir", methods=["GET"])
    async def salir(request: Request):
        r = RedirectResponse("/admin", status_code=303)
        r.delete_cookie("admin")
        return r

    @mcp.custom_route("/admin", methods=["GET"])
    async def inicio(request: Request):
        if not autenticado(request):
            return _login()
        filas = "".join(
            f"<tr><td><a href='/admin/empresa/{e['id']}'><b>{_e(e['nombre'])}</b></a></td>"
            f"<td class='cod'>{_e(e['prefijo'])}</td><td>{e['n']}</td>"
            f"<td><a href='/gerente?token={_e(e['token_gerente'])}'>Panel del gerente</a></td></tr>"
            for e in db.listar_empresas()
        )
        tabla = (
            f"<table><tr><th>Empresa</th><th>Prefijo</th><th>Integrantes</th><th></th></tr>{filas}</table>"
            if filas
            else "<p class='fine'>Todavía no hay empresas.</p>"
        )
        return pagina(
            "Empresas",
            "<h1>Empresas</h1>" + tabla +
            "<h2>Nueva empresa</h2><form method='post' action='/admin/empresa' class='f'>"
            "<input name='nombre' placeholder='Nombre (Hotel Gloria)' required>"
            "<input name='prefijo' placeholder='Prefijo (GLO)' maxlength='4' size='6' required>"
            "<button>Crear empresa</button></form>"
            "<p><a href='/admin/salir'>Salir</a></p>",
        )

    @mcp.custom_route("/admin/empresa", methods=["POST"])
    async def nueva_empresa(request: Request):
        if not autenticado(request):
            return _login()
        d = await request.form()
        nombre, prefijo = str(d.get("nombre", "")).strip(), str(d.get("prefijo", "")).strip()
        if not nombre or not (2 <= len(prefijo) <= 4) or not prefijo.isalpha():
            return pagina("Error", "<div class='no'>Pon un nombre y un prefijo de 2 a 4 letras.</div><a href='/admin'>Volver</a>")
        try:
            eid = db.crear_empresa_vacia(nombre, prefijo)
        except Exception:
            return pagina("Error", "<div class='no'>Ese prefijo ya existe. Usa otro.</div><a href='/admin'>Volver</a>")
        return RedirectResponse(f"/admin/empresa/{eid}", status_code=303)

    async def _vista_empresa(request, eid, aviso="", ok=True):
        emp = db.empresa_por_id(eid)
        if not emp:
            return pagina("No existe", "<p>Empresa no encontrada.</p><a href='/admin'>Volver</a>")
        alumnos = db.alumnos_de_empresa(eid)
        smtp = _smtp_listo()
        filas = []
        for a in alumnos:
            ficha = {"codigo": a["codigo"], "nombre": a["nombre"], "email": a["email"], "empresa": emp["nombre"]}
            asunto, texto = mensaje(request, ficha)
            mailto = (
                "mailto:" + urllib.parse.quote(a["email"]) + "?subject=" + urllib.parse.quote(asunto)
                + "&body=" + urllib.parse.quote(texto)
            )
            boton = (
                f"<form method='post' action='/admin/enviar/{_e(a['codigo'])}' style='display:inline'>"
                "<button>Enviar correo</button></form>"
                if smtp and a["email"]
                else (f"<a class='b' href='{_e(mailto)}'>Abrir en mi correo</a>" if a["email"] else "<span class='fine'>sin correo</span>")
            )
            ult = "–" if a["ultima"] is None else a["ultima"].strftime("%d/%m")
            filas.append(
                f"<tr><td><b>{_e(a['nombre'] or '(sin nombre)')}</b><div class='fine'>{_e(a['email'])}</div>"
                f"<details><summary class='fine'>ver mensaje</summary><pre>{_e(texto)}</pre></details></td>"
                f"<td class='cod'>{_e(a['codigo'])}</td><td>{_dias_html(a['dias7'])}</td>"
                f"<td>{a['racha']}</td><td>{ult}</td>"
                f"<td>{boton}<div class='fine'>{_enviado(a['enviado_ts'])}</div></td>"
                f"<td><form method='post' action='/admin/quitar/{_e(a['codigo'])}' onsubmit=\"return confirm('¿Quitar a este integrante y su historial?')\">"
                "<button class='g'>Quitar</button></form></td></tr>"
            )
        tabla = (
            "<table><tr><th>Integrante</th><th>Código</th><th>Últimos 7 días</th><th>Racha</th><th>Última</th><th>Correo</th><th></th></tr>"
            + "".join(filas) + "</table>"
            if filas else "<p class='fine'>Aún no hay integrantes. Agrega el primero abajo.</p>"
        )
        nota_smtp = (
            "" if smtp else
            "<div class='no'>El envío automático no está configurado (faltan las variables SMTP en Easypanel). "
            "Mientras tanto, usa «Abrir en mi correo»: se abre tu correo con el mensaje ya escrito.</div>"
        )
        pendientes = [a for a in alumnos if a["email"] and not a["enviado_ts"]]
        todos = (
            f"<form method='post' action='/admin/enviar-todos/{eid}' style='display:inline'>"
            f"<button>Enviar a los {len(pendientes)} pendientes</button></form>"
            if smtp and pendientes else ""
        )
        msg = f"<div class='{'ok' if ok else 'no'}'>{_e(aviso)}</div>" if aviso else ""
        return pagina(
            emp["nombre"],
            f"<p><a href='/admin'>← Empresas</a></p><h1>{_e(emp['nombre'])}</h1>{msg}{nota_smtp}"
            f"<p><a class='b g' href='/gerente?token={_e(emp['token_gerente'])}'>Abrir panel del gerente</a> {todos}</p>"
            + tabla +
            "<h2>Agregar integrante</h2><form method='post' action='/admin/alumno/" + str(eid) + "' class='f'>"
            "<input name='nombre' placeholder='Nombre completo' required size='28'>"
            "<input name='email' type='email' placeholder='Correo' required size='28'>"
            "<button>Agregar y generar código</button></form>"
            "<p class='fine'>El código aparece al instante. Luego envías el correo con el botón de su fila.</p>",
        )

    @mcp.custom_route("/admin/empresa/{eid:int}", methods=["GET"])
    async def ver_empresa(request: Request):
        if not autenticado(request):
            return _login()
        return await _vista_empresa(request, request.path_params["eid"])

    @mcp.custom_route("/admin/alumno/{eid:int}", methods=["POST"])
    async def nuevo_alumno(request: Request):
        if not autenticado(request):
            return _login()
        eid = request.path_params["eid"]
        d = await request.form()
        nombre, email = str(d.get("nombre", "")).strip(), str(d.get("email", "")).strip()
        if not nombre or "@" not in email:
            return await _vista_empresa(request, eid, "Escribe el nombre y un correo válido.", ok=False)
        c = db.agregar_alumno(eid, nombre, email)
        return await _vista_empresa(request, eid, f"Listo: {nombre} ya tiene su código {c}.")

    async def _enviar_a(request, codigo):
        a = db.alumno(codigo)
        if not a or not a["email"]:
            return False, "sin correo"
        asunto, texto = mensaje(request, a)
        try:
            await anyio.to_thread.run_sync(_enviar_smtp, a["email"], asunto, texto)
        except Exception as ex:
            return False, str(ex)[:150]
        db.marcar_enviado(codigo)
        return True, ""

    @mcp.custom_route("/admin/enviar/{codigo}", methods=["POST"])
    async def enviar(request: Request):
        if not autenticado(request):
            return _login()
        codigo = request.path_params["codigo"].upper()
        a = db.alumno(codigo)
        if not a:
            return pagina("No existe", "<p>Integrante no encontrado.</p><a href='/admin'>Volver</a>")
        if not _smtp_listo():
            return await _vista_empresa(request, _eid(codigo), "El envío automático no está configurado.", ok=False)
        ok, err = await _enviar_a(request, codigo)
        aviso = f"Correo enviado a {a['email']}." if ok else f"No se pudo enviar: {err}"
        return await _vista_empresa(request, _eid(codigo), aviso, ok=ok)

    @mcp.custom_route("/admin/enviar-todos/{eid:int}", methods=["POST"])
    async def enviar_todos(request: Request):
        if not autenticado(request):
            return _login()
        eid = request.path_params["eid"]
        if not _smtp_listo():
            return await _vista_empresa(request, eid, "El envío automático no está configurado.", ok=False)
        enviados, fallos = 0, []
        for a in db.alumnos_de_empresa(eid):
            if a["email"] and not a["enviado_ts"]:
                ok, err = await _enviar_a(request, a["codigo"])
                if ok:
                    enviados += 1
                else:
                    fallos.append(f"{a['nombre'] or a['codigo']}: {err}")
        aviso = f"Enviados: {enviados}." + (" Fallaron: " + "; ".join(fallos) if fallos else "")
        return await _vista_empresa(request, eid, aviso, ok=not fallos)

    @mcp.custom_route("/admin/quitar/{codigo}", methods=["POST"])
    async def quitar(request: Request):
        if not autenticado(request):
            return _login()
        codigo = request.path_params["codigo"].upper()
        eid = _eid(codigo)
        db.quitar_alumno(codigo)
        return await _vista_empresa(request, eid, "Integrante quitado.") if eid else RedirectResponse("/admin", status_code=303)


def _eid(codigo):
    with db.conectar() as con:
        r = con.execute("SELECT empresa_id FROM alumnos WHERE codigo=?", (codigo,)).fetchone()
        return r["empresa_id"] if r else 0
