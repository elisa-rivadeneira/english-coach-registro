# Registro de práctica de inglés (conector MCP + panel del gerente)

Servidor mínimo para que el coach de Claude registre la práctica de cada alumno.

## Instalar y arrancar
    pip install -r requirements.txt
    python admin.py empresa "Hotel Gloria" GLO 8     # crea la empresa, 8 códigos y el enlace del gerente
    uvicorn server:app --host 127.0.0.1 --port 8000

Ponlo detrás de HTTPS en el puerto 443 (nginx o Caddy). El conector queda en
`https://TU-DOMINIO/mcp` y el panel en `https://TU-DOMINIO/gerente?token=...`.
Claude solo se conecta a direcciones públicas, en el puerto estándar.

## Cómo lo agrega cada alumno (una sola vez)
En Claude: Conectores > Agregar conector personalizado > URL `https://TU-DOMINIO/mcp`.
En los encabezados de la petición agrega `X-Codigo` con su código (por ejemplo GLO-K7Q2).
La cuenta gratuita permite un solo conector personalizado.

## Qué guarda
- `registrar_practica`: tipo de ejercicio, aciertos, notas de pronunciación y comprensión (1 a 5).
  La hora la pone el servidor; los minutos los calcula el servidor a partir de las horas de los avisos.
- `mi_progreso`: días practicados, racha y minutos de la semana.

## Instrucción para el coach (agregar a sus instrucciones)
"Al terminar cada ejercicio, llama a la herramienta registrar_practica con valores reales.
Nunca inventes resultados. Si la herramienta responde que el código no es válido, díselo al alumno."

## Pendiente
- Probar con Claude real (cuenta gratuita, app móvil y modo de voz): lo probado hasta ahora fue con un cliente de prueba.
- Correo mensual al gerente, usando `db.resumen_empresa`.
- Login del gerente: hoy es un enlace con token secreto.

## Voz gratuita (generar_audio)
Herramienta `generar_audio(texto, voz_id, velocidad)`: voz Kokoro (código abierto), frases de hasta 300
caracteres, caché por frase en `cache_audio/`. Requiere `ffmpeg` y los archivos de `modelos/`
(kokoro-v1.0.int8.onnx renombrado a kokoro-int8.onnx, y voices-v1.0.bin; ~120 MB, de
github.com/thewh1teagle/kokoro-onnx/releases, etiqueta model-files-v1.0). Devuelve audio MP3 en base64.
Probado en local; sin probar desde un artefacto ni desde la app móvil.

## Despliegue en Easypanel (desde GitHub)
1. Sube esta carpeta a un repositorio privado de GitHub.
2. En Easypanel: crear servicio "App", fuente GitHub (repositorio y rama), construcción "Dockerfile".
3. Puerto del servicio: 8000. Memoria sugerida: 1 GB o más.
4. Montajes: volumen persistente en `/data` (guarda registro.db y cache_audio).
5. Dominio: agrega tu dominio al servicio con HTTPS activado (Easypanel emite el certificado).
6. Crear empresa: consola del servicio, `URL_BASE=https://TU-DOMINIO python admin.py empresa "Hotel Gloria" GLO 8`.
7. Probar: https://TU-DOMINIO/salud debe decir ok. El conector queda en https://TU-DOMINIO/mcp

## Panel de administración (`/admin`)

Entras con la clave `ADMIN_PASSWORD`. Flujo en una reunión:
1. **Nueva empresa** (nombre y prefijo de 3 letras, por ejemplo GLO).
2. Dentro de la empresa, **Agregar integrante** (nombre completo + correo): el código aparece al instante.
3. **Enviar correo** (o «Enviar a los N pendientes»): sale el mensaje con el código, la URL del conector y el enlace a la plantilla en GitHub. «ver mensaje» muestra el texto antes de enviar.
4. En la misma tabla ves los últimos 7 días, la racha y la última práctica de cada integrante, y el botón «Abrir panel del gerente».

Variables de entorno en Easypanel:
- `ADMIN_PASSWORD` (obligatoria para activar el panel)
- `URL_BASE` (opcional, por ejemplo `https://automation-english-coach-registro.gnrjtm.easypanel.host`)
- Envío de correo (SMTP): `SMTP_HOST`, `SMTP_PORT` (587 o 465), `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` (por ejemplo `English Master Coach <hola@tudominio.com>`). Sin SMTP el panel ofrece «Abrir en mi correo» con el mensaje ya escrito.
- `PLANTILLA_URL`, `GUIA_URL`, `COACH_NOMBRE` (opcionales, para cambiar los enlaces y el nombre en el correo).

## Conversaciones por voz con duración programada

El coach no percibe el tiempo, así que el servidor lleva el reloj. Herramientas del conector:
- `iniciar_sesion(minutos, tema)`: abre la conversación y devuelve la agenda (calentamiento, tema, role-play, repaso, cierre). Con `minutos=0` usa el compromiso diario que el alumno dijo en su entrevista (`fijar_compromiso`); si no lo dijo, el valor por defecto de la empresa (panel). No hay tope diario: la persona practica lo que quiera. Si la llamada se corta, puede volver a llamar y el coach retoma según el reloj del servidor.
- `tiempo_restante()`: dice cuánto falta y qué actividad toca; el coach la llama cada 4–5 min y antes de despedirse. Mientras haya tiempo le indica no cerrar y cambiar de actividad.
- `registrar_practica(tipo="conversacion", ...)` cierra la conversación. Cada consulta de tiempo cuenta como un pulso, así que los minutos del reporte son reales.

## Métricas del gerente (`/gerente?token=...`)

- **Asistencia** = días en que el alumno habló con el coach (conversación por voz). Los **ejercicios** son complementarios y se muestran aparte (días y acierto al primer intento, con tendencia contra la semana anterior).
- Días desde que empezó, racha, minutos hablados (7 días) frente al **compromiso diario** y % de cumplimiento.
- **Nivel**: estimación del coach (herramienta `registrar_evaluacion`: A0–B2) al inicio y cada semana. Es orientativa, no una certificación.
- **A reforzar / Fortalezas**: temas de gramática y vocabulario con acierto bajo (<60%) o alto (≥80%), mínimo 4 respuestas (la app envía el tema de cada ejercicio).
- «Para animar hoy»: quienes llevan 2 días o más sin hablar con el coach.

Desde `/admin` se editan el tope diario de minutos (Inicio) y el compromiso diario de cada alumno (tabla de la empresa).

## Cuentas de dueños de empresa (`/cuenta`)

Cada dueño entra con su correo y su clave en `https://tu-dominio/cuenta` y ve **solo** el panel de los alumnos de su empresa (la empresa sale de la sesión firmada, no de la URL). Desde `/admin`, dentro de la empresa, la sección «Cuenta del dueño de la empresa» crea la cuenta: genera una clave temporal que se muestra una sola vez y, con SMTP configurado, se envía por correo. También permite generar una clave nueva o quitar la cuenta. El dueño puede cambiar su clave desde la barra lateral (al cambiarla, las sesiones anteriores dejan de valer). Hay bloqueo de 10 minutos tras 8 intentos fallidos. Las claves se guardan con PBKDF2-SHA256 y la sesión dura 14 días.

El enlace antiguo `/gerente?token=...` sigue funcionando (lo usas tú desde el panel de administración).


## Capacidad de la voz y prueba de carga
- Generar una frase nueva ocupa un motor de Kokoro; las frases ya generadas salen del caché sin gastar CPU.
- `MOTORES` (variable de entorno, 1 por defecto): cuántas frases nuevas se generan a la vez. Los pedidos extra esperan en fila.
- Para saber cuánto aguanta tu VPS: abre la terminal del servicio en Easypanel y ejecuta `python prueba_carga.py`. Te dice frases/min, lecciones nuevas por hora y cuánto espera la última persona si 10 abren su lección a la vez.
- `preparar_audios(frases)`: el coach la llama al crear una lección; el servidor genera los audios en segundo plano (misma voz y velocidad que usa la app, así coinciden con el caché) y el alumno no espera al abrir.

## Voz con Google Cloud (Neural2) en vez de Kokoro
- Lo único que va en Easypanel es el secreto: `GOOGLE_TTS_API_KEY=<tu clave>`. Todo lo demás (motor, voces, tope, precios, hilos) se cambia en el panel, página **Configuración** (`/admin/configuracion`), sin reiniciar. Las variables `TTS_MOTOR`, `GOOGLE_VOCES_MEZCLA`, `GOOGLE_VOZ_DEFECTO`, `GOOGLE_HILOS`, `GOOGLE_TOPE_CARACTERES_MES` y `MOTORES` solo sirven como valor inicial si no hay nada guardado en el panel.
- Con `TTS_MOTOR=kokoro` (o sin la variable) todo sigue como antes. Si Google falla y hay modelo de Kokoro instalado, el servidor usa Kokoro como respaldo.
- El caché es por frase, voz y velocidad, y los audios de Google se guardan aparte de los de Kokoro.
- En el panel de administración, «Probar voces» (`/admin/muestras`) reproduce la misma frase con Kokoro y con varias voces de Google para comparar.
- Mezcla de voces (Configuración): lista separada por comas. Cada frase recibe siempre la misma voz y distintas frases suenan con voces distintas para entrenar el oído. "Voz única" la anula.
- Tope de seguridad (Configuración, 2,000,000 por defecto; 0 = sin tope): caracteres que el servidor envía a Google al mes. Al alcanzarlo usa Kokoro como respaldo (si el modelo está instalado) y el panel de inicio lo avisa, junto con los caracteres del mes, el % de lo gratis y el costo estimado.
