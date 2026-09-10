# Plan V2 — Arquitectura base de agentes para proyectos de desarrollo

**Qué es esto:** la estructura base que usaremos en todos los proyectos nuevos.
Un orquestador caro que piensa, y uno o varios trabajadores baratos que hacen el
trabajo mecánico. Dónde corren esos trabajadores es una decisión de despliegue,
no de arquitectura.

**Origen:** [Portal by Spotify cut my Claude Code token usage by 90%](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90).
Misma idea, llevada a OpenCode y sin depender de la plataforma interna de Spotify.

**Configuración actual:** Claude Opus 4.8 como orquestador, `gemini-2.5-flash`
como worker vía Vertex AI con credenciales ADC. Ollama queda como perfil
alternativo para cuando haya GPU disponible.
**Fecha:** 2026-09-10

Este documento reemplaza al `PLAN_V2.md` anterior. Incorpora la revisión de
`propuesta_v2.md` y reorienta el diseño hacia lo que realmente queremos: una base
reutilizable, no una optimización para un repositorio concreto.

---

# 1. El cambio de enfoque

La V1 se diseñó alrededor de una máquina concreta: una 4090 con Ollama. Funcionó,
pero eso metió el hardware dentro de la arquitectura. `bulk_read` habla con
`127.0.0.1:11434`, el shunt conoce una lista de proveedores llamados "locales", y
el modelo `qwen3-coder-shunt:30b` aparece cableado en el código.

Eso no sirve como base para todos los proyectos, porque el trabajador va a cambiar
según dónde se trabaje:

```
portátil de casa      → Qwen en la 4090          coste 0, 32k
máquina de empresa    → Qwen en Google Cloud     coste de VM, contexto mayor
cualquier sitio       → Gemini Flash             ~0,10 $/M, 1M de contexto
cualquier sitio       → OpenCode Zen             modelos gratuitos, 1M
sin GPU ni red        → sin trabajador           el orquestador lee él
```

**La arquitectura son roles. El modelo que ocupa cada rol es configuración.**

---

# 1b. Contraste con el artículo de Spotify

Conviene tenerlo claro porque explica una discrepancia que parece grave y no lo es.

## Lo que hacemos igual

Bloquear la lectura del modelo caro por encima de un umbral de líneas, interceptar
también `cat`/`head`/`tail`, delegar en un worker barato, dejar pasar las lecturas
con `offset`/`limit`, y hacer el umbral configurable. Ellos usan 350 líneas por
defecto, nosotros 400. Ellos usan Gemini 2.5 Flash como worker; nosotros, ahora,
también.

## Lo que tenemos y ellos declaran imposible

El artículo dice, textualmente, que no se puede delegar la edición: *"The worker
model's summaries don't include reliable line numbers"*. Ese es exactamente el
problema que resolvimos inyectando el número de línea en cada línea del contenido
que se manda al worker. Medido: el error de citación pasó de 2-25 líneas a 0-1, y
con Gemini Flash salieron 5 de 6 citas exactas y la sexta desviada una línea por
un blanco final.

También tenemos dos cosas que no aparecen en su diseño:

- **Verificación determinista de rutas** contra el disco, con los cuatro estados.
- **Output shunt.** Ellos sólo interceptan lecturas de fichero. La salida de un
  `pytest` o un `git diff` entra íntegra en el contexto de Claude.

## Lo que tienen y nos falta: `code-writer`

Su segundo modo genera código repetitivo —tests, stubs, configuración— a partir de
un fichero de referencia y **lo escribe directamente en disco, sin que Claude lo
vea**. Eso ataca los tokens de *salida*, que en Opus cuestan 25 $/M frente a 5 $/M
de entrada.

Es justo el punto débil de nuestras mediciones: en la V1, el test A ahorró 43% de
tokens pero sólo 9% de coste, porque el shunt reduce lo que el modelo lee y no lo
que escribe. **Si hay una sola cosa que añadir después de esto, es ésta.**

## Por qué su 90% y nuestro 22% no se contradicen

Miden cosas distintas, y esto invalida la comparación directa:

- **Ellos miden la operación:** los tokens que Claude habría consumido leyendo los
  ficheros, frente a los que consume leyendo el resumen. Es un ratio por delegación.
- **Nosotros medimos la sesión completa:** todo lo que se factura de principio a
  fin, incluyendo el razonamiento y la respuesta final, que no cambian.

Por su métrica estamos en su mismo rango: `bulk_read` analizó 42 KB y devolvió unos
2 KB, y el output shunt convirtió 170 KB de diff en 14 KB. Eso es un 91-95% en la
operación. Pero como el razonamiento y la salida de Claude no se reducen, el efecto
sobre la factura de una sesión real se queda en el 22-28% que medimos.

Ninguna de las dos cifras es falsa. La suya vende mejor; la nuestra es la que
aparece en la factura.

---

# 2. Los tres roles

```
                         TÚ
                          │
                          ▼
              ┌───────────────────────┐
              │      ORQUESTADOR      │   caro, razona
              │   Claude Opus 4.8     │   decide, revisa
              └───────────┬───────────┘
                          │
      ┌───────────────────┼───────────────────┐
      │                   │                   │
      ▼                   ▼                   ▼
   EXACTO              WORKER            ORQUESTADOR
  grep / glob      bulk_read +          lee rangos
                   local-explorer       concretos
  coste 0                │              y razona
  0 alucinación          ▼
                  perfil configurable
                  ┌──────────────────┐
                  │ qwen local 4090  │
                  │ qwen en GCP      │
                  │ gemini flash     │
                  │ zen free         │
                  └──────────────────┘
```

## 2.1 Orquestador

Piensa, diseña, decide y revisa. Es el único que produce la respuesta final y el
único autorizado a decidir sobre arquitectura, seguridad, concurrencia,
migraciones y corrección de datos.

Hoy: `anthropic/claude-opus-4-8`. La sección 6 plantea medir alternativas más
baratas, pero el rol no cambia.

## 2.2 Worker

Lee, busca, filtra, resume y localiza. Nunca decide. Su salida es siempre un mapa
con rutas, símbolos y rangos de línea exactos, nunca código completo.

Lo ocupan `bulk_read` (cuando sabemos qué ficheros) y `local-explorer` (cuando no).
El modelo detrás es intercambiable.

## 2.3 Exacto

`grep` y `glob`, que OpenCode ya trae y que son ripgrep. Coste cero, latencia
mínima y cero posibilidad de alucinar una ruta. Para "¿dónde está X?" no hace falta
ningún modelo.

Este nivel no requiere herramientas nuevas, sólo una regla de routing en el prompt
del orquestador que hoy no existe.

---

# 3. Perfiles de worker

Un perfil describe quién hace el trabajo sucio y con qué límites. Se declara en
`.opencode/shunt.json`, versionado con el proyecto, y se puede sobrescribir por
variable de entorno para pruebas.

Hay tres tipos de backend, que se diferencian sólo en cómo se autentican:

```json
{
  "profile": "gemini-flash",
  "bulkExempt": ["ollama", "google-vertex/gemini-2.5-flash"],
  "profiles": {
    "gemini-flash": {
      "kind": "vertex",
      "project": "production-400914",
      "location": "global",
      "model": "gemini-2.5-flash",
      "contextTokens": 1048576,
      "costPerMillionInput": 0.3
    },
    "local-4090": {
      "kind": "ollama",
      "baseURL": "http://127.0.0.1:11434",
      "model": "qwen3-coder-shunt:30b",
      "contextTokens": 32768,
      "costPerMillionInput": 0,
      "keepAlive": "30m"
    },
    "zen": {
      "kind": "openai-compatible",
      "baseURL": "https://opencode.ai/zen/v1",
      "model": "deepseek-v4-flash",
      "apiKeyEnv": "OPENCODE_API_KEY",
      "contextTokens": 1000000,
      "costPerMillionInput": 0.14
    }
  }
}
```

`kind: "vertex"` usa el endpoint OpenAI-compatible de Vertex y saca el token de las
credenciales ADC llamando a `gcloud auth application-default print-access-token`,
con caché de cincuenta minutos. No hace falta ninguna API key ni variable de
entorno: el proyecto va en el perfil.

`bulkExempt` acepta un proveedor (`"ollama"`) o un modelo concreto
(`"google-vertex/gemini-2.5-flash"`). Sin esa segunda forma, un worker en la nube
se bloquearía a sí mismo al intentar leer.

Reglas de diseño:

- **Un solo campo cambia de máquina a máquina**: `profile`. Todo lo demás se
  comparte.
- **`contextTokens` gobierna el troceado.** Con el perfil local se trocea a 32k;
  con Gemini no se trocea nunca. El código de `bulk_read` no necesita saber qué
  modelo hay detrás.
- **Pero un techo por llamada, independiente del contexto.** 400.000 caracteres,
  unos 125k tokens. Una ventana de un millón hace innecesario el troceado y también
  hace fácil enviar, y pagar, mucho más de lo que ninguna pregunta necesita.
- **`overflowProfile` sustituye a la escalada L1.5** del plan anterior. Si el
  contenido no cabe en el perfil activo, se usa el de desbordamiento en una sola
  llamada. Si no hay `overflowProfile`, se trocea y **se avisa explícitamente** de
  que la respuesta se basa en parte del código.
- **Sin perfil configurado, el sistema se comporta como la V1.**

## 3.1 Por qué esto sustituye al debate del umbral de lotes

El plan anterior proponía escalar a la nube a partir de tres lotes, y la revisión
pedía con razón medir ese número antes de fijarlo. Al medirlo salió esto:

```
BATCH_BUDGET_CHARS = 95.104 chars por lote
todo el código Python del repo, con números de línea: 268.661 chars = 2,82 lotes
los 20 ficheros mayores (MAX_FILES=20):              1,87 lotes
```

En `automation-cofers-engine` **el umbral de tres lotes es inalcanzable**, incluso
pasando el repositorio entero. Discutir el número era discutir sobre algo que aquí
no ocurre.

Con perfiles el problema desaparece: no hay umbral que calibrar. Si trabajas con
el perfil local y el contenido no cabe, se desborda al perfil configurado. Si
trabajas directamente con el perfil Gemini, nunca cabe mal. El dato que hay que
vigilar sigue siendo el mismo y ya se registra: cuántos lotes usa `bulk_read` en la
práctica.

## 3.2 Política de datos

La revisión proponía `allowCloudBulkCode: false` por defecto. Para este proyecto no
aplica: enviar código a Gemini es aceptable, y la base debe ser cómoda por defecto.

Pero la revisión señalaba un riesgo real que sí conservo, corrigiendo además un
hueco de su propia propuesta: **gatear sólo la escalada del worker es inconsistente
si el orquestador es un modelo del mismo proveedor**, porque ve el mismo código y
además el razonamiento y los diffs.

Así que la política, cuando haga falta, se expresa como proveedores autorizados a
ver el repositorio, y aplica a los tres roles por igual:

```json
{
  "allowedProviders": ["anthropic", "google", "ollama"]
}
```

Ausente significa "todos permitidos". En un repositorio con restricciones se
declara la lista y el sistema se niega a usar un perfil o un orquestador fuera de
ella. Es una línea de configuración, no un sprint.

---

# 4. Qué se conserva de la V1 sin tocar

Todo esto está validado con datos y no se discute:

- **Números de línea inyectados** en lo que se manda al worker. Bajó el error de
  citación de 2-25 líneas a 0-1. Es parte de la interfaz, no una optimización.
- **`num_ctx` fijo** en los perfiles Ollama. Cambiarlo fuerza una recarga de 5,2s.
- **Shunt consciente del proveedor y fail-open.** Un falso negativo cuesta tokens;
  un falso positivo rompe una sesión.
- **El orquestador verifica antes de actuar** sobre lo que diga el worker.
- **El usuario ve tres roles.** Los perfiles no son agentes: el orquestador no
  elige modelo, sólo pide trabajo.

---

# 5. Decisiones tomadas tras las dos revisiones

| Tema | Decisión | Motivo |
|---|---|---|
| Caché de `bulk_read` | No se construye. Caché en sombra en Sprint 3 | 7 llamadas reales, 0 repeticiones exactas |
| `code_search` | No se crea. Regla de prompt sobre `grep`/`glob` | La herramienta ya existe en OpenCode |
| Escalada L1.5 | Sustituida por `overflowProfile` | El umbral de lotes era inalcanzable aquí |
| `read_output` | Sólo abre artefactos registrados, nunca rutas libres | Aportación de `propuesta_v2.md`, es mejor |
| Política de nube | Lista de proveedores, aplica a los tres roles | Gatear sólo el worker dejaba la puerta principal abierta |
| Fallback sin desbordamiento | Debe avisar, nunca truncar en silencio | Cabo suelto de la revisión |

---

# 6. El coste, y lo único que lo mueve

El benchmark de la V1 dejó esta asimetría:

| Test | Ahorro de tokens | Ahorro de coste |
|---|---|---|
| A | 43,2% | **9%** |
| B | 60,3% | 50,6% |

La salida de Opus cuesta $25/M frente a $5/M de entrada, y el shunt no reduce la
salida. **Ninguna mejora del worker va a mover la factura.** El único resorte es el
modelo del orquestador:

| Orquestador | Contexto | Entrada $/M | Salida $/M | Frente a Opus |
|---|---|---|---|---|
| `anthropic/claude-opus-4-8` | 1M | 5,00 | 25,00 | referencia |
| `anthropic/claude-sonnet-4-6` | 1M | 3,00 | 15,00 | −40% |
| `google/gemini-3.1-pro-preview` | 1M | 2,00 | 12,00 | −55% |

Se mide sin escribir código, porque `bench.py` ya acepta `BENCH_MODEL` y ya
registra pasos y herramientas:

```bash
BENCH_MODEL=google/gemini-3.1-pro-preview ./bench.py --repo ... --name test-b-gemini "..."
BENCH_MODEL=anthropic/claude-sonnet-4-6   ./bench.py --repo ... --name test-b-sonnet "..."
```

**Criterio de adopción:** mantiene la exactitud de citas (8/8 en el Test B) y no
aumenta el número de pasos. Un modelo más barato que necesite el doble de pasos se
come su propia ventaja.

**Y el cambio se queda manual.** Nada de un router que adivine si un problema es
difícil: eso es sustituir una heurística por otra. Dos agentes explícitos,
`orchestrator` y `orchestrator-opus`, y tú eliges.

---

# 7. Modelos disponibles hoy

Precios reales del catálogo instalado, por millón de tokens.

**Para el rol worker:**

| Modelo | Contexto | Entrada | Notas |
|---|---|---|---|
| `qwen3-coder-shunt:30b` local | 32k | **0** | 100% GPU, 6.851 tok/s de prefill |
| `google/gemini-2.5-flash-lite` | 1M | 0,10 | El más barato con 1M |
| `google/gemini-3.1-flash-lite` | 1M | 0,25 | Más reciente |
| Zen `deepseek-v4-flash` | 1M | 0,14 | Vía OpenCode Zen |
| Zen `nemotron-3-ultra-free` | 1M | 0 | Gratuito |

Para dimensionar: 200.000 tokens de código cuestan 2 céntimos con
`gemini-2.5-flash-lite` y más de un dólar si los ingiere Opus.

**Sobre OpenCode Zen:** su valor es una sola autenticación para un centenar de
modelos, incluidos varios de 1M gratuitos. Encaja bien como perfil de worker
alternativo cuando no haya GPU. Lo que no haría es rutear el perfil local por Zen:
si el trabajo masivo vuelve a salir por la red teniendo una 4090 al lado, el
proyecto pierde su razón de ser.

---

# 8. Sprints

## Sprint 1 — Base configurable y fallos conocidos ✅ COMPLETADO

### 1.0 Perfiles de worker ✅
`opencode/lib/worker.ts` carga `.opencode/shunt.json`, resuelve el perfil activo y
habla con dos tipos de backend: `ollama` (`/api/chat`) y `openai-compatible`
(`/chat/completions`), que es el que usan Gemini y Zen. Sin fichero, comportamiento
idéntico a la V1.

*Verificado:* `SHUNT_PROFILE=local-openai` cambió el worker sin tocar una línea de
código, y el pie de `bulk_read` reportó `profile local-openai`. Como no hay
`GEMINI_API_KEY` en esta máquina, el camino de código de nube se validó contra el
endpoint OpenAI-compatible de Ollama, que es literalmente el mismo request y el
mismo parseo de `usage`. Queda pendiente una pasada real contra Gemini cuando haya
credencial.

### 1.1 Regla de routing exacto ✅
`orchestrator.md` ahora distingue los tres roles con una regla memorizable:
*"where* es una búsqueda, *how it works* es un worker, *what should we do* eres tú"*.

*Verificado, y con un efecto que no esperaba:* al pedirle análisis de dos ficheros
de 5 KB y 3 KB, el orquestador **se negó a usar `bulk_read`** y los leyó él,
argumentando que eran pequeños. Es la decisión correcta y demuestra que la regla no
sólo escala hacia abajo (usar `grep`) sino también hacia arriba.

### 1.2 `keep_alive` ✅ — sin `sudo`
El plan pedía editar el servicio de systemd. No hace falta: la API de Ollama acepta
`keep_alive` por petición, así que va en el cliente y es un campo del perfil.
Mejor solución que la planificada, porque viaja con el repositorio en lugar de
depender de la configuración de cada máquina.

*Verificado:* `UNTIL` pasó de "4 minutes from now" a "29 minutes from now",
`PROCESSOR` en 100% GPU y `load_duration` de 1,1 ms a 0,5 ms en la segunda llamada.

### 1.3 Verificación determinista de rutas ✅
`opencode/lib/verify-paths.ts`, con la máquina de estados
`VERIFIED` / `CORRECTED` / `AMBIGUOUS` / `NOT_FOUND`.

Una mejora sobre el diseño original: en lugar de resolver sólo cuando el basename
es único, se puntúa cada candidato por **cuántos segmentos finales de ruta comparte**
con lo que citó el worker. Un modelo que se equivoca de directorio suele acertar el
último segmento o dos, así que el mejor match por sufijo es casi siempre el fichero
pretendido. Esto convirtió 2 de 4 ambigüedades en correcciones seguras sin adivinar
nunca entre candidatos empatados.

*Verificado* con 12 rutas construidas a propósito: 6 correctas → `VERIFIED`,
4 con directorio erróneo → `CORRECTED` (las 4 bien, incluida la que falló de verdad
en la V1: `reconciliation/reconciliation_activities.py` →
`src/cofers_engine/worker/reconciliation_activities.py`), 2 inexistentes →
`NOT_FOUND`. Un segundo test con basenames desnudos (`__init__.py`, 23 coincidencias)
confirma que lo genuinamente ambiguo se marca y **no se reescribe**.

### 1.4 El bug que encontró el propio orquestador

La primera versión reescribía cada corrección con `split().join()`. Cuando el worker
citaba el mismo fichero en forma corta (`chat/__init__.py`) y en forma completa
(`src/cofers_engine/chat/__init__.py`), la sustitución por subcadena alcanzaba
también la cola de la ruta ya correcta y producía
`src/cofers_engine/src/cofers_engine/chat/__init__.py`.

Dos cosas que merece la pena registrar:

1. **Claude lo detectó solo.** Hizo `ls` de la ruta "corregida", vio que no existía,
   descartó la corrección y siguió con las rutas originales. El diseño de "avisar
   siempre, el orquestador verifica" funcionó como red de seguridad ante un fallo de
   la propia red de seguridad.
2. **La corrección es estructural**, no un parche: una sola pasada de
   `text.replace(PATH_PATTERN, ...)` con el mismo patrón que extrae los candidatos.
   Así los límites de cada coincidencia los define el mismo regex y una ruta corta no
   puede solaparse con el final de una larga. Hay test de regresión.

### 1.5 Lista de exención del shunt, por modelo y no por localidad ✅
`shunt.ts` ya no lleva cableada una lista de "proveedores locales". Lee `bulkExempt`
de `shunt.json` y acepta tanto `"ollama"` como `"google/gemini-2.5-flash-lite"`,
porque en esta arquitectura un worker no es necesariamente local. Sin esto, un worker
en Gemini se bloquearía a sí mismo.

*Verificado:* Claude sigue recibiendo el bloqueo con el mensaje de guía al leer un
fichero de 548 líneas, y `@local-explorer` sigue leyendo sin restricciones.

## Sprint 2 — Output shunt ✅ COMPLETADO

La división de trabajo es lo importante: **lo que el orquestador necesita exacto se
extrae con expresiones regulares y sobrevive literal; el modelo sólo resume el
ruido.** Un resumidor que parafrasea el mensaje de una aserción es peor que no
tener resumidor.

### 2.1 Extracción determinista ✅
`extractVerbatim` conserva, según el tipo de salida:

- **pytest:** líneas `FAILED`/`ERROR`, todo lo que empieza por `E   `, las
  referencias `fichero.py:12: TypeError` y la línea de recuento final.
- **git diff:** cabeceras `diff --git`, `@@`, y los marcadores de fichero nuevo,
  borrado o renombrado.
- **genérico:** líneas con marcadores de error y trazas de pila.

Deduplicado y con techo de 8 KB. Cuando el techo recorta, se dice cuántas líneas
faltan en lugar de dejar que el orquestador suponga que lo vio todo.

### 2.2 `read_output(id, offset, limit)` ✅
`limit` es obligatorio y sólo abre salidas registradas, nunca rutas del
repositorio. Esto resuelve el choque que habíamos identificado entre el output
shunt y el read shunt: la vía de acceso al raw no pasa por `read`, así que no la
bloquea.

### 2.3 Resumen por tipo ✅
`pytest`, `git-diff` y genérico, detectados por comando y por contenido.

### 2.4 Umbrales
30 KB o 400 líneas, y separados de los del read shunt: un fichero de 400 líneas
merece resumen, una salida de 400 líneas es a menudo la respuesta misma.

### 2.5 Resultados medidos

| Caso | Raw | Resumen | Ahorro | Tiempo |
|---|---|---|---|---|
| pytest, 5.239 líneas, 93 fallos | 263 KB | 9,6 KB | **96,3%** | 8,0s |
| pytest, 873 líneas, 18 fallos | 43 KB | 5,2 KB | 87,9% | 6,6s |
| git diff limpio, 3.000 líneas | 110 KB | 4,4 KB | 96,1% | 9,7s |

*Aceptación cumplida:* con 5.239 líneas de salida, Claude diagnosticó los
93 fallos agrupados en sus 5 causas reales, incluidas las condiciones exactas
(`amount % 700 == 0` y `id % 11 == 0`), que no aparecían escritas en ninguna
línea de la salida.

### 2.6 Tres cosas que la implementación descubrió

**OpenCode ya truncaba la salida antes que nosotros.** Corta `bash` en unos 50 KB
y guarda el original en `~/.local/share/opencode/tool-output/`, dejando la ruta en
la propia salida. La primera versión resumía la copia truncada, que es peor que no
hacer nada: el resultado se lee como un resumen de todo. Ahora se recupera el
fichero completo antes de resumir. Medido: de 760 líneas que veía el hook a las
5.239 reales. Y `read_output` guarda un puntero al artefacto de OpenCode en vez de
duplicar un cuarto de megabyte de logs.

**El modelo parafrasea los mensajes de error.** En una sola prueba convirtió
`could not reach temporal at localhost:7233` en "temporary server" y
`unsupported ledger` en "unsupported currency". El bloque literal tenía los dos
exactos. También situó un fallo en la línea de la llamada en vez de la del `raise`.
Esto no es un defecto a corregir con un prompt mejor: es la razón de que la
extracción sea determinista.

**Un diff puede ser casi todo binario.** El primer diff de prueba eran 2,4 MB en
4.000 líneas porque el repositorio contiene `docs/architecture.pdf`. Al worker le
llegaban 95 KB de bytes de PDF y devolvía un ensayo genérico que acababa
preguntando "is there something specific you'd like me to explain?". Con el filtro
de bloques binarios y líneas de más de 1.000 caracteres, el mismo diff produce
"17 files changed" con descripción funcional por fichero. El artefacto guardado
sigue siendo byte a byte.

### 2.7 Contaminación del baseline, corregida
El output shunt es un plugin y actúa por proveedor, así que habría afectado también
al agente de control del benchmark, invalidando la comparación. Ahora hay exención
por nombre de agente y `benchmark-baseline` no ve ningún shunt, ni de lectura ni de
salida.

## Sprint 3 — Medir

### 3.0 Lo primero que midió el sprint fue el propio benchmark

Al repetir los tests aparecieron estas cifras en el **brazo de control**, que por
definición no cambia entre versiones:

```
Test B baseline:  230.217  ->  213.217  ->  135.101 tokens
Test C baseline:   42.987  ->   52.054  ->   71.033 tokens
```

Un ±25% de dispersión en la referencia. El modelo elige una estrategia distinta
cada vez —cuántos ficheros lee, si lanza un subagente, cuántos pasos da— y eso
domina el resultado. **Con ahorros en el rango del 26 al 60%, una sola ejecución
por brazo no distingue una mejora de la varianza.**

Los números de la V1 (48,6% agregado) eran `n=1`, así que hay que tratarlos como
una indicación, no como una medida. Lo mismo vale para todo lo que se concluyó de
la primera pasada de la V2.

`bench.py` ahora acepta `--repeat N`, agrega por mediana, muestra mínimo y máximo,
y en la columna de fiabilidad marca `solido` sólo con `n>=3` y dispersión del
control por debajo del 20%. Todo lo demás sale etiquetado con su `n` y su
dispersión, para que no se pueda leer ruido como señal por descuido.

### 3.1 Un defecto del criterio de routing, encontrado por el camino

La primera pasada de la V2 mostró el test B usando sólo `grep` y `read`, sin
delegar nada. La causa estaba en cómo redacté la regla del Sprint 1: decía que
`bulk_read` es para cuando los ficheros "son grandes". Los ficheros de
reconciliación son pequeños de uno en uno, así que por ese criterio no aplicaba.

Pero el coste no está en el tamaño de cada fichero: **está en el volumen agregado**,
porque todo lo leído permanece en la conversación y se reenvía en cada paso
posterior. Ocho ficheros pequeños cuestan mucho más de lo que sugieren sus bytes.

El criterio corregido es un disparador concreto: *si responder implica leer más de
dos ficheros, o más de unas cuatrocientas líneas en total, delega* — por pequeño
que sea cada fichero. No sabemos aún cuánto mejora esto, por lo dicho en 3.0.

### 3.3 Resultado con n=3 (parcial: A, B, C)

| Test | Ahorro tokens | Ahorro coste | Dispersión del control |
|---|---|---|---|
| A, fichero grande | +25,6% | +21,2% | ±146% |
| B, multifichero | **+22,2%** | **+28,0%** | **±6%** |
| C, exploración | −54,1% | +7,9% | ±60% |
| Agregado A,B,C | +13,7% | +22,2% | |

**El único test con un control estable es el B: 22% de tokens y 28% de coste.** Esa
es la cifra defendible. El 48,6% de la V1 era `n=1` y no se reproduce.

Dos cosas que sí salen limpias del ruido:

**El shunt hace el coste predecible.** En el test A el baseline osciló entre 50.559
y 170.403 tokens según lo que el modelo decidiera leer, mientras el brazo con shunt
se quedó entre 60.412 y 64.917. Tres veces de variación contra un 7%. Para
presupuestar, eso vale más que el porcentaje de ahorro.

**En preguntas pequeñas el shunt encarece.** El test C sale consistentemente peor
con shunt (57.158 frente a 37.103). Montar una llamada al worker y su resumen es
coste añadido cuando la respuesta se resolvía con un `grep` y una lectura corta. El
artículo de Spotify dice exactamente lo mismo: *"counterproductive for small ones.
The line threshold exists for this reason"*. **Es el defecto abierto más
importante.**

### 3.4 Pendiente

1. Test D con `--repeat 3`. Se abortó a media ejecución.
2. Caché en sombra: registrar los aciertos que habría tenido, sin cachear.
3. `shunt-stats`.
4. Benchmark del orquestador: Opus vs Sonnet vs Gemini Pro. Sin código.
5. Benchmark de perfiles de worker: Qwen local vs Gemini Flash, misma pregunta.

## Sprint 4 — Experimentos que pueden salir que no

- Caché real, sólo si la sombra superó el 10% de aciertos.
- `local-writer`, con los criterios cuantificados sin rebajarlos.
- 64k local con KV cuantizado, sólo si hay troceado frecuente.
- Perfil `gcp-qwen`, cuando haya máquina.

## Sprint 5 — Uso real y globalización

Una o dos semanas de trabajo real, revisión de telemetría, ajuste de umbrales, y
sólo entonces mover lo genérico a `~/.config/opencode/`.

---

# 9. Criterios de éxito

Cerrados en el Sprint 1:

- [x] Cambiar de worker es un campo en un JSON, no un cambio de código.
- [x] Ninguna ruta inexistente llega al orquestador sin estar marcada.
- [x] Las rutas resolubles se corrigen solas; las empatadas nunca.
- [x] Cuando se trocea sin desbordamiento, el orquestador se entera.
- [x] El orquestador usa búsqueda exacta para localizar.
- [x] Sin recargas de Ollama en uso normal.

Cerrados en el Sprint 2:

- [x] Los outputs gigantes se reducen antes de entrar al frontier.
- [x] Errores y stack traces se conservan literalmente.
- [x] El acceso al raw no pasa por `read`, así que el read shunt no lo bloquea.
- [x] El baseline del benchmark no recibe ningún shunt.

Pendientes:

- [ ] Una pasada real contra Gemini, cuando haya `GEMINI_API_KEY`.
- [ ] Decisión con datos sobre orquestador alternativo (Sprint 3).
- [ ] Exactitud de citas al nivel de la V1, 8/8 en el Test B (Sprint 3).

---

# 9b. Mapa de ficheros

```
opencode/
  lib/worker.ts          perfiles de worker y los dos clientes (ollama / openai)
  lib/verify-paths.ts    verificacion determinista de rutas
  lib/output-shunt.ts    extraccion literal + resumen de salidas grandes
  tools/bulk-read.ts     la tool: trocea, delega, verifica, telemetria
  tools/read-output.ts   acceso paginado al raw que aparto el output shunt
  plugins/shunt.ts       read shunt (before) y output shunt (after)
  agents/orchestrator.md rol orquestador, routing y uso de read_output
  agents/local-explorer.md
  agents/benchmark-baseline.md
  shunt.json             perfiles y perfil activo (especifico de cada maquina)
install.sh               instala en un repo, preserva shunt.json existente
bench.py                 A/B con y sin shunt
```

Los dos umbrales que más se van a tocar en la práctica, por si hace falta
ajustarlos sin leer el código: `SHUNT_MAX_LINES` (lectura, 400) y
`SHUNT_OUTPUT_MAX_LINES` (salida, 400).

---

# 10. Lo que no vamos a hacer

Nada de RAG, embeddings, base vectorial, LangChain, LangGraph, CrewAI, AutoGen,
LiteLLM, servicio propio, Redis ni modelo de 70B local.

Y tres específicas de esta fase:

**Un agente por cada modelo disponible.** Tener Claude, Gemini, Zen y Qwen no es
razón para tener cuatro agentes. Los modelos son perfiles; los roles son tres.
Cada nivel visible es una decisión que el orquestador puede tomar mal.

**Un router automático que adivine la dificultad.** Primero determinismo y datos,
después automatización.

**Construir antes de medir.** La caché parecía obvia y dio 0%. El umbral de tres
lotes parecía razonable y era inalcanzable. Es la lección del proyecto.

---

# 11. Resumen

La V1 demostró que separar "quien lee" de "quien piensa" funciona y ahorra la
mitad del contexto sin perder calidad.

La V2 convierte ese hallazgo en **una base reutilizable**: tres roles fijos, un
worker intercambiable por configuración, y todo lo dudoso decidido por medición y
no por intuición.

El Sprint 1 es menos de un día y deja el sistema listo para funcionar igual en tu
4090, en una VM de Google Cloud o contra Gemini.
