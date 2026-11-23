"""Cuentas de los dueños de empresa: cada dueño entra con su correo y su clave y ve SOLO el panel
de los alumnos de su empresa. La empresa sale siempre de la sesión firmada, nunca de la URL."""
import hashlib
import hmac
import time

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

import db
import panel

DURACION = 60 * 60 * 24 * 14  # 14 días
_intentos = {}  # (ip, correo) -> [marcas de tiempo de intentos fallidos]


def _firma(dueno_id, exp, ver):
    return hmac.new(db.secreto_sesion().encode(), f"{dueno_id}.{exp}.{ver}".encode(), hashlib.sha256).hexdigest()


def _crear_cookie(d):
    exp = int(time.time()) + DURACION
    ver = d["clave_hash"][-8:]  # al cambiar la clave, las sesiones anteriores dejan de valer
    return f"{d['id']}.{exp}.{_firma(d['id'], exp, ver)}"


def dueno_actual(request: Request):
    """Devuelve el dueño con sesión válida o None."""
    valor = request.cookies.get("dueno", "")
    try:
        did, exp, firma = valor.split(".")
        did, exp = int(did), int(exp)
    except ValueError:
        return None
    if exp < time.time():
        return None
    d = db.dueno_por_id(did)
    if not d:
        return None
    if not hmac.compare_digest(firma, _firma(did, exp, d["clave_hash"][-8:])):
        return None
    return d


def _bloqueado(clave_ip):
    ahora = time.time()
    lista = [t for t in _intentos.get(clave_ip, []) if ahora - t < 600]
    _intentos[clave_ip] = lista
    return len(lista) >= 8


def _login_html(error="", correo=""):
    aviso = f"<div class='no'>{panel._e(error)}</div>" if error else ""
    return panel.pagina(
        "Entrar", nav=False, cuerpo=(
            "<h1>Panel de tu empresa</h1><p class='sub'>English Master Coach</p>" + aviso +
            "<form method='post' action='/cuenta/login'>"
            f"<div class='f'><input name='email' type='email' placeholder='Correo' value='{panel._e(correo)}' required style='flex:1 1 100%'></div>"
            "<div class='f'><input name='clave' type='password' placeholder='Clave' required style='flex:1 1 100%'></div>"
            "<button style='width:100%'>Entrar</button></form>"
        ),
    )


def registrar_rutas(mcp):
    @mcp.custom_route("/cuenta", methods=["GET"])
    async def cuenta_inicio(request: Request):
        if dueno_actual(request):
            return RedirectResponse("/cuenta/panel", status_code=303)
        return _login_html()

    @mcp.custom_route("/cuenta/login", methods=["POST"])
    async def cuenta_login(request: Request):
        datos = await request.form()
        correo, clave = str(datos.get("email", "")).strip(), str(datos.get("clave", ""))
        ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
        llave = (ip, correo.lower())
        if _bloqueado(llave):
            return _login_html("Demasiados intentos. Espera 10 minutos e inténtalo de nuevo.", correo)
        d = db.autenticar_dueno(correo, clave)
        if not d:
            _intentos.setdefault(llave, []).append(time.time())
            return _login_html("Correo o clave incorrectos.", correo)
        r = RedirectResponse("/cuenta/panel", status_code=303)
        r.set_cookie("dueno", _crear_cookie(d), httponly=True, secure=True, samesite="strict", max_age=DURACION)
        return r

    @mcp.custom_route("/cuenta/salir", methods=["GET"])
    async def cuenta_salir(request: Request):
        r = RedirectResponse("/cuenta", status_code=303)
        r.delete_cookie("dueno")
        return r

    @mcp.custom_route("/cuenta/panel", methods=["GET"])
    async def cuenta_panel(request: Request):
        d = dueno_actual(request)
        if not d:
            return RedirectResponse("/cuenta", status_code=303)
        datos = db.resumen_empresa_id(d["empresa_id"])  # solo SU empresa
        if not datos:
            return HTMLResponse("<p>Empresa no encontrada.</p>", status_code=404)
        return panel.pagina_gerente(datos, cuenta=d)

    def _form_clave(aviso="", ok=False):
        clase = "ok" if ok else "no"
        msg = f"<div class='{clase}'>{panel._e(aviso)}</div>" if aviso else ""
        return panel.pagina(
            "Cambiar clave", nav=False, cuerpo=(
                "<h1>Cambiar clave</h1>" + msg +
                "<form method='post' action='/cuenta/clave'>"
                "<div class='f'><input name='actual' type='password' placeholder='Clave actual' required style='flex:1 1 100%'></div>"
                "<div class='f'><input name='nueva' type='password' placeholder='Clave nueva (mínimo 8 caracteres)' minlength='8' required style='flex:1 1 100%'></div>"
                "<button style='width:100%'>Guardar</button></form>"
                "<p class='fine'><a href='/cuenta/panel'>← Volver al panel</a></p>"
            ),
        )

    @mcp.custom_route("/cuenta/clave", methods=["GET", "POST"])
    async def cuenta_clave(request: Request):
        d = dueno_actual(request)
        if not d:
            return RedirectResponse("/cuenta", status_code=303)
        if request.method == "GET":
            return _form_clave()
        datos = await request.form()
        actual, nueva = str(datos.get("actual", "")), str(datos.get("nueva", ""))
        if not db.autenticar_dueno(d["email"], actual):
            return _form_clave("La clave actual no es correcta.")
        if len(nueva) < 8:
            return _form_clave("La clave nueva debe tener al menos 8 caracteres.")
        db.cambiar_clave_dueno(d["id"], nueva)
        nuevo = db.dueno_por_id(d["id"])
        r = _form_clave("Listo, tu clave se cambió.", ok=True)
        r.set_cookie("dueno", _crear_cookie(nuevo), httponly=True, secure=True, samesite="strict", max_age=DURACION)
        return r
