English summary: [ROADMAP.md](ROADMAP.md)

# Plan de trabajo

Revisión externa recibida el 11/09/2026, verificada hallazgo por hallazgo y
reordenada por coste y beneficio. De los nueve hallazgos concretos que se
comprobaron, ocho eran reales. Este documento no resume esa revisión: la
convierte en trabajo.

Dos diferencias con el orden que proponía:

- **El plugin npm va después de los P0, no antes.** Ya somos un plugin de
  OpenCode; lo que cambia es cómo se distribuye, y eso no arregla ningún fallo.
- **El adiós a Python no va.** Ver el apartado 3.

---

## 1. Hecho en 3.4.0

Todo lo de aquí tiene test de regresión que falla sin el arreglo.

### Un fichero podía escribirse fuera del repositorio

`resolveInside` resolvía el directorio padre pero no el fichero final. Un `.py`
dentro del repo que fuese un symlink a cualquier otro sitio pasaba el control, y
la escritura seguía el enlace. Ahora se comprueba también el último componente.
`tests/guards.test.mjs`

### La herramienta se contradecía a sí misma sobre la cobertura

`bulk_read` calcula qué ficheros analizó el worker y cuáles no, imprime
`WARNING: x is unaccounted for`... y acto seguido marcaba **todos** los leídos
como cubiertos, con lo que el shunt tenía motivos para bloquear al orquestador
cuando intentase leer justamente el que no tenía evidencia. Ahora solo se marcan
los que el worker declaró analizados o irrelevantes.

### La cobertura casaba por subcadena

`foo.py` se daba por cubierto porque el worker había analizado
`tests/test_foo.py`. Ahora la coincidencia respeta los límites del nombre.
`tests/coverage.test.mjs`

### Se escribía antes de validar

El orden era: escribir, comprobar sintaxis, y deshacer desde un backup si
fallaba. Tres problemas en uno: si el backup había fallado (era *best effort* y
nadie lo miraba) el código roto se quedaba; la escritura no era atómica; y
`before` se había leído antes de una llamada al worker que tarda segundos, así
que una edición tuya en esa ventana se perdía sin avisar.

Ahora hay una única función, `applyVerified`, que releé el fichero y rechaza si
cambió, escribe en una copia temporal del mismo directorio, valida ahí, y solo
entonces hace `rename`. El original nunca está en riesgo y el backup pasa a ser
solo un artefacto de consulta. `tests/apply.test.mjs`

### La contabilidad se mentía en dos sitios

`prices.get("price_in") or 0.30` convertía un cero legítimo en tarifa por
defecto: **un worker local gratis se facturaba a precio de Gemini Flash**. Y se
aplicaba una sola tarifa a todas las llamadas aunque lector y escritor fuesen
modelos distintos. Ahora cada llamada se tarifa con el modelo que la atendió,
que ya venía registrado en la telemetría, y un modelo sin precio conocido se
declara como no tarifado en vez de inventarse una cifra.

### La economía ignoraba lo que cuesta el worker

El cálculo del suelo solo contaba el lado caro. Faltaba el término que siempre
favorece al sistema, que son los peligrosos. Contarlo sube el suelo un 11% con
Flash y un 8% con Flash Lite; si el worker fuese más caro por token que el
contexto que ahorra, el suelo es infinito y ahora se dice así en vez de dar un
número grande. `shunt doctor` avisa a quien tenga una config anterior.

### `shunt config` borraba tu política

Escribía el fichero entero, así que reconfigurar un modelo se llevaba por
delante `allowedProviders` o un `editPaths` ampliado a mano. Y contradecía lo
que promete el propio proyecto: `shunt update` no toca `shunt.json` ni con
`--force`. El fichero estaba a salvo del comando que suena peligroso y lo
reescribía el que suena inofensivo.

Ahora lo que el asistente **deduce** (precios, perfiles, economía) se reescribe,
porque mantener el precio de caché de un modelo que ya no usas es justo el fallo
al que este sistema es más propenso, y lo que tú **decides** (los allowlists de
rutas) se conserva y se te dice cuál se ha respetado.

### Tres cosas pequeñas

- El CI escuchaba `main` y la rama es `master`: **no se había ejecutado nunca**.
- `shunt stats` salía con código 1 en una instalación nueva, que es el caso
  normal y no un error.
- `shunt report --since` fallaba porque la implementación aceptaba `--days`, y
  además el informe no se acotaba al repositorio: acreditaba a donde estuvieras
  el trabajo hecho en cualquier otro sitio.

---

## 2. Siguiente: distribuir el runtime como paquete npm

Esto sí merece la pena y lo infravaloré en la primera respuesta.

Hoy copiamos 17 ficheros dentro de tu `.opencode/` y mantenemos un
`.shunt-manifest.json` con hashes para saber cuáles tocaste tú. Toda esa
maquinaria existe *solo* porque copiamos ficheros. Con `"plugin":
["opencode-shunt"]` en `opencode.json`, OpenCode lo instala con Bun en su propia
caché y actualizar deja de ser problema nuestro.

Tres riesgos concretos, por orden de gravedad:

**Doble carga.** La documentación de OpenCode lo dice literalmente: un plugin
local y uno npm con nombres parecidos se cargan por separado. Durante la
migración, quien tenga los dos ejecuta cada hook dos veces: doble bloqueo, doble
telemetría, doble llamada al worker. No da error, solo duplica la factura. Hace
falta un guardia explícito y que `shunt update` detecte y retire la copia local.

**La resolución del worktree empeora.** Acabamos de arreglar un fallo donde
OpenCode pasaba `worktree: "/"`, y el arreglo se apoya en `process.cwd()` porque
el plugin vive dentro del repo al que sirve. Desde `~/.cache/opencode/node_modules/`
esa red desaparece. Hay que resolverlo **antes** de mover el paquete, no después.

**`init` no desaparece.** Los prompts de agentes son personalizables por repo y
tienen que seguir aterrizando en `.opencode/agents/`.

A favor: las herramientas dejan de ser ficheros sueltos en `.opencode/tools/` y
se registran desde el plugin con la clave `tool:`, lo que las versiona junto a
él.

---

## 3. Lo que no vamos a hacer, y por qué

**Reescribir el CLI de Python en TypeScript.** Son 3.721 líneas y es donde vive
todo el análisis: `config` abre `opencode.db` con SQL para medir cuánto duran
tus conversaciones, `costs` reconstruye la factura, `replay` reevalúa decisiones
pasadas contra otros umbrales, `doctor` valida credenciales. Reescribir eso
compra "no necesitas Python" para gente que ya tiene instalado un asistente de
código. El envoltorio `npx` ya cubre a quien no quiera saber de Python.

Si algún día alguien se queja de verdad, se reconsidera. Hasta entonces es el
90% del esfuerzo por el 10% del beneficio.

---

## 4. Pendiente, por orden

1. **Permisos nativos.** Las herramientas acceden al disco sin pasar por
   `context.ask`, así que el modo plan y los permisos de edición de OpenCode no
   las vinculan. Es el P0 que queda sin hacer, y no lo he tocado porque cambia
   el contrato con OpenCode y quiero medirlo antes.
2. **Respetar `context.abort`** en lugar de fabricar un timeout propio.
3. **El worktree en las herramientas**, no solo en el plugin.
4. **Retirar el umbral del 146%** de `replay`: convertí una variación observada
   una vez en una regla general para descartar A/B, y eso no se sostiene.
   Conservar el replay como simulación, quitar la regla.
5. **Invalidar la cobertura por contenido**, no solo por sesión.
6. **CI en macOS** y pruebas de integración con OpenCode real.

## 5. Lo que sigue sin estar medido

Conviene tenerlo escrito para no confundirlo con lo que sí sabemos:

- El proyecto se ha usado en dos repositorios, los dos míos.
- La tarea de criterio (Opus 3/3, Gemini 3.1 Pro 2/3, Astra 3/3) es **una tarea
  y una pasada por brazo**. No es una medida de capacidad.
- Las cifras de compresión por operación (78-88%) y las de ahorro de factura por
  sesión (12-25%) miden cosas distintas y no deben presentarse juntas sin decir
  cuál es cuál.
- Nadie ha instalado esto en una máquina que no sea la mía.
