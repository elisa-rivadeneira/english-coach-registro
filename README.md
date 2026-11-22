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
