# Plan de mejoras V2 — OpenCode + Shunt + Qwen local

**Proyecto base:** OpenCode + Claude Opus 4.8 + Ollama + `qwen3-coder:30b` + RTX 4090  
**Fecha:** 2026-09-09  
**Estado actual:** MVP validado con benchmarks reales  
**Objetivo de esta V2:** mejorar fiabilidad, latencia y ahorro de contexto sin degradar calidad ni añadir infraestructura innecesaria.

---

# 1. Punto de partida

La V1 ya ha demostrado que la arquitectura funciona.

Resultados medidos en `automation-cofers-engine`:

- **48,6% menos tokens ingeridos** en agregado.
- **60,3% menos tokens** en el caso multi-fichero.
- En una llamada real a `bulk_read`, **16.077 tokens locales** se redujeron a **636 tokens entregados a Claude**.
- **8/8 referencias de código verificadas correctamente** en el benchmark principal.
- Qwen funciona a **100% GPU** con contexto de **32k**.
- Coste de la arquitectura: aproximadamente **6-7 segundos adicionales** en las consultas que delegan trabajo local.

La V1 ya resuelve correctamente:

```text
código grande
     ↓
bulk_read / local-explorer
     ↓
Qwen local
     ↓
resumen preciso
     ↓
Claude razona
```

La V2 debe centrarse en los problemas que siguen abiertos:

```text
1. rutas inventadas por el modelo local
2. trabajo local repetido innecesariamente
3. outputs gigantes que todavía entran en Claude
4. búsquedas exactas que no deberían usar ningún LLM
5. decidir si compensa local-writer
6. validar el sistema durante uso real antes de globalizarlo
```

---

# 2. Principios que no deben cambiar

## 2.1 Claude sigue siendo el cerebro

Qwen no debe sustituir al modelo frontier.

```text
Qwen:
- busca
- lee
- filtra
- resume
- localiza

Claude:
- razona
- diseña
- decide
- revisa
- corrige
```

Especialmente en:

```text
concurrencia
seguridad
migraciones delicadas
arquitectura
bugs ambiguos
SQL crítico
consistencia de datos
```

---

## 2.2 Mantener 32k mientras no haya evidencia para cambiarlo

Configuración actual:

```text
qwen3-coder-shunt:30b
num_ctx = 32768
100% GPU
~21 GB VRAM usados
```

No cambiar a 64k únicamente porque sea técnicamente posible.

32k + chunking tiene actualmente ventajas claras:

- modelo completamente en GPU;
- latencia conocida;
- precisión ya validada;
- menor riesgo de degradación en contextos largos;
- menos complejidad.

Cualquier cambio de contexto debe superar un benchmark de precisión y latencia.

---

## 2.3 Mantener números de línea inyectados

Formato:

```text
00107| def match_one_to_one(...):
00108|     ...
```

Debe considerarse parte de la interfaz entre Qwen y Claude.

No eliminarlo.

---

## 2.4 Shunt debe seguir discriminando por proveedor

Regla:

```text
provider local → no bloquear
provider frontier → aplicar Shunt
```

Mantener comportamiento fail-open:

```text
si no sabemos quién ejecuta la tool
        ↓
permitir
```

Un falso negativo cuesta tokens.

Un falso positivo puede romper una sesión.

---

# 3. Prioridad P0 — Verificación determinista de rutas

## Problema

El modelo local puede acertar:

```text
filename
symbol
line number
```

pero inventar parcialmente el path.

Ejemplo observado:

```text
reconciliation/reconciliation_activities.py
```

cuando realmente era:

```text
worker/reconciliation_activities.py
```

Esto no debe depender de un prompt.

## Solución

Añadir un postprocesador en `bulk-read.ts` y, si es práctico, compartirlo con `local-explorer`.

Pipeline:

```text
Qwen responde
      │
      ▼
extraer rutas candidatas
      │
      ▼
normalizar
      │
      ▼
¿existe exactamente?
   │           │
  sí           no
   │           │
 VERIFIED      ▼
         buscar basename
               │
        ┌──────┴──────┐
        │             │
      1 match       >1 match
        │             │
     CORRECTED      AMBIGUOUS
```

Estados posibles:

```text
VERIFIED
CORRECTED
AMBIGUOUS
NOT_FOUND
```

Ejemplo:

```text
PATH CHECK
- worker/reconciliation_activities.py    VERIFIED
- reconciliation/reconciliation_activities.py
  -> worker/reconciliation_activities.py CORRECTED
- utils/helpers.py                       AMBIGUOUS
```

Nunca corregir silenciosamente una ruta ambigua.

### Implementación recomendada

Funciones pequeñas:

```text
extractCandidatePaths(text)
resolvePath(candidate, worktree)
findByBasename(basename, worktree)
annotatePathStatus(result)
```

Preferir APIs del filesystem de Node/Bun antes que shell.

### Criterio de aceptación

Crear un test con:

```text
5 rutas correctas
3 rutas parcialmente incorrectas
2 rutas ambiguas
2 rutas inexistentes
```

Resultado esperado:

```text
100% detectar las correctas
100% corregir las de basename único
0% inventar correcciones ambiguas
```

**Impacto:** muy alto.  
**Esfuerzo:** bajo.

---

# 4. Prioridad P0 — Caché de `bulk_read`

## Problema

La misma combinación:

```text
pregunta
+
archivos
```

puede ejecutarse varias veces durante una sesión.

Actualmente:

```text
Claude pregunta
   ↓
Qwen procesa ~16k tokens
   ↓
~7,6 s

Claude repite
   ↓
Qwen vuelve a procesar ~16k
   ↓
otros ~7,6 s
```

## Clave de caché

Calcular:

```text
SHA256(
    prompt_version
    model
    question
    canonical_paths
    file_size
    file_mtime_ns
)
```

Incluir `prompt_version` evita reutilizar respuestas generadas con instrucciones antiguas.

## Almacenamiento

Propuesta:

```text
~/.cache/opencode-shunt/bulk-read/
```

Cada entrada:

```text
<hash>.json
```

Metadatos:

```json
{
  "created_at": "...",
  "model": "...",
  "question": "...",
  "files": [...],
  "response": "...",
  "prompt_tokens": 16077,
  "output_tokens": 636,
  "duration_ms": 7600
}
```

No almacenar contenido completo de archivos.

## Invalidación

V1:

```text
mtime + size
```

Si después aparecen falsos hits, pasar a hash de contenido.

## Telemetría

Añadir:

```text
cache_hit
cache_miss
cache_age_ms
```

## Criterio de aceptación

Dos llamadas idénticas:

```text
primera: Qwen
segunda: cache
```

Objetivo:

```text
segunda respuesta < 100 ms
0 tokens Ollama
mismo resultado
```

Editar uno de los archivos debe provocar:

```text
cache miss
```

**Impacto:** alto.

---

# 5. Prioridad P1 — Output Shunt

Ésta es probablemente la mejora funcional más importante después de rutas y caché.

## Problema

La V1 protege:

```text
filesystem → Claude
```

pero no:

```text
tools → Claude
```

Ejemplos:

```text
pytest
git diff
docker logs
kubectl logs
terraform plan
gcloud
dbt
airflow
```

Una ejecución puede devolver miles de líneas.

## Arquitectura

Usar:

```text
tool.execute.after
```

Pipeline:

```text
tool termina
    │
    ▼
medir output
    │
    ▼
¿menor que threshold?
  │             │
 sí             no
  │             │
return raw       ▼
            guardar raw
                 │
                 ▼
             clasificar
                 │
                 ▼
            resumir Qwen
                 │
                 ▼
      devolver resumen + path
```

---

# 6. Resumen especializado según output

No utilizar el mismo prompt para todo.

## Tests

Para:

```text
pytest
python -m pytest
uv run pytest
```

Preservar literalmente:

```text
FAILED test name
exception class
exception message
assertion
file:line
relevant stack frames
summary
```

Eliminar o condensar:

```text
tests que pasan
warnings repetidos
progreso
ruido
```

Nunca parafrasear una excepción cuando pueda conservarse literalmente.

## Git diff

Conservar:

```text
archivos modificados
hunks relevantes
líneas añadidas/eliminadas
cambios potencialmente peligrosos
```

## Logs

Conservar:

```text
ERROR
WARN relevante
timestamp
service
traceback
correlation/request IDs
primera aparición
última aparición
frecuencia
```

Agrupar repeticiones.

## Terraform / infraestructura

Conservar literalmente:

```text
resource
action create/update/destroy
attributes críticos
warnings/errors
```

Nunca ocultar:

```text
destroy
replace
force replacement
```

---

# 7. Almacenamiento de output raw

Propuesta:

```text
~/.local/share/opencode-shunt/output/
```

Formato:

```text
2026-09-09T180312Z_<session>_<tool>.log
```

La respuesta a Claude debe contener el path del raw.

Threshold inicial:

```text
> 30 KB
OR
> 400 líneas
```

No asumir que es definitivo.

## Criterio de aceptación

Generar un output de tests de unas 5.000 líneas.

Claude debe recibir:

```text
tests fallidos
error exacto
stack relevante
resumen
path raw
```

y ser capaz de diagnosticar el fallo con calidad equivalente al raw completo.

**Impacto:** muy alto.

---

# 8. Prioridad P1 — Nivel L0 determinista

El sistema debe evolucionar de:

```text
L1 = Qwen
L2 = Claude
```

a:

```text
L0 = herramientas exactas
L1 = Qwen
L2 = Claude
```

Regla:

```text
¿Dónde está X?      → L0
¿Cómo funciona X?   → L1
¿Qué hacemos con X? → L2
```

---

# 9. `code_search`

Crear custom tool:

```text
code_search(
    pattern,
    paths?,
    glob?,
    context_lines?
)
```

Internamente usar:

```text
ripgrep
```

Salida:

```text
path
line
matching line
opcionalmente N líneas de contexto
```

Ejemplo:

```text
code_search("transaction_checksum")
```

Resultado:

```text
models/silver/transactions.sql:42
macros/transaction_checksum.sql:3
tests/test_transactions.py:87
```

Sin LLM.

Ventajas:

```text
0 hallucinations
0 coste
latencia mínima
resultado reproducible
```

Posible evolución futura:

```text
tree-sitter
LSP
ctags
```

pero no empezar por ahí.

## Criterio de aceptación

El orchestrator debe preferir `code_search` para localizar literales, funciones, clases o símbolos conocidos.

---

# 10. Prioridad P1 — Keep Alive de Ollama

Problema:

```text
Ollama descarga modelo tras inactividad
      ↓
siguiente bulk_read
      ↓
~5 s de recarga
```

Configuración recomendada:

```bash
sudo systemctl edit ollama
```

Añadir:

```ini
[Service]
Environment="OLLAMA_KEEP_ALIVE=30m"
```

Después:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Verificar el comportamiento real antes/después.

---

# 11. Prioridad P2 — Evaluar `local-writer`

No activarlo directamente.

Debe ganar su puesto mediante benchmark.

## Hipótesis

Puede compensar para:

```text
tests repetitivos
fixtures
boilerplate
renames
cambios mecánicos
```

Pero puede salir caro si:

```text
Qwen genera
  ↓
Claude revisa
  ↓
Claude detecta errores
  ↓
Claude reescribe
```

---

# 12. Benchmark de `local-writer`

Elegir tres tareas reales.

## W1 — Tests siguiendo patrón

Crear tests para un módulo copiando el patrón de otro.

Medir:

```text
tests que pasan
correcciones frontier
tokens frontier
tiempo total
```

## W2 — Fixtures

Crear varias fixtures similares.

## W3 — Refactor mecánico

Ejemplo:

```text
rename de API interna
actualizar imports
actualizar tests
```

---

# 13. Regla para adoptar `local-writer`

Adoptarlo sólo si:

```text
>= 80-90% de tareas mecánicas correctas al primer intento
```

y:

```text
correcciones medias <= 1
```

y además:

```text
tokens frontier revisando
<
tokens frontier que habría consumido escribiéndolo
```

Si no cumple:

```text
descartar
```

No hay obligación de tener writer local.

## Permisos si se activa

```text
read    allow
grep    allow
glob    allow
edit    allow
bash    ask
```

Bloquear explícitamente:

```text
git push
git reset --hard
git clean
rm -rf
sudo
chmod recursivo
chown
acciones fuera del repo
```

Todo diff generado debe pasar por revisión frontier.

---

# 14. Prioridad P2 — Telemetría útil

Crear un comando:

```text
shunt-stats
```

Salida propuesta:

```text
Last 7 days

Frontier reads blocked:          37
Files delegated:                 91
Lines kept out of frontier:   42,381
Local tokens processed:      814,200
Bulk-read cache hits:             18
Estimated cloud tokens saved: 231,000
Average local latency:           6.2s
P95 local latency:               8.7s
Output shunts:                    14
```

La finalidad no es tener dashboards bonitos.

Es poder responder:

```text
¿esto sigue compensando dentro de tres meses?
```

Métricas importantes:

```text
cloud_ingested_tokens
reference_accuracy
bug_resolution_success
number_of_frontier_corrections
bulk_read_ms
cache_hit_ms
ollama_reload_ms
shunt_block_count
false_positive_count
fallback_count
```

---

# 15. Prioridad P2 — Globalización

No hacerlo inmediatamente.

Mantener por proyecto:

```text
repo/
├── .opencode/
└── opencode.json
```

Condición previa:

```text
1-2 semanas de uso real
```

Revisar:

```text
falsos positivos
latencia
errores de routing
fallbacks
cache
telemetría
```

Después mover piezas genéricas a:

```text
~/.config/opencode/
```

y dejar por proyecto:

```text
thresholds
excepciones
prompts específicos
reglas de dominio
```

---

# 16. Prioridad P3 — Probar 64k con KV cache cuantizado

No es prioritario.

Investigar sólo si empieza a haber demasiadas consultas troceadas.

Configuración candidata:

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
```

y:

```text
num_ctx = 65536
```

Benchmark obligatorio:

```text
VRAM
prefill
generation
line citation accuracy
precisión en mitad del contexto
latencia
```

Adoptar 64k sólo si mejora tareas reales.

No porque "quepa".

---

# 17. Cosas que NO recomiendo añadir

Mientras el sistema siga resolviendo el problema actual, evitar:

```text
RAG
embeddings
vector database
LangChain
LangGraph
CrewAI
AutoGen
LiteLLM
servicio FastAPI propio
Redis
modelo 70B
```

Cada capa nueva debe responder:

```text
¿Qué problema medido resuelve?
```

Si no hay una respuesta concreta, no añadirla.

---

# 18. Arquitectura objetivo V2

```text
                              TÚ
                               │
                               ▼
                    ┌───────────────────┐
                    │    ORCHESTRATOR   │
                    │      Claude       │
                    └─────────┬─────────┘
                              │
       ┌──────────────────────┼────────────────────────┐
       │                      │                        │
       ▼                      ▼                        ▼
   L0 exacto              L1 local                  L2 frontier
       │                      │                        │
       │                ┌─────┴─────┐                  │
       │                │           │                  │
 code_search       bulk_read   local-explorer          │
       │                │           │                  │
       │                └─────┬─────┘                  │
       │                      ▼                        │
       │                  Qwen 30B                     │
       │                  RTX 4090                     │
       │                                               │
       └───────────────────────► Claude ◄──────────────┘
                                   │
                                   ▼
                                 tools
                                   │
                                   ▼
                              output shunt
                                   │
                                   ▼
                               Qwen local
                                   │
                                   ▼
                             resumen + raw path
```

Rodeando todo el sistema:

```text
path verification
cache
telemetry
provider-aware shunt
```

---

# 19. Orden exacto recomendado

## Sprint 1 — Fiabilidad

1. **Path verification**
2. **Cache de `bulk_read`**
3. **Ollama KEEP_ALIVE**

Objetivo:

```text
menos errores
menos latencia
```

## Sprint 2 — Segundo gran ahorro

4. **Output Shunt**
5. **`code_search` / L0**

Objetivo:

```text
reducir más contexto frontier
evitar LLM donde una búsqueda exacta basta
```

## Sprint 3 — Medición

6. **`shunt-stats`**
7. **Repetir benchmark A/B**

Comparar V1 contra V2.

## Sprint 4 — Experimentos

8. **Benchmark `local-writer`**
9. **64k experimental sólo si los datos lo justifican**

## Sprint 5 — Producción personal

10. **Usarlo 1-2 semanas**
11. **Revisar telemetría**
12. **Ajustar thresholds**
13. **Globalizar**

---

# 20. Criterios de éxito de la V2

Consideraría terminada la V2 cuando:

- [ ] ninguna ruta inexistente llegue a Claude sin estar marcada;
- [ ] las rutas corregibles se validen automáticamente;
- [ ] `bulk_read` tenga caché con invalidación;
- [ ] una consulta cacheada sea prácticamente instantánea;
- [ ] outputs gigantes se reduzcan antes de entrar al frontier;
- [ ] errores y stack traces importantes se conserven literalmente;
- [ ] exista búsqueda L0 determinista;
- [ ] la telemetría muestre cache hits y output shunts;
- [ ] el ahorro agregado supere claramente la V1 en repos grandes;
- [ ] la calidad siga siendo equivalente al baseline;
- [ ] los falsos positivos del Shunt sean muy bajos;
- [ ] `local-writer` tenga una decisión basada en datos;
- [ ] el sistema se haya probado durante trabajo real antes de globalizarse.

---

# 21. Resultado ideal

## V1

```text
Claude
   ↓
Qwen lee código grande
   ↓
Claude piensa
```

## V2

```text
                 pregunta
                    │
                    ▼
              ¿es exacta?
              /         \
            sí           no
            │             │
            ▼             ▼
         L0 / rg       ¿requiere lectura?
                          │
                          ▼
                     Qwen local
                          │
                    cache + verify
                          │
                          ▼
                       Claude
                          │
                       razona
                          │
                          ▼
                       tools
                          │
                          ▼
                     output shunt
                          │
                          ▼
                       Claude
```

El objetivo es que Claude reciba cada vez menos:

```text
ruido
boilerplate
búsquedas
logs gigantes
ficheros completos
```

y cada vez más:

```text
evidencia precisa
rangos relevantes
errores concretos
decisiones que realmente requieren razonamiento
```

---

# 22. Las cinco mejoras que haría sí o sí

En este orden:

```text
1. verificación determinista de rutas
2. caché de bulk_read
3. output shunt
4. code_search / L0
5. telemetría resumida
```

Después pararía.

Usaría el sistema.

Mediría.

Y sólo entonces decidiría si:

```text
local-writer
64k
más agentes
más automatización
```

aportan valor real.

La V1 ya ha demostrado que la idea funciona.

La V2 no debe convertirla en una plataforma compleja.

Debe convertirla en una herramienta **más fiable, más rápida y más invisible durante el trabajo diario**.
