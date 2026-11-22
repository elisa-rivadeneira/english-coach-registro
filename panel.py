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
:root{--navy:#101a2e;--navy2:#18253f;--gold:#c9a45c;--ink:#1c2230;--mut:#6c7488;--line:#e6e8ee;--bg:#f4f5f8;--ok:#1f7a4d}
*{box-sizing:border-box}
body{font:15px/1.5 -apple-system,"Segoe UI",Inter,system-ui,sans-serif;margin:0;color:var(--ink);background:var(--bg)}
a{color:#2a5bd7;text-decoration:none}
.shell{display:flex;min-height:100vh}
.side{width:264px;flex:none;background:linear-gradient(180deg,var(--navy),var(--navy2));color:#cfd6e6;display:flex;flex-direction:column;position:sticky;top:0;height:100vh;overflow:auto}
.brand{padding:26px 22px 18px;border-bottom:1px solid rgba(255,255,255,.07)}
.brand b{display:block;color:#fff;font-size:17px;letter-spacing:.3px}
.brand span{color:var(--gold);font-size:11px;letter-spacing:2.2px;text-transform:uppercase}
.nav{padding:14px 12px;flex:1}
.nav h6{margin:16px 10px 6px;font-size:11px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:#7f8aa6}
.nav a{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 12px;border-radius:10px;color:#cfd6e6;margin:2px 0;transition:.15s}
.nav a:hover{background:rgba(255,255,255,.06);color:#fff}
.nav a.on{background:rgba(201,164,92,.16);color:#fff;box-shadow:inset 3px 0 0 var(--gold)}
.nav a small{background:rgba(255,255,255,.09);border-radius:20px;padding:1px 8px;font-size:12px;color:#aab4cc}
.nav a.nuevo{color:var(--gold)}
.side .pie{padding:14px 22px;border-top:1px solid rgba(255,255,255,.07);font-size:13px}
.side .pie a{color:#9aa6c2}
.main{flex:1;min-width:0;padding:34px 40px 60px;max-width:1180px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.2px}
h2{font-size:17px;margin:30px 0 10px}
.sub{color:var(--mut);margin:0 0 22px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:18px 0 26px}
.stat{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px 18px;box-shadow:0 1px 2px rgba(16,26,46,.04)}
.stat b{display:block;font-size:28px;line-height:1.1}
.stat span{color:var(--mut);font-size:13px}
.panel{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:0 1px 2px rgba(16,26,46,.04);overflow:auto}
table{border-collapse:collapse;width:100%}
th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{font-size:11px;font-weight:600;letter-spacing:1.2px;text-transform:uppercase;color:var(--mut);background:#fafbfc}
tr:last-child td{border-bottom:0}
input{font:inherit;padding:10px 12px;border:1px solid #d5d9e3;border-radius:10px;background:#fff}
input:focus{outline:2px solid rgba(201,164,92,.5);border-color:var(--gold)}
button,.b{font:inherit;font-weight:600;padding:9px 16px;border:0;border-radius:10px;background:var(--navy);color:#fff;cursor:pointer;text-decoration:none;display:inline-block;transition:.15s}
button:hover,.b:hover{filter:brightness(1.2)}
button.g,.b.g{background:#eceef4;color:var(--ink)}
.b.gold{background:var(--gold);color:#1b1608}
.f{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;align-items:end}
.ok{background:#e6f4ec;color:#14563a;border:1px solid #c7e6d5;padding:11px 15px;border-radius:12px;margin:12px 0}
.aviso{background:#fff7e6;color:#7a5410;border:1px solid #f1deb0;padding:11px 15px;border-radius:12px;margin:12px 0}
.no{background:#fdecea;color:#8a2a21;border:1px solid #f5cbc6;padding:11px 15px;border-radius:12px;margin:12px 0}
.cod{font-family:ui-monospace,Menlo,monospace;font-weight:700;letter-spacing:.5px;background:#f1f2f7;padding:3px 8px;border-radius:7px;font-size:13px}
.dia{display:inline-block;width:12px;height:12px;border-radius:4px;background:#dfe2ea;margin-right:3px}
.dia.on{background:var(--ok)}
.fine{color:var(--mut);font-size:13px}
.acciones{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 20px}
details{margin:6px 0}summary{cursor:pointer}pre{white-space:pre-wrap;background:#f6f7fa;padding:12px;border-radius:10px;font-size:13px;max-width:520px}
.login{max-width:380px;margin:12vh auto;background:#fff;border:1px solid var(--line);border-radius:18px;padding:30px;box-shadow:0 10px 30px rgba(16,26,46,.08)}
@media(max-width:820px){
 .shell{flex-direction:column}
 .side{width:100%;height:auto;position:static}
 .nav{display:flex;gap:6px;overflow:auto;padding:10px}.nav h6{display:none}.nav a{white-space:nowrap}
 .brand{padding:16px 18px}.side .pie{display:none}
 .main{padding:22px 16px 50px}
}
"""


def pagina(titulo, cuerpo, nav=True, activo=None):
    if not nav:
        return HTMLResponse(
            "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(titulo)}</title><style>{ESTILO}</style></head>"
            f"<body><div class='login'>{cuerpo}</div></body></html>"
        )
    items = "".join(
        f"<a href='/admin/empresa/{e['id']}' class='{'on' if activo == e['id'] else ''}'>"
        f"<span>{html.escape(e['nombre'])}</span><small>{e['n']}</small></a>"
        for e in db.listar_empresas()
    )
    lateral = (
        "<aside class='side'><div class='brand'><b>English Master Coach</b><span>Administración</span></div>"
        "<nav class='nav'>"
        f"<a href='/admin' class='{'on' if activo == 0 else ''}'><span>Inicio</span></a>"
        f"<h6>Empresas</h6>{items}"
        "<a href='/admin#nueva' class='nuevo'><span>+ Nueva empresa</span></a></nav>"
        "<div class='pie'><a href='/admin/salir'>Cerrar sesión</a></div></aside>"
    )
    return HTMLResponse(
        "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(titulo)} · Administración</title><style>{ESTILO}</style></head>"
        f"<body><div class='shell'>{lateral}<main class='main'>{cuerpo}</main></div></body></html>"
    )


def _e(x):
    return html.escape(str(x))


def _login(error=""):
    if not _password():
        return pagina(
            "Admin", nav=False, cuerpo=
            "<h1>Panel no activado</h1><p>Define la variable de entorno <b>ADMIN_PASSWORD</b> "
            "en Easypanel y vuelve a implementar.</p>",
        )
    aviso = "<div class='no'>Clave incorrecta.</div>" if error else ""
    return pagina(
        "Entrar", nav=False, cuerpo=
        f"<h1>Panel de administración</h1><p class='sub'>English Master Coach</p>{aviso}<form method='post' action='/admin/login' class='f'>"
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
        emps = db.listar_empresas()
        total = sum(e["n"] for e in emps)
        activos = 0
        for e in emps:
            activos += sum(1 for a in db.alumnos_de_empresa(e["id"]) if any(a["dias7"]))
        filas = "".join(
            f"<tr><td><a href='/admin/empresa/{e['id']}'><b>{_e(e['nombre'])}</b></a></td>"
            f"<td><span class='cod'>{_e(e['prefijo'])}</span></td><td>{e['n']}</td>"
            f"<td><a href='/gerente?token={_e(e['token_gerente'])}'>Panel del gerente</a></td></tr>"
            for e in emps
        )
        tabla = (
            f"<div class='panel'><table><tr><th>Empresa</th><th>Prefijo</th><th>Integrantes</th><th></th></tr>{filas}</table></div>"
            if filas
            else "<p class='fine'>Todavía no hay empresas. Crea la primera abajo.</p>"
        )
        return pagina(
            "Inicio",
            "<h1>Inicio</h1><p class='sub'>Resumen de tus empresas y su práctica.</p>"
            f"<div class='cards'><div class='stat'><b>{len(emps)}</b><span>Empresas</span></div>"
            f"<div class='stat'><b>{total}</b><span>Integrantes</span></div>"
            f"<div class='stat'><b>{activos}</b><span>Practicaron esta semana</span></div></div>"
            + tabla +
            "<h2 id='nueva'>Nueva empresa</h2><form method='post' action='/admin/empresa' class='f'>"
            "<input name='nombre' placeholder='Nombre (Hotel Gloria)' required>"
            "<input name='prefijo' placeholder='Prefijo (GLO)' maxlength='4' size='6' required>"
            "<button>Crear empresa</button></form>",
            activo=0,
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
                f"<td><span class='cod'>{_e(a['codigo'])}</span></td><td>{_dias_html(a['dias7'])}</td>"
                f"<td>{a['racha']}</td><td>{ult}</td>"
                f"<td>{boton}<div class='fine'>{_enviado(a['enviado_ts'])}</div></td>"
                f"<td><form method='post' action='/admin/quitar/{_e(a['codigo'])}' onsubmit=\"return confirm('¿Quitar a este integrante y su historial?')\">"
                "<button class='g'>Quitar</button></form></td></tr>"
            )
        tabla = (
            "<div class='panel'><table><tr><th>Integrante</th><th>Código</th><th>Últimos 7 días</th><th>Racha</th><th>Última</th><th>Correo</th><th></th></tr>"
            + "".join(filas) + "</table></div>"
            if filas else "<p class='fine'>Aún no hay integrantes. Agrega el primero abajo.</p>"
        )
        nota_smtp = (
            "" if smtp else
            "<div class='aviso'>El envío automático no está configurado (faltan las variables SMTP en Easypanel). "
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
            f"<h1>{_e(emp['nombre'])}</h1><p class='sub'>{len(alumnos)} integrante{'' if len(alumnos)==1 else 's'} · prefijo {_e(emp['prefijo'])}</p>{msg}{nota_smtp}"
            f"<div class='acciones'><a class='b gold' href='/gerente?token={_e(emp['token_gerente'])}' target='_blank'>Abrir panel del gerente</a> {todos}</div>"
            + tabla +
            "<h2>Agregar integrante</h2><form method='post' action='/admin/alumno/" + str(eid) + "' class='f'>"
            "<input name='nombre' placeholder='Nombre completo' required size='28'>"
            "<input name='email' type='email' placeholder='Correo' required size='28'>"
            "<button>Agregar y generar código</button></form>"
            "<p class='fine'>El código aparece al instante. Luego envías el correo con el botón de su fila.</p>",
            activo=eid,
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
