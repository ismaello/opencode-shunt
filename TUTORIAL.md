# Tutorial: qué es esto y cómo se usa

Escrito para alguien que no ha visto el proyecto nunca. No hace falta entender
nada de modelos de lenguaje para seguirlo.

---

## 1. El problema, en una frase

Cuando le pides algo a un modelo potente (Claude, GPT, Gemini Pro), **pagas por
cada letra que entra y por cada letra que sale**. Y muchas de esas letras no
necesitaban un modelo potente.

Una comparación: imagina que contratas a un arquitecto a 200 €/hora. Le pides
una reforma. El arquitecto es imprescindible para decidir dónde va el tabique.
Pero si además le pones a **leer en voz alta las 400 páginas de la normativa**,
le estás pagando 200 €/hora por hacer de lector. Eso es lo que pasa cuando un
modelo caro lee tu repositorio entero para responder "¿dónde se valida el
usuario?".

**Este proyecto pone un becario al lado del arquitecto.** El becario cuesta 50
veces menos. El becario lee las 400 páginas y dice: "página 212, párrafo 3, y
contradice la 87". El arquitecto solo recibe esa frase y sigue decidiendo.

Eso es todo. El resto del documento es cómo se monta y qué se ve cuando
funciona.

---

## 2. Las tres piezas

| Pieza | Quién es | Qué hace | Cuánto cuesta |
|---|---|---|---|
| **Orquestador** | Tu modelo caro | Piensa, decide, revisa, escribe el código importante | Caro. Es lo único que merece la pena pagar |
| **Worker** | Un modelo barato o local | Lee mucho y resume, aplica cambios repetitivos | Casi gratis. Medido: **~2% de la factura** |
| **Tú** | Tú | Le pides cosas en lenguaje normal | — |

Tú hablas **solo con el orquestador**. El worker no se ve nunca: el orquestador
lo llama por su cuenta cuando toca. No tienes que acordarte de nada.

Los tres roles los rellenas tú al configurar. Ejemplos que funcionan:

- Claude Opus de orquestador + Gemini Flash de worker (lo que uso yo)
- Gemini 3.1 Pro de orquestador + Gemini Flash Lite de worker (más barato)
- GPT-6 Astra de orquestador + Qwen en tu propia GPU de worker (el worker sale gratis)
- DeepSeek haciendo los dos papeles

---

## 3. Qué necesitas antes de empezar

1. **OpenCode instalado.** Es el programa donde escribes tus peticiones.
   → https://opencode.ai
2. **Credenciales de un modelo caro** para el orquestador. Una de estas:
   - Claude: `ANTHROPIC_API_KEY` o `opencode auth login`
   - Gemini: `gcloud auth application-default login` + `GOOGLE_CLOUD_PROJECT`
   - OpenAI: `OPENAI_API_KEY`
3. **Un worker.** O credenciales de un modelo barato en la nube, o
   [Ollama](https://ollama.com) corriendo en tu máquina (gratis, pero necesita
   GPU para ir rápido).
4. **Python 3.10 o superior**, solo para instalar. El sistema en sí es
   TypeScript.

---

## 4. Instalación: cuatro comandos

```bash
pipx install opencode-shunt     # o: uv tool install opencode-shunt

cd tu-proyecto
shunt init                      # copia el sistema a ./.opencode
shunt config                    # te pregunta quién hace cada papel
shunt doctor                    # comprueba que funciona de verdad
```

Y a partir de ahí trabajas como siempre:

```bash
opencode --agent orchestrator
```

### Qué te pregunta `shunt config`

Tres preguntas, y nada más:

```
Measured from 126 of your own sessions: conversations run about 17k tokens,
and what you read stays in context for roughly 2 more turns.

This repository looks like: python (1)
Ready on this machine: google-vertex, ollama, anthropic

Who orchestrates? This one reasons, designs and reviews. Its own output is
the expensive part of the bill, which is the whole reason the other two exist.
  1. Anthropic (Claude) - claude-opus-5 (default)
  2. Anthropic (Claude) - claude-sonnet-4-6
  3. Google Gemini (Vertex AI) - gemini-3.1-pro-preview
  4. OpenAI - gpt-6-astra  [not configured on this machine]
  5. DeepSeek - deepseek-v4-pro  [not configured on this machine]
choice [1]:

Who reads in bulk? The job is obedience to a rigid format and exact line
citations, so cheap and fast wins here.
  1. Ollama (local GPU) - qwen3-coder:30b  free (default)
  2. Google Gemini (Vertex AI) - gemini-3.1-flash-lite  $0.25/M
choice [1]:

Who writes? ...
```

Los modelos y los precios de esa lista **no están escritos en nuestro código**:
salen de la tabla que mantiene OpenCode. Un catálogo a mano se queda viejo
siempre, y aquí un precio viejo no es un detalle cosmético — el punto a partir
del cual delegar sale rentable se calcula con esos números.

**Todo lo demás lo mide en vez de preguntártelo.** Lee la base de datos de
OpenCode para averiguar cuánto duran tus conversaciones, mira tu repositorio
para saber en qué lenguaje está y dónde tienes los tests, y calcula con eso a
partir de qué tamaño delegar sale rentable. Son cosas que nadie sabe de sí
mismo, así que preguntarlas daría peores respuestas que medirlas.

---

## 5. Qué ficheros aparecen en tu proyecto

Después de `shunt init` y `shunt config`:

```
tu-proyecto/
├── .opencode/
│   ├── agents/
│   │   └── orchestrator.md        ← las instrucciones del jefe
│   ├── plugins/
│   │   └── shunt.ts               ← el guardia: intercepta lecturas caras
│   ├── tools/
│   │   ├── bulk-read.ts           ← herramienta: "leer mucho y resumir"
│   │   ├── delegate-edit.ts       ← herramienta: "cambio repetitivo"
│   │   └── delegate-write.ts      ← herramienta: "escribe este fichero"
│   ├── lib/
│   │   └── economics.ts           ← la aritmética de cuándo delegar sale a cuenta
│   ├── shunt.json                 ← política del proyecto → SÍ se commitea
│   └── shunt.local.json           ← tu máquina → NO se commitea (se ignora solo)
└── opencode.json                  ← config de OpenCode (se le añade un bloque)
```

Los dos ficheros que te importan son los últimos:

**`shunt.json`** — decisiones sobre *el proyecto*. Van al repositorio para que
tu compañero tenga las mismas reglas:

```json
{
  "_roles": {
    "orchestrator": "anthropic/claude-opus-5",
    "reader":       "google-vertex/gemini-3.1-flash-lite",
    "writer":       "google-vertex/gemini-3.8-flash"
  },
  "writePaths": ["tests/**", "**/test_*.py"],
  "editPaths":  ["**/*.py", "**/*.js", "tests/**"],
  "economics": {
    "assumedConversationTokens": 18000,
    "remainingTurns": 2,
    "cacheWritePerMillion": 6.25,
    "cacheReadPerMillion": 0.5
  }
}
```

- `writePaths`: dónde puede el worker **crear ficheros desde cero**. Corto a
  propósito: un fichero que nadie lee solo es seguro donde un test puede
  juzgarlo.
- `editPaths`: dónde puede **modificar** ficheros. Más amplio, porque un cambio
  vuelve como un diff que puedes revisar.
- `economics`: los números con los que se calcula si delegar sale a cuenta.
  Los pone `shunt config` midiendo; no los toques a mano.

**`shunt.local.json`** — tu máquina. Se ignora en git automáticamente, porque
commitearlo o filtra algo o le rompe la configuración al siguiente que clone:

```json
{
  "profile": "google-vertex-reader",
  "profiles": {
    "google-vertex-reader": {
      "kind": "vertex",
      "model": "gemini-2.5-flash",
      "project": "tu-proyecto-gcp",
      "location": "global"
    }
  }
}
```

**Las claves de API no se escriben nunca en ninguno de los dos.** Se guarda el
*nombre* de la variable de entorno donde está la clave, jamás su valor.

---

## 6. Flujos de ejemplo

Aquí está lo que pasa de verdad. Seis casos, del más común al más raro.

### Flujo A — Preguntas algo sobre código que ocupa mucho

**Tú escribes:**

```
¿Cómo se valida que un cliente no vea los datos de otro?
```

**Qué pasa por dentro:**

```
1. El orquestador no sabe qué ficheros mirar. Llama a @explorer (worker barato),
   que le devuelve una lista: services/runs.py, auth/tenant.py, api/router.py

2. El orquestador llama a:
      bulk_read(
        question = "¿Cómo se valida el aislamiento entre clientes?",
        paths    = ["src/services/runs.py", "src/auth/tenant.py", "src/api/router.py"]
      )

3. La herramienta lee los 3 ficheros del disco: 1.240 líneas, 48 KB.
   Les añade números de línea y se los manda al worker.
   >>> Estas 48 KB NUNCA llegan al modelo caro. <<<

4. El worker responde con un formato rígido.

5. El orquestador recibe solo esto (5,8 KB):
```

```
COVERAGE:
- src/services/runs.py: analysed - arranca y consulta ejecuciones, es donde se comprueba el tenant
- src/auth/tenant.py: analysed - extrae el tenant del token
- src/api/router.py: not relevant - solo enruta, no valida nada

HALLAZGOS

src/services/runs.py:112-118  _authorized_handle()
  Compara el tenant del token con el del workflow antes de devolver el handle.
  Es el único sitio donde se hace esa comprobación.

src/services/runs.py:203  signal_run()
  AVISO: llama a client.get_workflow_handle() directamente, sin pasar por
  _authorized_handle(). Se salta la comprobación de tenant.

---
bulk_read: 1240 lines / 48 KB analysed by gemini-2.5-flash in 6.2s.
These lines did not enter your context.
paths: 3 correct
```

**Lo que ganas:** el modelo caro ha visto 5,8 KB en vez de 48 KB. **88% menos**
(mediana medida sobre llamadas reales). Y con los números de línea puede ir a
mirar exactamente `runs.py:203` si quiere, gastando 15 líneas y no 1.240.

---

### Flujo B — El fichero es pequeño

**Tú escribes:**

```
Lee src/config.py y dime qué variables de entorno usa
```

**Qué pasa:** nada especial. `config.py` son 80 líneas / 2 KB. El sistema **lo
deja pasar directo** al modelo caro.

Y si el orquestador intenta delegarlo por su cuenta, la herramienta se niega y
le explica por qué:

```
bulk_read declined: these files total 80 lines / 2.1 KB, below the 12.6 KB at
which delegating starts to pay for itself. Read them directly.
Delegating this would cost about $0.011 more than reading it.
```

**Por qué importa:** delegar cuesta un viaje de ida y vuelta. Por debajo de
cierto tamaño, ese viaje cuesta más que lo que ahorras. El umbral no está
inventado: sale de los precios reales de tu proveedor y de lo que duran tus
conversaciones. Un sistema que delega *todo* sale más caro que no tener sistema.

---

### Flujo C — El orquestador intenta leer algo grande y se le impide

**Tú escribes:**

```
Revisa src/engine/pipeline.py y dime si hay problemas
```

`pipeline.py` son 2.100 líneas / 80 KB. El orquestador va a leerlo entero.
**El guardia lo corta antes de que salga la petición:**

```
SHUNT: read of src/engine/pipeline.py blocked (2100 lines / 80 KB).
This would burn frontier context on bulk reading. Instead:
  - bulk_read(question, paths) to answer a question about these files locally, or
  - @explorer if you do not yet know which files matter, or
  - read with offset/limit if you already know the exact lines you need.
```

El orquestador lee el mensaje, entiende la alternativa y llama a `bulk_read`.
**Tú no ves nada de esto**: ves la respuesta, y te ha costado la décima parte.

**Por qué es un bloqueo y no un consejo:** un consejo que el modelo tiene que
acordarse de seguir es un consejo que a veces no sigue. Y cuando no lo sigue,
no hay ningún aviso: te llega una respuesta perfectamente buena con la factura
completa.

---

### Flujo D — Un cambio repetitivo en muchos sitios

**Tú escribes:**

```
Añade docstrings a todas las funciones de src/services/runs.py
```

Son 21 funciones. Lo caro aquí **no es leer, es escribir**: el modelo caro
tendría que emitir 21 docstrings *más el código de alrededor dos veces* (el
texto a buscar y el texto con el que reemplazarlo).

**Qué pasa:**

```
1. El orquestador llama a:
      delegate_edit(
        path        = "src/services/runs.py",
        instruction = "Añade un docstring de una línea a cada función pública.
                       Estilo imperativo. No cambies ninguna otra cosa."
      )

2. Se comprueba que src/services/runs.py está en editPaths.  ✓
3. Se hace copia de seguridad del fichero.
4. El worker devuelve el fichero completo, ya modificado.
5. >>> El diff se calcula AQUÍ, no lo reporta el worker. <<<
6. Comprobaciones automáticas:
      - ¿el fichero sigue siendo Python válido?            ✓
      - ¿ha metido claves o secretos?                      ✓
      - ¿ha borrado código con la excusa de editarlo?       ✓
      - ¿el diff toca medio fichero (reescritura disfrazada)? ✓
7. Se escribe. El orquestador recibe un resumen del diff (30 líneas).
```

```
delegate_edit: src/services/runs.py, +21 -0 lines across 21 sites.
Verified: parses as Python, no secrets, no deletions.
Backup at ~/.local/share/opencode-shunt/backups/runs.py.1757577600

@@ -45,6 +45,7 @@
 def start_run(tenant_id: str, definition_id: str) -> Run:
+    """Arranca una ejecución nueva para el cliente indicado."""
     definition = load_definition(definition_id)
... 28 more lines
```

**Lo que ganas: 78% menos tokens de salida** (medido sobre este caso exacto:
21 docstrings en un fichero de 308 líneas, tests pasando, y el fichero idéntico
al original salvo los docstrings).

**El punto clave:** el diff lo calculamos nosotros comparando antes y después.
Si el worker dice "solo he añadido docstrings" pero ha reescrito la mitad del
fichero, el diff lo delata y el cambio se rechaza. No hay que confiar en el
worker.

---

### Flujo E — Crear un fichero nuevo

**Tú escribes:**

```
Escribe tests para la función start_run
```

```
1. El orquestador llama a:
      delegate_write(
        path            = "tests/test_runs.py",
        instruction     = "Tests para start_run: caso normal, tenant equivocado,
                           inputs inválidos. Usa pytest y los fixtures de conftest.",
        reference_paths = ["src/services/runs.py", "tests/conftest.py"]
      )

2. ¿tests/test_runs.py está en writePaths?   ✓
3. ¿existe ya el fichero? No. (Si existiera, copia de seguridad primero.)
4. El worker lo escribe. Comprobaciones de sintaxis y secretos.
5. El orquestador recibe: la ruta, el número de líneas, los nombres de los tests.
   NO recibe el cuerpo del fichero.
```

**Lo que ganas: 76% menos tokens de salida.**

**Aviso importante:** `delegate_write` solo puede escribir en `writePaths`, que
por defecto es únicamente tests y andamiaje. La razón es honesta: **un test
generado que pasa puede estar comprobando lo que no debe.** Un fichero que nadie
lee solo es seguro donde ejecutar los tests puede juzgarlo. Para código de
verdad se usa `delegate_edit`, porque eso vuelve como un diff revisable.

Y por debajo de todo hay una lista que **nadie puede ampliar**: ficheros de CI,
migraciones de base de datos, lockfiles y `.opencode/` no los toca ningún worker
aunque tú configures que sí.

---

### Flujo F — Los tests fallan y el error es enorme

**Tú escribes:**

```
Ejecuta los tests y arregla lo que falle
```

El orquestador lanza `pytest`. Salen **1.800 líneas** de salida: 3 fallos y
1.700 líneas de ruido, trazas repetidas y avisos de deprecación.

**Qué pasa:** antes de que esa salida llegue al modelo caro se resume. Pero con
una regla: **la parte crítica se extrae mecánicamente y se conserva letra por
letra.** El mensaje de error y la traza no se "resumen" nunca, porque un error
parafraseado es un error inútil.

El orquestador recibe unas 140 líneas: los 3 fallos completos y exactos, más
un recuento del resto.

**Lo que ganas: 92% menos** (mediana medida).

---

## 7. Cómo sabes que está funcionando

Esta es la parte que más importa, y ahora explico por qué.

```bash
shunt doctor     # ¿está bien montado? Contesta sí o no, y qué arreglar
shunt costs      # escribe shunt-costes.md: qué ha costado este repo y en qué
shunt stats      # qué ha ahorrado cada operación
shunt report     # ahorro estimado sobre sesiones reales
shunt replay     # ¿y si cambio un umbral? Gratis, sin gastar nada
```

### Por qué `shunt doctor` no es un adorno

**Todas las formas en que este sistema se rompe son silenciosas, y todas caen
del lado caro.** Estas tres están comprobadas, no son hipótesis:

| Si pasa esto... | ...lo que ves es |
|---|---|
| `shunt.json` mal escrito (una coma de más) | OpenCode arranca, responde con normalidad, y no dice nada. El sistema simplemente no está |
| El perfil del worker no existe | `bulk_read` falla, el orquestador lee los ficheros él mismo, te da una respuesta buenísima y te cobra el precio completo |
| El orquestador aparece en la lista de exentos | Nada se desvía nunca. Y como la exención se comprueba *antes* de escribir telemetría, no queda ni un registro |

Fíjate en la consecuencia: **una instalación rota y una sin usar se ven
exactamente igual — un informe vacío.** Nada dentro del sistema puede
distinguirlas. Para eso está `doctor`, y por eso comprueba el reparto de
papeles y no solo que los ficheros estén ahí.

Salida cuando todo va bien:

```
[  ok  ] runtime installed, version 3.2.0
[  ok  ] shunt.json and shunt.local.json are valid JSON
[  ok  ] orchestrator anthropic/claude-opus-4-8 is not in the exempt list
[  ok  ] profile google-vertex-reader resolves, credentials present
[  ok  ] 77 delegations in this repository, 0 failures
```

Y cuando algo falla, dice qué y cómo se arregla:

```
[ FAIL ] vertex credentials expired
           ADC token rejected: invalid_rapt.
           Fix: gcloud auth application-default login
```

### `shunt costs`: la factura en lenguaje humano

Genera un Markdown con lo que llevas gastado en **este** repositorio:

```markdown
## Resumen

- **Gasto total: $12.18** en 96 sesiones
  - orquestador: $11.95
  - workers: $0.2297 en 46 llamadas (1.9%)
```

Y luego lo desglosa por tipo de token — salida, escritura en caché, lectura de
caché — porque cada uno se ataca con una herramienta distinta y un total sin
desglosar no te dice qué hacer a continuación.

El gasto del orquestador sale de la contabilidad de OpenCode, no de una
estimación nuestra. El del worker se calcula con nuestra telemetría, porque las
llamadas al worker son HTTP directo y no crean sesión en OpenCode: **no
aparecen en ninguna factura**. Contar solo lo que OpenCode ve ponía al worker en
el 0,8% cuando la cifra real es el 2%, y el error caía del lado que favorecía
al sistema.

---

## 8. Cuándo esto NO te sirve

Dicho claro, para que no pierdas el tiempo:

- **Si tu repositorio es pequeño.** Si nada de lo que lees llega a 12 KB, no hay
  nada que delegar y el sistema no hará nada. No molesta, pero no aporta.
- **Si lo que te cuesta caro es pensar, no leer.** El orquestador razonando es
  un tercio de la factura y ahí no se toca nada — y está bien así, porque eso
  es exactamente lo que estás pagando.
- **Si no soportas 3–14 segundos de espera** cuando hay una delegación. Ese es
  el precio en latencia, y lo domina el worker *escribiendo* el resumen, no
  leyendo la entrada.
- **Si tu código no puede salir de tu máquina.** Se puede, pero entonces el
  orquestador también tiene que ser local. `allowedProviders` restringe a los
  workers, **no al orquestador**: el modelo del orquestador lo elige OpenCode y
  el sistema no lo controla. `doctor` te avisa si estás en ese estado creyendo
  que estás protegido.

---

## 9. Problemas típicos

**"No noto ninguna diferencia."**
Ejecuta `shunt doctor`. Si dice que hay 0 delegaciones, o está mal montado o no
has pedido nada lo bastante grande. Las dos cosas se ven igual, y `doctor` es lo
único que las distingue.

**"Me ha salido más caro."**
Mira `shunt costs`. Si hay delegaciones fallidas, ahí está: cuando una
delegación falla, el trabajo vuelve al modelo caro en silencio. Y si el worker
está escribiendo resúmenes más largos que el código que resume, `shunt stats`
lo enseña como un ahorro negativo.

**"El worker se equivoca."**
Se equivocará algunas veces. Cada recibo lo dice, y las instrucciones del
orquestador son explícitas en que lo que dice el worker es **una pista que hay
que verificar, no una conclusión**. Se comprueba la sintaxis, se limita dónde
puede escribir, se hace copia de seguridad y se revierte si no compila — pero
nada de eso es una revisión de código.

**"Quiero cambiar de modelo."**
`shunt config` otra vez. Reescribe las instrucciones, la aritmética y las
exenciones para que sigan cuadrando entre sí. Tus `writePaths`, `editPaths` y
tu política de nube sobreviven, y `shunt update` nunca toca `shunt.json`.

**"He tocado shunt.json a mano y ahora no va."**
`shunt doctor` te dice en qué línea. El fallo más común es que el sistema no
carga y no avisa de nada.

---

## 10. Resumen en cinco líneas

1. Instalas con `pipx install opencode-shunt`, y en tu proyecto: `shunt init`,
   `shunt config`, `shunt doctor`.
2. Trabajas igual que antes: `opencode --agent orchestrator`.
3. Cuando algo es voluminoso, va al worker barato solo. Tú no haces nada.
4. `shunt costs` te dice qué has gastado; `shunt doctor`, si está funcionando.
5. Lo que ahorras es la parte voluminosa. Lo que se piensa se sigue pagando, y
   así debe ser.

---

## Para seguir

- [README](README.md) — resumen y cifras medidas
- [Cómo funciona](docs/COMO_FUNCIONA.md) — el sistema completo, con más detalle
- [Resultados reales](docs/RESULTADOS_PRUEBA_REAL.md) — una app construida con
  esto, lo que costó y los cuatro defectos que solo aparecieron con uso real
- [Próximas mejoras](docs/PROXIMAS_MEJORAS.md) — lo que falta, y lo que se
  descartó después de medirlo
