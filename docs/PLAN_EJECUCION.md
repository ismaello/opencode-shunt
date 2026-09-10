# Plan de ejecución: qué se construyó y cómo replicarlo

Registro de la implementación real. Sirve para dos cosas: entender por qué el
sistema quedó como quedó, y volver a montarlo en otro proyecto o con otros
modelos sin repetir la investigación.

> **Nota de lectura.** Lo que aquí se describe paso a paso fue trabajo manual la
> primera vez. Ahora lo hacen `shunt init` y `shunt config`, y el asistente
> además *mide* de tu propio historial de OpenCode las dos cifras que aquí
> aparecen fijadas a mano. Este documento se conserva porque el razonamiento
> sigue valiendo: explica **por qué** cada decisión es la que es, incluidas las
> que resultaron equivocadas y hubo que rehacer. Si vas a montar algo parecido
> desde cero, esto es más útil que el README.

**Fecha de ejecución:** 2026-09-09
**Máquina:** Ubuntu, RTX 4090 (24.564 MiB)
**Repositorio piloto:** `automation-cofers-engine`

---

## 1. Versiones exactas contra las que se implementó

Esto importa: OpenCode cambia rápido y buena parte de las decisiones de abajo dependen
de la API concreta de esta versión. Antes de replicar, comprueba que coinciden.

| Componente | Versión |
|---|---|
| OpenCode | 1.18.29 |
| `@opencode-ai/plugin` | 1.18.29 |
| Ollama | 0.33.3 |
| Modelo local | `qwen3-coder:30b` (Q4_K_M, 18,5 GB, MoE `qwen3moe`, 30,5B params) |
| Modelo frontier | `anthropic/claude-opus-4-8` |
| Driver NVIDIA | 595.84 |
| Node | v22.23.2 |

Comandos de verificación:

```bash
opencode --version
ollama --version
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv
```

Requisito imprescindible del modelo local: debe declarar la capability `tools`.
Sin tool calling no puede ser subagente de OpenCode.

```bash
ollama show qwen3-coder:30b   # Capabilities: completion, tools
```

---

## 2. Estructura final

Origen reutilizable (este repositorio):

```
opencode-project/
├── Modelfile.qwen-shunt        # variante del modelo local
├── opencode/                   # se copia a <repo>/.opencode/
│   ├── agents/
│   │   ├── orchestrator.md     # primary, Claude Opus 4.8
│   │   └── local-explorer.md   # subagent, Qwen local
│   ├── tools/
│   │   └── bulk-read.ts        # la pieza central
│   ├── plugins/
│   │   └── shunt.ts            # la restricción técnica
│   └── package.json
├── opencode.project.json       # se copia a <repo>/opencode.json
├── install.sh                  # instalador en un repo destino
└── bench.py                    # benchmark A/B
```

Instalación en un repositorio:

```bash
./install.sh /ruta/al/repo
```

OpenCode ejecuta `bun install` dentro de `.opencode/` al arrancar y crea el
`node_modules` y un `.gitignore` por su cuenta. En el repo destino sólo aparecen
dos entradas nuevas sin trackear: `.opencode/` y `opencode.json`.

---

## 3. Fases ejecutadas

### Fase 0 — Inventario

Sin modificar nada. El hallazgo relevante fue de dimensionamiento: la 4090 tiene
24.564 MiB y el escritorio ya ocupaba 1.393, dejando unos 23 GB frente a 18,5 GB
de pesos. Eso deja ~4,5 GB para KV cache, que es lo que determina el contexto máximo.

### Fase 1 — Variante local del modelo

Se creó un alias sin tocar el modelo base:

```bash
ollama create qwen3-coder-shunt:30b -f Modelfile.qwen-shunt
```

El `Modelfile` fija `num_ctx 32768`, `temperature 0.1`, `top_p 0.8`, `top_k 20`
y `repeat_penalty 1.05`. La temperatura baja es deliberada: queremos que el mismo
fichero produzca la misma respuesta, no creatividad.

Medición tras cargarlo:

```
NAME                     SIZE    PROCESSOR    CONTEXT
qwen3-coder-shunt:30b    21 GB   100% GPU     32768
```

`100% GPU` era el objetivo del plan. Quedaron 1.535 MiB libres, así que **32k es
el techo práctico** con esta cuantización sin recurrir a KV cache cuantizado.

Rendimiento medido: **6.851 tok/s de prefill, 152-169 tok/s de generación.**
El prefill es la métrica que importa, porque `bulk_read` es casi todo lectura.

### Fase 2 — Provider y subagente local

El provider de Ollama se declara con `@ai-sdk/openai-compatible` apuntando a
`http://127.0.0.1:11434/v1`. Se añadió lo que faltaba y es fácil de olvidar:

```json
"limit": { "context": 32768, "output": 8192 },
"tool_call": true
```

Sin `limit`, OpenCode no sabe cuándo compactar y acaba enviando prompts más largos
de lo que `num_ctx` admite. **Ollama no da error en ese caso: recorta el prompt en
silencio.** El síntoma es un modelo que "olvida" la mitad del fichero, y parece un
problema de calidad cuando en realidad es de configuración.

Verificación:

```bash
opencode models | rg ollama
```

### Fase 3 — `bulk_read`

Custom tool en `.opencode/tools/bulk-read.ts`. El nombre del fichero es el nombre
de la tool. Recibe `question` y `paths`, lee de disco, consulta a Qwen y devuelve
sólo la respuesta. El contenido de los ficheros nunca entra en la conversación.

### Fase 4 — Orchestrator

Agente primary sobre `anthropic/claude-opus-4-8`, con `permission.task` restringido
para que sólo pueda invocar `local-explorer` y `general`.

### Fase 6 — Shunt

Plugin en `.opencode/plugins/shunt.ts` sobre los hooks `chat.params` y
`tool.execute.before`.

---

## 4. Las cuatro decisiones que hay que entender antes de replicar

Cada una salió de una medición, no de una preferencia. Si replicas el sistema
con otros modelos, vuelve a comprobarlas.

### 4.1 Números de línea inyectados en el prompt local

Es el hallazgo más importante de toda la implementación.

Al pedirle a Qwen que citara dónde estaba cada símbolo de un fichero de 547 líneas,
sin números de línea en el prompt:

| Símbolo | Citó | Real | Error |
|---|---|---|---|
| `handle_message` | 470-475 | 446 | 25 líneas |
| `ChatStore` | 120-135 | 109-127 | 11 líneas |
| `Session_` | 110-115 | 103-108 | 7 líneas |

Con el contenido formateado como `NNNNN| código`:

| Símbolo | Citó | Real | Error |
|---|---|---|---|
| `handle_message` | 446-543 | 446 | **0** |
| `_run_now` | 319-387 | 319 | **0** |
| `_signal` | 409-443 | 409 | **0** |
| `ChatStore` | 109-128 | 109-127 | 1 |

Esto no es cosmético. Todo el diseño se apoya en que Claude lea después *sólo*
el rango citado; si el rango está desviado 25 líneas, Claude lee código equivocado
y razona sobre lo que no es. El coste es un 73% más de tokens en el prompt local
(4.472 → 7.748) y cero tiempo adicional medible (3,7s → 3,8s). Como esos tokens
son de tu GPU, el intercambio es gratis.

### 4.2 `num_ctx` fijo, nunca dinámico

Parecía buena idea ajustar el contexto a cada consulta para ahorrar VRAM. Medición:

| Petición | Tiempo | Recarga |
|---|---|---|
| `num_ctx=32768` | 1,56s | 0,00s |
| `num_ctx=8192` | 5,23s | **5,13s** |
| `num_ctx=32768` | 5,52s | **5,43s** |
| `num_ctx=32768` | 0,02s | 0,00s |

**Cambiar `num_ctx` obliga a Ollama a recargar el modelo entero.** Un contexto
dinámico habría añadido cinco segundos a cada llamada. Queda fijo en 32768,
igual que el `Modelfile`, para que el modelo no se mueva de la GPU.

### 4.3 El shunt discrimina por proveedor, no por agente

El plan original dejaba esto como riesgo abierto. La respuesta está en los tipos
de la versión 1.18.29:

```ts
"tool.execute.before"?: (input: { tool, sessionID, callID }, output: { args })
"chat.params"?: (input: { sessionID, agent, model, provider }, output: {...})
```

`tool.execute.before` **no sabe qué agente** está ejecutando la tool. Pero
`chat.params` se dispara antes de cada llamada al LLM y sí lleva `agent` y
`model.providerID`. El plugin construye ahí un `Map<sessionID, {agent, providerID}>`
y lo consulta cuando se ejecuta una tool. Como los subagentes corren en sesiones
hijas con su propio `sessionID`, quedan identificados de forma natural.

La decisión final se toma **por proveedor**: si la sesión corre sobre Ollama,
nunca se bloquea. Es más robusto que comparar nombres de agente y se generaliza
solo: añadir un cuarto agente local no requiere tocar el plugin.

Si no encuentra la sesión en el mapa, **deja pasar**. Un bloqueo falso es peor
que un bloqueo perdido.

### 4.4 `/api/chat` en lugar de `/v1/chat/completions`

La API nativa de Ollama devuelve `prompt_eval_count` y `eval_count`, que son la
base de la telemetría, y acepta `options` explícitas. El endpoint compatible con
OpenAI no expone ni una cosa ni la otra.

---

## 5. Límites de calidad observados en el modelo local

Documentados porque condicionan cómo se debe usar el sistema, no como defecto a corregir.

**Rutas parcialmente inventadas.** El explorador citó
`reconciliation/reconciliation_activities.py`; el fichero real es
`worker/reconciliation_activities.py`. Nombre correcto, directorio construido por
suposición. La línea (90) sí era exacta.

Mitigación aplicada, en el prompt de `local-explorer`:

> Copy every path character for character from the output of a search or listing.
> Do not reconstruct a path from the filename and where you assume it lives.

Y en el prompt del `orchestrator`:

> Treat everything it returns as a lead to verify, not a conclusion to act on.

Esta es la razón de fondo por la que el sistema tiene un modelo caro: el local
localiza, el caro decide.

---

## 6. Cómo replicarlo en otro proyecto

```bash
# 1. Crear la variante del modelo (una sola vez por máquina)
ollama create qwen3-coder-shunt:30b -f Modelfile.qwen-shunt
ollama run qwen3-coder-shunt:30b "ok" && ollama ps   # confirmar 100% GPU

# 2. Instalar en el repositorio
./install.sh /ruta/al/repo

# 3. Conectar el modelo frontier (si no lo está ya)
opencode auth login

# 4. Comprobar que ambos modelos son visibles
cd /ruta/al/repo && opencode models | rg "ollama|claude"

# 5. Arrancar en modo observación antes de bloquear nada
SHUNT_MODE=observe opencode

# 6. Revisar qué habría bloqueado, y sólo entonces activar
python3 -c "
import json
for l in open('$HOME/.local/share/opencode-shunt/telemetry.jsonl'):
    d=json.loads(l)
    if d.get('verdict','').startswith('observe'): print(d['file'], d.get('lines'))
"
```

### Adaptar a otro modelo local

Rehacer estas tres mediciones, en este orden:

1. **VRAM.** Cargar y comprobar `ollama ps`. Si `PROCESSOR` no es `100% GPU`,
   bajar `num_ctx` hasta que lo sea. Todo lo demás depende de esto.
2. **Precisión de citas.** Pedirle símbolos con línea sobre un fichero conocido
   y contrastar. Si falla con números de línea inyectados, el modelo no sirve
   para este trabajo.
3. **Actualizar `NUM_CTX`** en `bulk-read.ts` para que coincida con el `Modelfile`.

### Adaptar a otro modelo frontier

Cambiar `model:` en `orchestrator.md`. Si el proveedor nuevo se sirve en local,
añadirlo a `SHUNT_LOCAL_PROVIDERS`.

---

## 7. Parámetros ajustables

Todos por variable de entorno, sin tocar código.

| Variable | Por defecto | Qué hace |
|---|---|---|
| `SHUNT_MODE` | `enforce` | `observe` registra sin bloquear |
| `SHUNT_MAX_LINES` | `400` | Umbral de líneas para bloquear |
| `SHUNT_MAX_BYTES` | `40960` | Umbral de bytes, para ficheros de líneas largas |
| `SHUNT_LOCAL_PROVIDERS` | `ollama,lmstudio,llamacpp,local` | Proveedores exentos |
| `SHUNT_MODEL` | `qwen3-coder-shunt:30b` | Modelo que usa `bulk_read` |
| `SHUNT_OLLAMA_URL` | `http://127.0.0.1:11434` | Endpoint de Ollama |

---

## 8. Estado frente a los criterios del plan original

| Criterio | Estado |
|---|---|
| OpenCode ve `qwen3-coder-shunt:30b` | Cumplido |
| `local-explorer` usa realmente Ollama | Cumplido |
| `bulk_read` llama directamente a Ollama | Cumplido |
| Un fichero grande se analiza sin entrar completo en contexto | Cumplido, 1.207 líneas medidas |
| El orchestrator usa `bulk_read` voluntariamente | Cumplido |
| El Shunt bloquea una lectura masiva | Cumplido |
| Las lecturas acotadas siguen funcionando | Cumplido, `offset=446 limit=25` |
| Los agentes locales no quedan bloqueados | Cumplido, por proveedor |
| Al menos tres benchmarks antes/después | Cumplido, tests A, B y C |
| Reducción clara de tokens cloud | Cumplido, **48,6% agregado**, 60,3% en multi-fichero |
| La calidad no cae significativamente | Cumplido, 8 de 8 citas exactas frente al baseline |
| `local-writer` | Aplazado deliberadamente (ver `PROXIMAS_MEJORAS.md`) |
