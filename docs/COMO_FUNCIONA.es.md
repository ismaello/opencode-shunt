English: [HOW_IT_WORKS.md](HOW_IT_WORKS.md)

# Cómo funciona el sistema

Guía de uso y funcionamiento. Explica qué hace el sistema, por qué, qué reglas
aplica, y cómo comprobar que realmente ahorra.

> **Instalación.** Todo lo que sigue se instala con tres órdenes:
> `shunt init` deja el runtime en `.opencode/`, `shunt config` decide quién
> orquesta, quién lee y quién escribe, y `shunt doctor` comprueba que funciona
> de verdad. Ese último no es una formalidad: **este sistema falla siempre en
> silencio y en la dirección caro**, así que una instalación rota y una sin usar
> tienen exactamente el mismo aspecto. El README lo resume; este documento
> explica el por qué.

---

## 1. El problema que resuelve

Cuando le pides a Claude que arregle algo en un repositorio, lo primero que hace
es leer código. Mucho. Y cada línea que lee ocupa sitio en su ventana de contexto
y se te factura.

El problema no es sólo el dinero. Es que **el contexto es un recurso finito y se
degrada**. Un modelo que ha absorbido 400.000 líneas para encontrar una función
razona peor sobre el bug que tiene delante que uno que llegó a esa función
directamente. Estás pagando un precio alto por un trabajo que no requiere
inteligencia: buscar, filtrar y resumir.

Mientras tanto tienes una RTX 4090 parada, capaz de leer código a 6.851 tokens por
segundo sin cobrarte nada.

**La idea es sencilla: que la GPU lea y que Claude piense.**

---

## 2. La idea en una imagen

```
                    TÚ
                    │
                    ▼
            ┌───────────────┐
            │  ORCHESTRATOR │   Claude Opus 4.8
            │               │   Piensa, decide, revisa
            └───────┬───────┘
                    │
       ┌────────────┼────────────┐
       │            │            │
       ▼            ▼            ▼
  bulk_read   explorer   shunt
  (tool)      (subagente)      (guardia)
       │            │            │
       └─────┬──────┘            │
             ▼                   │
        Qwen3-Coder ◄────────────┘
         RTX 4090            impide que Claude
        (gratis)             lea a lo bruto
```

Reparto del trabajo:

| Trabajo | Quién |
|---|---|
| Leer ficheros grandes | Qwen local |
| Buscar por el repositorio | Qwen local |
| Resumir, localizar, mapear | Qwen local |
| Arquitectura y diseño | Claude |
| Bugs sutiles, concurrencia, seguridad | Claude |
| Decidir qué cambiar | Claude |
| Revisión final | Claude |

---

## 3. Las tres piezas

### `bulk_read` — la pieza central

Es una herramienta, no un agente. Claude la llama así:

```
bulk_read(
  question = "¿Dónde se deduplican las transacciones y qué puede fallar?",
  paths    = ["src/matching.py", "src/bq.py", "src/workflows.py"]
)
```

Lo que pasa por dentro:

```
Claude pregunta
     │
     ▼
bulk-read.ts  ── lee los ficheros DE DISCO (no pasan por Claude)
     │
     ├─ rechaza secretos (.env, *.pem, credentials*)
     ├─ rechaza binarios
     ├─ comprueba que están dentro del repositorio
     ├─ añade números de línea a cada línea
     └─ trocea si no cabe en 32k
     │
     ▼
Qwen en la 4090  ── analiza y responde
     │
     ▼
Claude recibe SÓLO el resumen
```

La clave está en la primera flecha. `bulk-read.ts` lee del disco directamente,
así que **el contenido de los ficheros nunca pasa por la conversación con Claude**.
No es que Claude lo lea y lo olvide: es que nunca llega.

Qwen devuelve, para cada hallazgo: fichero, símbolo, rango de líneas exacto,
evidencia en una frase, y nivel de confianza. Claude usa ese mapa para leer
después sólo las 20 líneas que de verdad necesita.

### `delegate_write` y `delegate_edit` — la otra mitad de la factura

Todo lo anterior recorta lo que Claude **ingiere**. Falta lo que Claude **emite**,
que es la mitad más cara: la salida de Opus va a 25 $/M frente a 6,25 $/M de lo
que entra en contexto. Medido sobre sesiones reales nuestras, la salida es el
**34% de la factura**, y el shunt de lectura no la toca en absoluto.

**`delegate_write`** crea un fichero sin que Claude escriba el cuerpo. Le pasas el
código que se va a probar y un test parecido como referencias, y el worker copia
las convenciones del repositorio. Claude recibe solo un recibo: líneas, símbolos
definidos y si compila.

**`delegate_edit`** aplica el mismo cambio mecánico en muchos sitios. Aquí hay un
detalle que cambia todo el diseño y conviene entender: el `edit` de OpenCode
funciona por búsqueda y reemplazo, así que **cambiar una línea ya cuesta solo esa
línea**, y delegarlo no ahorraría nada. Lo que cuesta dinero es la repetición,
porque cada sitio necesita su contexto alrededor emitido **dos veces**, como texto
a buscar y texto de reemplazo. La misma lección que en lectura: volumen, no tamaño.

Nuestros tests lo cuantifican: diez ediciones dispersas cuestan 1.190 caracteres
frente a 290 si van juntas, cuatro veces más.

La decisión que hace esto seguro: **el worker devuelve el fichero completo y el
diff lo calculamos nosotros**. Así no puede describir mal su propia edición, y una
reescritura disfrazada de retoque salta a la vista como un diff que toca medio
fichero, que se rechaza. Las guardas, por orden de peligro:

| Guarda | Qué evita |
|---|---|
| Allowlist (`editPaths`) | Que un worker toque lógica de negocio |
| Detección de abreviación | **Borrar código**: los modelos devuelven "resto sin cambios" y eso se escribiría a disco |
| Fracción de cambio máxima | Reescrituras y reformateos colados junto a un cambio pequeño |
| Comprobación de sintaxis | Deja el fichero roto; si no compila **se revierte solo** |
| Copia de seguridad | Que cualquier escritura sea definitiva |

La de la abreviación es la que más importa, porque su fallo es silencioso: el
fichero recortado compila igual y los tests que quedan siguen pasando.

Medido en un caso real (docstrings en 21 funciones de un fichero de 308 líneas):
**78% menos tokens de salida**, 0,055 $ ahorrados en una sola operación, los 21
tests pasando y el fichero idéntico al original salvo las docstrings.

### `explorer` — el explorador

Un subagente que corre en Qwen. Se usa cuando **todavía no sabes qué ficheros
importan**:

> "Encuentra dónde se calculan los checksums de transacciones."

Busca con `grep` y `glob`, lee lo que haga falta, sigue las referencias, y
devuelve un mapa: rutas, símbolos, rangos, relaciones. No puede editar nada.
Corre en su propia sesión, así que todo lo que lee se queda en su sesión y no
contamina la de Claude.

### `shunt` — el guardia

Un plugin que vigila cada lectura. Si Claude intenta leer un fichero entero de
más de 400 líneas, lo bloquea y le devuelve este mensaje:

```
SHUNT: refusing to read src/cofers_engine/chat/__init__.py in full (548 lines).
This would burn frontier context on bulk reading. Instead:
  - bulk_read(question, paths) ...
  - @explorer ...
  - read with offset/limit ...
```

Existe porque **pedirle a un modelo que se porte bien no funciona**. Un prompt que
dice "por favor no leas ficheros grandes" se cumple las tres primeras veces y luego
se olvida. El shunt lo convierte en algo que no se puede olvidar.

Lo bonito es lo que pasa después del bloqueo. En la prueba real, Claude leyó el
mensaje de error y **cambió por su cuenta a `bulk_read`**, sin intervención. El
bloqueo no es un muro, es una señal de tráfico.

---

## 4. Las reglas exactas

### Cuándo bloquea

| Situación | Resultado |
|---|---|
| Leer fichero de más de 400 líneas | **Bloqueado** |
| Leer fichero de más de 40 KB | **Bloqueado** |
| `cat` / `less` / `more` sobre un fichero grande | **Bloqueado** |
| `head -n 5000 fichero` | **Bloqueado** |
| Leer con `offset` y `limit` de 400 líneas o menos | Permitido |
| Leer un fichero pequeño | Permitido |
| `head -n 20 fichero` | Permitido |
| **Cualquier cosa que haga un agente local** | **Siempre permitido** |

Esa última fila es la más importante del sistema. El shunt existe para proteger el
contexto caro, no para impedir que Qwen lea. Si bloqueara a los agentes locales,
todo el diseño se vendría abajo.

### Cuándo se niega a delegar

Delegar no es gratis, así que hay un suelo por debajo del cual `bulk_read` rechaza
el trabajo y te dice que leas tú. Ese suelo **no es un número fijo, se calcula**
con los precios, en `opencode/lib/economics.ts`.

La cuenta tiene dos lados. A favor: lo que no entra en el contexto se ahorra a
precio de escritura de caché (6,25 $/M en Opus) y además se ahorra en cada turno
posterior, porque todo lo que está en el contexto se reenvía una y otra vez a
precio de lectura de caché. En contra: delegar añade viajes de ida y vuelta, y
cada uno reenvía la conversación entera.

Ese segundo término es la razón por la que delegar un fichero pequeño **pierde
dinero**, algo que primero vimos como una rareza del benchmark. Con los valores
medidos en sesiones reales, el cruce cae en **unos 13 KB**, casi donde estaba el
umbral de 400 líneas que habíamos elegido solo con datos. Que las dos vías
coincidan es la mejor señal de que ninguna está muy equivocada.

Cuando rechaza, te enseña la cuenta:

```
bulk_read declined: these files total 163 lines / 5.1 KB, below the 12.6 KB at
which delegating starts to pay for itself.
...delegating this would cost about $0.013 more than reading it.
```

Se ajusta por repositorio en `.opencode/shunt.json`. Los dos valores que de verdad
mueven el resultado describen **cómo trabajas**, no cuánto cuestan las cosas:

```json
"economics": {
  "assumedConversationTokens": 25000,
  "remainingTurns": 3
}
```

Si tus conversaciones son largas, sube el primero: los viajes extra se encarecen
y el suelo sube. Si lo que lees sobrevive muchos turnos, sube el segundo: delegar
compensa antes. Una curiosidad útil del modelo es que **la escala de precios no
mueve el umbral**, solo la proporción entre escritura y lectura de caché; un
modelo más barato ahorra menos dinero en todos los tamaños, pero el punto de
cruce es el mismo.

### Cómo distingue quién es quién

Mira **qué modelo exacto sirve la sesión**, y lo compara con la lista
`bulkExempt` de `shunt.json`. Si la sesión corre sobre un modelo exento —el
explorador, o cualquier cosa local— no se bloquea nunca. Si corre sobre el
orquestador, se aplican las reglas.

Esto empezó siendo por proveedor, y era un fallo serio. En cuanto un mismo
proveedor ocupa dos roles —Gemini Pro de jefe con Gemini Flash de lector, que es
una configuración de lo más normal— eximir al proveedor eximía también al jefe, y
el sistema se apagaba entero sin decir nada: cero bloqueos, cero delegaciones,
factura completa y ninguna señal de que algo iba mal. Por eso ahora la clave es
el modelo, y por eso `shunt doctor` falla en voz alta si el orquestador acaba en
esa lista.

Y si no logra identificar la sesión, **deja pasar**. Bloquear por error es peor
que dejar pasar por error.

### Qué no lee nunca `bulk_read`

Ficheros que parezcan un secreto (`.env`, `.env.*`, `*.pem`, `*.key`,
`credentials*`, `service-account*`, `secrets*`, claves SSH), binarios (por
extensión y detectando bytes nulos), y cualquier ruta que se salga del
repositorio, incluidos symlinks que apunten fuera. Máximo 20 ficheros por llamada.

### Qué se registra

En `~/.local/share/opencode-shunt/telemetry.jsonl`, una línea JSON por operación:
qué herramienta, qué agente, qué proveedor, qué fichero, cuántas líneas y bytes,
cuántos tokens locales, cuánto tardó, y si el shunt bloqueó o no.

**No se guarda nunca**: el contenido del código, los prompts completos, ni ninguna
credencial.

---

## 5. Cómo se usa en el día a día

No cambia nada. Abres OpenCode y escribes lo que quieras:

> Tenemos sugerencias duplicadas en la reconciliación. Averigua de dónde salen.

Lo que ocurre por dentro:

```
1. El orquestador no sabe dónde está el código
   └─► @explorer  ────────► el lector busca por el repo
                            devuelve 4 ficheros y sus líneas

2. El orquestador necesita entender esos ficheros
   └─► bulk_read(...)  ───► el lector lee 1.200 líneas
                            devuelve un resumen con rangos exactos

3. El orquestador lee sólo las líneas 107-161 de matching.py
   (lectura acotada: el shunt la deja pasar)

4. El orquestador razona, encuentra la causa, propone el arreglo

5. El cambio mecánico va a delegate_edit; vuelve como diff y lo revisa
```

Tú sólo ves el resultado. Y en la interfaz, unas líneas indicando cuándo se ha
delegado trabajo.

### Los comandos que conviene conocer

```bash
shunt doctor     # ¿está bien montado? Ejecútalo cuando algo huela raro
shunt costs      # escribe shunt-costs.md: qué ha costado este repo y en qué
shunt stats      # qué ahorró cada delegación, operación por operación
shunt config     # cambiar quién orquesta, quién lee y quién escribe
```

`shunt costs` es el que contesta a "¿cuánto me está costando esto?". Escribe un
fichero Markdown del repositorio donde lo lances, y parte la factura en salida,
cache write y cache read, porque cada una la ataca una herramienta distinta y un
total sin desglosar no te dice qué hacer a continuación. El gasto sale de la
contabilidad de OpenCode; lo evitado es estimación y lo dice donde aparece.

También avisa de las delegaciones que fallaron, que importan más de lo que
parece: cuando una delegación falla, el trabajo vuelve al modelo caro sin
avisarte, y la factura sube sin que nada parezca roto.

Y dos variables de entorno para casos puntuales:

```bash
SHUNT_MODE=observe opencode      # no bloquea, pero sigue registrando
SHUNT_MAX_LINES=800 opencode     # subir el umbral si te resulta agresivo
```

### Cambiar de orquestador

`shunt config` otra vez. No hay que editar el modelo a mano en ningún sitio: el
asistente reescribe los prompts de los agentes, el bloque del proveedor en
`opencode.json`, la lista de exenciones y la economía de delegación **a la vez**,
y desincronizar cualquiera de esos cuatro es exactamente cómo este sistema se
rompe en silencio. Tus `writePaths`, `editPaths` y política de nube sobreviven.

Lo que necesita cada uno:

| Orquestador | Credenciales |
|---|---|
| `anthropic/claude-opus-4-8` | `ANTHROPIC_API_KEY`, o `opencode auth login` |
| `google-vertex/gemini-2.5-pro` | `gcloud auth application-default login`, y exportar `GOOGLE_CLOUD_PROJECT` y `VERTEX_LOCATION` |
| `openai/gpt-5.2` | `OPENAI_API_KEY` |
| `deepseek/deepseek-reasoner` | `DEEPSEEK_API_KEY` |

**El suelo de delegación se mueve con el jefe, y así debe ser.** Delegar existe
para proteger un contexto caro; con un jefe barato hay menos que proteger, así
que el umbral a partir del cual `bulk_read` se niega sube solo. Depende de la
relación entre lo que cobra el proveedor por meter un token en caché y por
reenviarlo: Anthropic descuenta un token cacheado un 90%, Google un 75%. Por eso
el mismo repositorio delega a partir de 12,6 KB con Opus y a partir de 23,4 KB
con Gemini Pro. No hay nada que tocar, lo calcula `shunt config` con el precio
del modelo que elijas.

---

## 6. Benchmark: comprobar que esto ahorra de verdad

Lo peor que puede pasar es creerse el ahorro sin medirlo. Por eso hay un script
que ejecuta la misma pregunta dos veces y compara.

### Cómo funciona la medición

OpenCode expone el consumo real con `--format json`, en eventos `step_finish`:

```json
{
  "type": "step_finish",
  "part": {
    "tokens": { "input": 2, "output": 4, "cache": { "write": 10907, "read": 0 } },
    "cost": 0.06827875
  }
}
```

El script suma `input + cache.write + cache.read` de todos los pasos. A esa suma
la llamamos **tokens ingeridos**: todo lo que el modelo tuvo que tragarse,
independientemente de cómo se facture. Es la métrica honesta, porque un fichero
leído se contabiliza en `cache.write` la primera vez y en `cache.read` después,
y si sólo mirases `input` parecería que no cuesta nada.

### Ejecutarlo

```bash
./bench.py --repo /path/to/your-repo \
           --name test-b \
           "Traza el flujo de reconciliación desde la entrada del workflow hasta las sugerencias persistidas."
```

Lanza la misma pregunta dos veces:

- **baseline**: `SHUNT_MODE=observe`, Claude lee los ficheros él mismo.
- **shunt**: `SHUNT_MODE=enforce`, Claude está obligado a delegar.

Y luego:

```bash
./bench.py --report
```

### Resultados reales

Ejecutado sobre `automation-cofers-engine` con Claude Opus 4.8 en ambas ramas:

```
name                   arm         ingested   output   cost $  wall s  tools
test-a-fichero-grande  baseline      105407     4014   0.2283    66.9  bash,grep,read
test-a-fichero-grande  shunt          59909     3272   0.2071    73.0  bulk-read,read
test-b-multifichero    baseline      230217     5971   0.4746    82.8  bash,grep,read
test-b-multifichero    shunt          91310     4314   0.2345    89.1  bulk-read,read,task
test-c-exploracion     baseline       42987     2746   0.1443    44.0  grep,read
test-c-exploracion     shunt          43218     2085   0.0975    50.1  bulk-read,read,task

benchmark                tokens ahorrados  ahorro %  ahorro $
test-a-fichero-grande              45,498     43.2%    0.0212
test-b-multifichero               138,907     60.3%    0.2401
test-c-exploracion                   -231     -0.5%    0.0468
TOTAL                             184,174     48.6%
```

**Cómo leer esto, sin autoengaños:**

El **Test B es el caso para el que se diseñó el sistema**: varios ficheros
relacionados, flujo que hay que trazar. Ahí ahorra un 60% de tokens y la mitad
del dinero. Es el resultado que importa.

El **Test C no ahorró nada** (-0,5%), y conviene entender por qué en lugar de
esconderlo: el algoritmo estaba en `matching.py`, un fichero de 162 líneas. Por
debajo del umbral de 400, así que el shunt nunca llegó a bloquear y Claude lo leyó
directamente. El coste de delegar se comió el ahorro. **En ficheros pequeños este
sistema no sirve de nada**, y eso es correcto: no debería activarse ahí.

El **ahorro en dinero es siempre menor que el ahorro en tokens** (43% de tokens
pero sólo 9% de coste en el Test A). La razón es que los tokens de salida cuestan
mucho más que los de entrada, y el shunt no reduce la salida. Si sólo miras la
factura, subestimas el beneficio: lo que más ganas es **espacio de contexto**, que
es lo que determina si el modelo sigue razonando bien en la hora tres de una sesión.

El sistema es **entre 6 y 7 segundos más lento** por consulta. Ese es el precio.

### Y lo más importante: la calidad no cayó

Las cifras de ahorro no valen nada si la respuesta empeora. Contrastando las ocho
referencias que dio la rama con shunt en el Test B contra el código real:

| Símbolo | Citó | Real |
|---|---|---|
| `match_one_to_one` | `matching.py:107-162` | 107 |
| `fetch_open_invoices` | `reconciliation_activities.py:45-64` | 45 |
| `fetch_open_transactions` | `reconciliation_activities.py:67-86` | 67 |
| `match_invoices_to_transactions` | `reconciliation_activities.py:89-106` | 89 |
| `RECONCILIATION_ACTIVITIES` | `:119-124` | 119 |
| `ReconciliationSuggestWorkflow` | `workflows.py:242-331` | 242 |
| `bq.fetch_open_invoices` | `bq.py:65-114` | 65 |
| `bq.fetch_open_transactions` | `bq.py:117-168` | 117 |

**Las ocho exactas**, incluyendo los decoradores. Y la respuesta con shunt fue
*más* detallada en la capa de BigQuery que la del baseline.

### Qué preguntas usar

Las del plan original, que están pensadas para cubrir casos distintos:

| Test | Pregunta | Qué mide |
|---|---|---|
| A | "¿Cómo se gestionan los errores y qué caminos producen retry?" sobre un fichero grande | Ahorro en fichero único |
| B | "Traza el flujo completo desde la entrada hasta la persistencia" | Ahorro multi-fichero |
| C | "Encuentra dónde se calcula X y explícame el flujo" (sin decir dónde está) | Que se active el explorador |
| E | Un bug real que exija razonar | **Que la calidad no caiga** |

El test E es el más importante de todos, y el que no se puede automatizar. Los
otros miden tokens; éste mide si el sistema sigue sirviendo. **Ahorrar un 80% de
tokens a cambio de no encontrar el bug es un fracaso, no un éxito.**

### Lo que ya está medido

Del funcionamiento local, con datos reales de este repositorio:

| Medición | Valor |
|---|---|
| Prefill de Qwen | 6.851 tok/s |
| Generación de Qwen | 152-169 tok/s |
| VRAM con 32k de contexto | 21 GB de 24,5, **100% GPU** |
| `bulk_read` sobre 5 ficheros | 1.207 líneas, 40 KB, **7,6 segundos** |
| Tokens procesados en local en esa llamada | **16.077** |
| Tokens devueltos a Claude | **636** |

Esa última pareja es el sistema entero resumido: **16.077 tokens de código
procesados, 636 entregados**. Alrededor de un 96% del volumen se quedó en la GPU.

### Una advertencia al leer los números

Una llamada trivial a Opus 4.8 ya consume 10.907 tokens sólo por el prompt de
sistema de OpenCode. Ese suelo es fijo y aparece en las dos ramas del benchmark,
así que en preguntas pequeñas diluye el porcentaje de ahorro y hace que el sistema
parezca peor de lo que es. **Mide el ahorro en tokens absolutos, no sólo en
porcentaje**, y usa preguntas que impliquen leer de verdad.

Y ten presente el tamaño de tu repositorio. `automation-cofers-engine` tiene 6.453
líneas en 62 ficheros Python, con un máximo de 547 líneas por fichero. Claude
podría leérselo entero en unos 80.000 tokens. El ahorro real de este sistema crece
con el tamaño del repositorio, así que las cifras que salgan aquí serán
conservadoras.

---

## 7. Cuando algo va mal

El sistema está diseñado para **degradarse, no para romperse**. Nunca debería
dejarte peor que sin él.

| Problema | Qué pasa |
|---|---|
| Qwen no responde | `bulk_read` devuelve un error corto y le dice a Claude que lea los ficheros él |
| Qwen tarda demasiado | Timeout a los 5 minutos, mismo comportamiento |
| El shunt bloquea algo que no debía | Lee con `offset`/`limit`, o arranca con `SHUNT_MODE=observe` |
| Qwen responde una tontería | Claude está instruido para verificar antes de actuar |
| Ollama está apagado | Sólo falla `bulk_read`; el resto de la sesión sigue |

### Las dos cosas que hay que vigilar

**Rutas inventadas.** El modelo local acierta el nombre del fichero pero a veces
se inventa el directorio. En las pruebas citó `reconciliation/reconciliation_activities.py`
cuando el fichero estaba en `worker/`. Por eso el prompt de Claude insiste en
verificar antes de actuar sobre lo que diga el modelo local.

**Modelo descargado de la GPU.** Ollama descarga el modelo tras 5 minutos sin uso,
y la siguiente llamada paga unos 5 segundos de recarga. Si te molesta:

```bash
sudo systemctl edit ollama
# añadir:  Environment="OLLAMA_KEEP_ALIVE=30m"
sudo systemctl restart ollama
```

---

## 8. Lo que el sistema NO hace

Conviene tenerlo claro para no esperar lo que no es:

- **No sustituye a Claude.** El objetivo no es que Qwen haga el trabajo, sino que
  Claude deje de gastar inteligencia en tareas que no la requieren.
- **No escribe código.** Se decidió dejar `local-writer` fuera del MVP a propósito.
- **No resume logs ni salidas de tests** todavía. Es la siguiente mejora prevista.
- **No ahorra en conversaciones cortas.** Si preguntas algo que no requiere leer
  código, no hay nada que ahorrar.
- **No es mágico.** En un repositorio pequeño el ahorro es modesto. Brilla en
  repositorios grandes.
