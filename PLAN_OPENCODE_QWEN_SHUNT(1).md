# Plan de implementación: OpenCode + modelo frontier + Qwen3-Coder local

**Objetivo:** reproducir en OpenCode la estrategia de "model routing" descrita por Spotify: reservar el modelo caro/frontier para razonamiento, diseño, debugging difícil y revisión final, y descargar lectura masiva, exploración y trabajo mecánico en un modelo local servido por Ollama.

**Máquina objetivo:** Ubuntu + OpenCode + Ollama + `qwen3-coder:30b`.

**Fecha de diseño:** 2026-09-09.

---

## 1. Resultado que queremos

El flujo final debe ser:

```text
                          ┌─────────────────────┐
                          │      OpenCode       │
                          │                     │
                          │   ORCHESTRATOR      │
                          │ Claude / GPT / otro │
                          │   modelo frontier   │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
        bulk_read tool        local-explorer        local-writer
              │                      │                      │
              │                      │                      │
              └──────────────┬───────┴──────────────┬───────┘
                             │                      │
                             ▼                      ▼
                        Ollama local           Ollama local
                     qwen3-coder:30b        qwen3-coder:30b
                             │
                             ▼
                          RTX 4090
```

Principio:

```text
TRABAJO MASIVO / MECÁNICO                 TRABAJO DIFÍCIL

leer archivos grandes       ───────► Qwen local
buscar patrones              ───────► Qwen local
exploración de repositorio   ───────► Qwen local
resumir logs                 ───────► Qwen local
boilerplate                  ───────► Qwen local
tests repetitivos            ───────► Qwen local

arquitectura                 ───────► modelo frontier
debugging complejo           ───────► modelo frontier
razonamiento                 ───────► modelo frontier
decisiones delicadas         ───────► modelo frontier
revisión final               ───────► modelo frontier
```

El punto crítico no es simplemente "usar Qwen algunas veces". Debemos impedir que el modelo caro absorba accidentalmente miles de líneas antes de decidir delegarlas.

Para ello habrá dos mecanismos:

1. **Delegación voluntaria:** el agente principal sabe cuándo usar los subagentes locales.
2. **Shunt obligatorio:** un hook bloquea lecturas masivas realizadas por el agente principal y le indica que use `bulk_read`.

---

# 2. Decisiones de arquitectura

## 2.1 OpenCode será el orquestador

No construiremos un framework externo de agentes.

OpenCode ya proporciona:

- agentes primarios;
- subagentes;
- modelo distinto por agente;
- permisos diferentes por agente;
- sesiones hijas/contexto separado;
- herramientas personalizadas;
- plugins;
- hooks `tool.execute.before` y `tool.execute.after`.

Eso nos permite construir el sistema dentro de OpenCode.

---

## 2.2 Ollama será el backend local

Qwen se servirá mediante:

```text
http://127.0.0.1:11434
```

Usaremos principalmente el endpoint compatible con OpenAI:

```text
POST http://127.0.0.1:11434/v1/chat/completions
```

No hace falta API key para el Ollama local.

OpenCode puede acceder al modelo como:

```text
ollama/qwen3-coder:30b
```

o, si creamos una variante específica:

```text
ollama/qwen3-coder-shunt:30b
```

---

## 2.3 El `bulk-reader` NO será inicialmente un subagente

Será una **custom tool** de OpenCode:

```text
bulk_read(question, paths)
```

Motivo:

Si el agente frontier utiliza el `read` normal sobre un archivo de 5.000 líneas, esas líneas ya han entrado en su contexto.

En cambio:

```text
frontier
   │
   │ bulk_read(
   │   question="¿Dónde se calcula el checksum?",
   │   paths=[...]
   │ )
   ▼
bulk-read.ts
   │
   ├── lee archivos directamente desde disco
   ├── los manda a Ollama
   ├── Qwen los analiza
   └── devuelve SOLO un resumen
   │
   ▼
frontier
```

El modelo caro nunca ve el contenido completo.

Esta será la pieza más importante del sistema.

---

# 3. APIs y componentes necesarios

## Obligatorios

### A. OpenCode

Ya instalado.

Comprobaremos:

```bash
opencode --version
```

No migraremos a una beta distinta únicamente para este proyecto si la versión instalada ya soporta:

- agentes;
- custom tools;
- plugins;
- `tool.execute.before`.

La implementación se hará contra la versión real instalada en la máquina.

---

### B. Ollama

Ya instalado.

Comprobaremos:

```bash
ollama --version
curl http://127.0.0.1:11434/api/tags
ollama ps
```

Modelo:

```text
qwen3-coder:30b
```

---

### C. API local de Ollama

La herramienta `bulk_read` usará:

```text
http://127.0.0.1:11434/v1/chat/completions
```

No necesitaremos:

- servidor MCP;
- FastAPI;
- Redis;
- base de datos;
- Docker;
- API gateway.

Para la primera versión será una llamada HTTP local directa.

---

### D. Provider del modelo frontier

El agente `orchestrator` utilizará el proveedor que queramos tener como cerebro principal en OpenCode.

Puede ser, por ejemplo:

```text
OpenAI
Anthropic
OpenCode Zen
OpenRouter
otro provider soportado por OpenCode
```

La autenticación se gestionará mediante OpenCode (`/connect`) y no dentro de nuestras tools.

**No meteremos ninguna API key en los archivos del proyecto.**

Si el provider ya está conectado en OpenCode, no necesitamos crear ninguna credencial nueva para este proyecto.

---

## No necesarios inicialmente

No instalaremos en la V1:

- MCP;
- LiteLLM;
- LangChain;
- LangGraph;
- CrewAI;
- AutoGen;
- vector database;
- embeddings;
- RAG;
- daemon propio;
- servicio Python permanente.

Todo eso añadiría complejidad sin resolver nuestro problema principal.

---

# 4. Estructura que construiremos

Primero se hará **por proyecto** para no romper todos tus repositorios.

```text
mi-proyecto/
│
├── opencode.json
│
└── .opencode/
    │
    ├── agents/
    │   ├── orchestrator.md
    │   ├── local-explorer.md
    │   └── local-writer.md
    │
    ├── tools/
    │   └── bulk-read.ts
    │
    ├── plugins/
    │   └── shunt.ts
    │
    └── package.json
```

Cuando el sistema esté probado, moveremos las piezas genéricas a:

```text
~/.config/opencode/
```

para que funcionen en todos los repositorios.

Orden correcto:

```text
probar en 1 repo
      ↓
ajustar
      ↓
benchmark
      ↓
hacerlo global
```

---

# 5. Agentes

## 5.1 `orchestrator`

**Tipo:** primary  
**Modelo:** frontier  
**Responsabilidad:** pensar.

Debe encargarse de:

- entender la petición del usuario;
- dividir el problema;
- decidir qué delegar;
- arquitectura;
- debugging complejo;
- razonar sobre resultados;
- revisar cambios importantes;
- producir la respuesta final.

No debe emplearse como lector masivo.

### Regla básica

Si necesita responder una pregunta que implique:

- un archivo grande;
- varios archivos completos;
- descubrir relaciones en gran parte del repo;
- recopilar información repetitiva;

debe delegar.

Pseudo-configuración:

```yaml
---
description: Main engineering orchestrator. Delegates bulk work to local agents.
mode: primary
model: <PROVIDER>/<FRONTIER_MODEL>
---

You are the senior engineering orchestrator.

Use your own context for reasoning, architecture, debugging and final review.

Delegate aggressively:
- bulk repository exploration -> local-explorer
- large known files -> bulk_read
- repetitive implementation -> local-writer

Never consume complete large files when bulk_read can answer the question.
Never ask a local model to make final architecture or safety-critical decisions.
```

El modelo exacto se decidirá al ejecutar la instalación según los providers que tengas conectados.

---

## 5.2 `local-explorer`

**Tipo:** subagent  
**Modelo:** `ollama/qwen3-coder-shunt:30b`  
**Responsabilidad:** explorar sin modificar.

Se utilizará cuando todavía **no sabemos qué archivos son relevantes**.

Ejemplos:

```text
Encuentra dónde se generan los checksums de transacciones.
Localiza todo el flujo de bronze a silver.
Busca quién llama a calculate_daily_balances.
Encuentra todos los puntos donde se usa company_id para deduplicar.
```

Permisos:

```text
read     allow
grep     allow
glob     allow
list     allow
edit     deny
bash     restringido/deny inicialmente
web      deny
```

Debe devolver al padre únicamente:

```text
- archivos relevantes;
- símbolos relevantes;
- relaciones;
- rangos aproximados de líneas;
- conclusión corta;
- incertidumbres.
```

No debe pegar archivos enteros en su respuesta.

Como corre en una sesión hija, su contexto masivo no contamina directamente el contexto del agente principal.

---

## 5.3 `local-writer`

**Tipo:** subagent  
**Modelo:** `ollama/qwen3-coder-shunt:30b`  
**Responsabilidad:** trabajo mecánico sobre código.

Buenos candidatos:

- tests repetitivos;
- fixtures;
- schemas;
- modelos;
- boilerplate;
- renames;
- cambios repetidos en varios ficheros;
- refactors mecánicos;
- documentación mecánica.

No debe decidir:

- arquitectura;
- contratos críticos;
- estrategia de migración;
- cambios de seguridad;
- algoritmos delicados.

Permisos iniciales:

```text
read     allow
grep     allow
glob     allow
list     allow
edit     allow
bash     ask/restringido
web      deny
```

Prohibiremos explícitamente:

```text
git push
git reset --hard
git clean
rm -rf
sudo
cambios fuera del worktree
```

Salida esperada:

```text
Archivos modificados:
- ...
- ...

Cambios:
- ...
- ...

Tests recomendados:
- ...
```

No debe devolver las 2.000 líneas que haya generado.

---

# 6. `bulk_read`: pieza central

Archivo:

```text
.opencode/tools/bulk-read.ts
```

Interfaz prevista:

```text
bulk_read(
    question: string,
    paths: string[],
    max_output_tokens?: number
)
```

Ejemplo de uso por el orchestrator:

```text
bulk_read(
  question="Explica exactamente cómo se deduplican las transacciones y señala posibles inconsistencias.",
  paths=[
    "models/silver/transactions.sql",
    "macros/transaction_checksum.sql",
    "dags/bronze_to_silver.py"
  ]
)
```

## Flujo interno

```text
1. Validar paths.
2. Confirmar que están dentro del worktree.
3. Rechazar binarios.
4. Rechazar secretos conocidos.
5. Leer contenido.
6. Aplicar límite máximo de entrada.
7. Construir prompt enfocado.
8. POST a Ollama /v1/chat/completions.
9. Recibir respuesta de Qwen.
10. Devolver sólo la respuesta condensada a OpenCode.
```

El prompt enviado a Qwen incluirá instrucciones como:

```text
Answer only the requested question.

Do not reproduce whole files.
Do not provide unrelated observations.

For every important finding include:
- file path
- symbol/function/model
- approximate line/range when possible
- evidence/reason
- confidence

Prefer compact structured output.
```

---

# 7. Seguridad de `bulk_read`

La herramienta no debe ser simplemente:

```text
cat paths | curl ollama
```

Implementaremos controles.

## Path traversal

Todo archivo debe resolver dentro de:

```text
context.worktree
```

Bloquear:

```text
../../
/etc/passwd
/home/... fuera del repo
symlinks que escapen del worktree
```

---

## Secretos

Bloqueo inicial para nombres tipo:

```text
.env
.env.*
*.pem
*.key
credentials*
service-account*
secrets*
```

Más adelante podremos permitir excepciones manuales si interesa.

---

## Binarios

Detectar/rechazar:

```text
images
archives
sqlite
parquet
avro
executables
```

La tool está pensada para texto y código.

---

## Límites

Primera configuración:

```text
máximo de archivos:       20
máximo total aproximado:  configurable
timeout Ollama:           configurable
máxima respuesta:         corta
```

No fijaremos el límite total definitivo hasta medir la velocidad y memoria de Qwen en la máquina.

---

# 8. Variante de Qwen específica

No quiero cambiar el modelo original.

Crearemos un alias:

```text
qwen3-coder-shunt:30b
```

con un `Modelfile`.

Primer candidato:

```text
FROM qwen3-coder:30b
PARAMETER num_ctx 32768
PARAMETER temperature 0.1
```

Creación:

```bash
ollama create qwen3-coder-shunt:30b -f Modelfile.qwen-shunt
```

Comprobar:

```bash
ollama show qwen3-coder-shunt:30b
```

## Contexto

No asumiremos que "más contexto = mejor".

Haremos pruebas:

```text
32k
 ↓
¿100% GPU y velocidad razonable?
 ↓ sí
64k
 ↓
¿sigue siendo razonable?
```

Comprobaremos después de ejecutar una consulta:

```bash
ollama ps
```

Objetivo preferido:

```text
PROCESSOR: 100% GPU
```

Si 64k provoca offload importante a CPU o empeora demasiado la latencia, nos quedaremos en 32k y haremos chunking.

Para nuestro diseño, **varias consultas locales de 32k pueden ser mejores que una monstruosa consulta de 100k**.

---

# 9. Shunt: impedir lecturas caras

Archivo:

```text
.opencode/plugins/shunt.ts
```

Usará:

```text
tool.execute.before
```

## V1 del hook

Interceptar `read`.

Lógica:

```text
modelo intenta read
       │
       ▼
¿archivo pequeño?
   │          │
  sí          no
   │          │
allow       ¿lectura acotada?
               │        │
              sí        no
               │        │
             allow     BLOCK
                        │
                        ▼
              "Use bulk_read or
               local-explorer"
```

Umbral inicial:

```text
350 líneas
```

No es un dogma; lo mediremos.

Permitiremos una lectura precisa como:

```text
líneas 800-950
```

aunque el fichero tenga 5.000 líneas.

La filosofía es:

```text
exploración masiva  -> Qwen
análisis preciso    -> frontier
```

---

## Bash guard

El modelo podría intentar saltarse `read` haciendo:

```bash
cat huge.py
less huge.py
more huge.py
head -n 5000 huge.py
tail -n 5000 huge.py
```

Añadiremos protección para los casos obvios.

V1:

```text
cat FILE
less FILE
more FILE
head/tail con salida excesiva
```

No intentaremos construir un parser completo de Bash el primer día.

Después ampliaremos según los casos reales que aparezcan.

---

# 10. Muy importante: el hook no debe bloquear al trabajador local

El Shunt existe para proteger el **contexto frontier**, no para impedir que Qwen lea.

Por eso, antes de convertirlo en global, verificaremos cómo identificar de forma fiable la sesión/agente que ejecuta una tool en la versión instalada de OpenCode.

El hook recibe al menos:

```text
tool
sessionID
callID
args
```

La implementación deberá:

```text
si agente == orchestrator:
    aplicar Shunt
else:
    permitir
```

Si identificar el agente desde el hook resultase frágil en nuestra versión, usaremos la alternativa más robusta:

```text
orchestrator -> bulk_read custom tool
local-explorer -> herramientas de lectura
```

y limitaremos el hook inicialmente sólo a escenarios inequívocos.

**No se desplegará un hook global que rompa los subagentes.**

---

# 11. Routing que enseñaremos al orchestrator

Reglas iniciales:

## `bulk_read`

Usar cuando:

```text
✓ conocemos los archivos
✓ son grandes
✓ queremos responder una pregunta concreta sobre ellos
✓ no necesitamos modificarlos todavía
```

---

## `local-explorer`

Usar cuando:

```text
✓ no sabemos dónde está el código
✓ hay que recorrer el repo
✓ hay que seguir referencias
✓ hay que encontrar candidatos
```

Una vez localizados los puntos relevantes, el frontier puede leer únicamente rangos pequeños.

---

## `local-writer`

Usar cuando:

```text
✓ el cambio está bien definido
✓ es mecánico
✓ existe un patrón de referencia
✓ genera mucho código
```

Después:

```text
local-writer modifica
        ↓
orchestrator inspecciona git diff resumido
        ↓
lee sólo partes críticas
        ↓
acepta/corrige
```

---

## Frontier directo

Mantener cuando:

```text
✓ bug sutil
✓ concurrencia
✓ seguridad
✓ diseño
✓ migración de datos delicada
✓ SQL complejo con consecuencias
✓ comportamiento ambiguo
✓ revisión final
```

---

# 12. Segunda optimización: logs y outputs enormes

Una vez funcione el `bulk_read`, atacaremos otro sumidero de tokens:

```text
pytest con 5.000 líneas
terraform plan enorme
docker logs
kubectl logs
gcloud output
git diff gigantesco
```

Fase posterior:

```text
tool.execute.after
       │
       ▼
¿salida > N caracteres?
       │
      sí
       │
       ├── conservar resultado completo localmente
       ├── resumir con Qwen
       └── entregar resumen al frontier
```

Esto será nuestro:

```text
OUTPUT SHUNT
```

Pero **no forma parte del MVP**.

Primero lectura de código. Después outputs.

---

# 13. Telemetría

Necesitamos comprobar que realmente merece la pena.

Registraremos en local:

```text
timestamp
tool
agent
archivo(s)
bytes leídos
líneas estimadas
bulk_read sí/no
duración Ollama
shunt bloqueó sí/no
```

NO guardaremos:

```text
contenido completo del código
prompts con secretos
API keys
```

Más adelante, si OpenCode expone de forma cómoda los tokens de cada turno, añadiremos:

```text
cloud input tokens
cloud output tokens
local estimated tokens
```

---

# 14. Benchmarks

Antes de decir que el sistema "ahorra X%" haremos pruebas reales.

## Test A — archivo gigante

Elegir un fichero de más de 1.000 líneas.

Pregunta concreta:

```text
"¿Cómo se gestionan los errores y qué caminos pueden producir retry?"
```

Comparar:

```text
OpenCode normal
vs
OpenCode + bulk_read
```

Medir:

```text
calidad
tokens cloud
latencia
```

---

## Test B — varios ficheros

5-10 archivos relacionados.

Pregunta:

```text
"Traza el flujo completo desde entrada hasta persistencia."
```

Comprobar que Qwen devuelve suficiente información para que el frontier razone sin abrir todos los ficheros.

---

## Test C — exploración

Pregunta sin decir dónde está la implementación:

```text
"Encuentra dónde se calcula X y explícame el flujo."
```

Debe activar `local-explorer`.

---

## Test D — escritura mecánica

Dar al `local-writer`:

```text
"Crea tests para este módulo siguiendo exactamente el patrón de estos tests existentes."
```

Comprobar:

```text
calidad del diff
tests
cantidad de texto que recibe el frontier
```

---

## Test E — bug difícil

Usar un bug donde el razonamiento sea importante.

Objetivo:

```text
Qwen explora
frontier razona
```

No:

```text
Qwen decide todo
```

Éste es el test de calidad más importante.

---

# 15. Criterios para considerar el MVP terminado

El sistema estará listo cuando se cumpla todo esto:

- [ ] OpenCode ve `qwen3-coder-shunt:30b`.
- [ ] `local-explorer` usa realmente Ollama.
- [ ] `local-writer` usa realmente Ollama.
- [ ] `bulk_read` llama directamente a Ollama.
- [ ] Un archivo grande puede analizarse sin entrar completo en el contexto frontier.
- [ ] El orchestrator usa `bulk_read` voluntariamente.
- [ ] El Shunt bloquea una lectura masiva del orchestrator.
- [ ] Las lecturas acotadas siguen funcionando.
- [ ] Los agentes locales no quedan bloqueados por Shunt.
- [ ] El local-writer no puede ejecutar acciones destructivas.
- [ ] El orchestrator revisa los cambios importantes.
- [ ] Tenemos al menos tres benchmarks antes/después.
- [ ] La calidad no cae significativamente.
- [ ] Se observa una reducción clara de tokens cloud en tareas de exploración.

No pondremos como objetivo obligatorio "90%".

Spotify logró cifras de ese orden en determinados workloads de lectura; nuestro objetivo debe ser:

```text
máximo ahorro sin perder calidad
```

---

# 16. Orden exacto de ejecución

## Fase 0 — Inventario

No modificar nada.

Ejecutar:

```bash
opencode --version
ollama --version
curl http://127.0.0.1:11434/api/tags
ollama show qwen3-coder:30b
ollama ps
```

Guardar las versiones.

---

## Fase 1 — Ollama

1. Crear `qwen3-coder-shunt:30b`.
2. Empezar con contexto 32k.
3. Probar `/v1/chat/completions`.
4. Verificar carga de GPU.
5. Probar 64k si tiene sentido.
6. Elegir configuración definitiva.

**Resultado de la fase:** endpoint local estable.

---

## Fase 2 — OpenCode + Qwen

1. Confirmar que OpenCode descubre/configura Ollama.
2. Verificar que aparece el modelo.
3. Crear únicamente `local-explorer`.
4. Invocarlo manualmente.
5. Confirmar que usa Qwen y no el provider cloud.

**Resultado:** primer subagente local funcionando.

---

## Fase 3 — `bulk_read`

1. Crear custom tool.
2. Validación de rutas.
3. Lectura.
4. Llamada HTTP a Ollama.
5. Formato compacto.
6. Límites.
7. Protección de secretos.
8. Prueba en archivo grande.

**Resultado:** podemos preguntar por 5.000 líneas sin entregarlas al frontier.

Ésta es la primera gran victoria.

---

## Fase 4 — Orchestrator

1. Crear agente primary.
2. Escribir routing rules.
3. Permitir `bulk_read`.
4. Permitir invocar `local-explorer`.
5. Ejecutar consultas reales.
6. Ajustar descripciones hasta que delegue de forma consistente.

**Resultado:** delegación voluntaria.

---

## Fase 5 — `local-writer`

1. Crear agente.
2. Permisos mínimos.
3. Prohibiciones.
4. Test con boilerplate.
5. Test con tests.
6. Revisión del diff por orchestrator.

**Resultado:** generación masiva local.

---

## Fase 6 — Shunt

1. Crear hook `tool.execute.before`.
2. Observar llamadas sin bloquear.
3. Añadir detección de tamaño.
4. Bloquear `read` masivo únicamente para el frontier.
5. Permitir rangos.
6. Añadir `cat/less/more`.
7. Test de bypass.
8. Test con subagentes.

**Resultado:** ahorro obligatorio, no sólo dependiente del prompt.

---

## Fase 7 — Benchmark

Comparar:

```text
normal
vs
routing voluntario
vs
routing + Shunt
```

Si el resultado es bueno, avanzar.

---

## Fase 8 — Globalizar

Mover lo genérico desde:

```text
<repo>/.opencode/
```

a:

```text
~/.config/opencode/
```

Mantener por proyecto únicamente:

```text
reglas específicas
thresholds particulares
excepciones
```

---

## Fase 9 — Output Shunt

Sólo después del MVP.

Optimizar:

```text
logs
test output
git diff
terraform
kubectl
gcloud
```

---

# 17. Estrategia de fallback

El sistema nunca debe hacer que trabajar sea peor.

Por eso:

```text
Qwen falla
    ↓
orchestrator puede continuar

bulk_read timeout
    ↓
devolver error corto, no bloquear sesión

Shunt da falso positivo
    ↓
permitir lectura explícita/acotada

calidad local insuficiente
    ↓
escalar al frontier
```

La regla es:

```text
local first for cheap work
frontier always available for hard work
```

---

# 18. Política de escalado de modelos

Podemos pensar en tres niveles en el futuro:

```text
L0
shell/ripgrep determinista
coste ≈ 0

L1
Qwen3-Coder local
coste monetario ≈ 0

L2
frontier model
coste alto
```

Ejemplo:

```text
"¿Dónde aparece transaction_checksum?"

rg primero
 ↓
Qwen organiza resultados
 ↓
frontier sólo si hace falta razonar
```

Esto sería aún mejor que mandar absolutamente todo a un LLM.

---

# 19. Qué NO vamos a hacer

Evitar estas trampas:

## No delegar todo a Qwen

Ahorrar tokens a cambio de empeorar bugs difíciles no sirve.

---

## No meter todo el repo en 128k/256k sólo porque el modelo pueda

Contexto disponible no significa contexto gratuito ni razonamiento perfecto.

Preferimos:

```text
pregunta concreta
+
evidencia relevante
```

---

## No construir una plataforma de microservicios

El sistema debe seguir siendo comprensible:

```text
OpenCode
Ollama
3 agentes
1 tool
1 plugin
```

---

## No depender únicamente del prompt

"Por favor no leas archivos grandes" no es suficiente.

El Shunt será una restricción técnica.

---

# 20. Configuración final esperada

Cuando terminemos, iniciarás OpenCode como siempre.

Podrás escribir:

```text
Tenemos duplicados en transactions silver.
Averigua de dónde vienen y corrígelo.
```

Y el flujo ideal será aproximadamente:

```text
ORCHESTRATOR
    │
    │ necesita localizar implementación
    ▼
local-explorer (Qwen)
    │
    │ devuelve 4 archivos relevantes
    ▼
ORCHESTRATOR
    │
    │ necesita comprender 3 archivos grandes
    ▼
bulk_read (Qwen)
    │
    │ devuelve resumen + símbolos + rangos
    ▼
ORCHESTRATOR
    │
    │ razona y detecta la causa
    │
    ├── cambio delicado -> lo hace/revisa él
    │
    └── cambio mecánico -> local-writer
                              │
                              ▼
                           Qwen
                              │
                              ▼
                          git diff
                              │
                              ▼
                       ORCHESTRATOR
                       revisión final
```

Ése es el objetivo.

---

# 21. Primera sesión de implementación

Cuando ejecutemos este plan no intentaremos hacerlo entero de golpe.

La primera sesión debería limitarse a:

```text
FASE 0
  ↓
FASE 1
  ↓
FASE 2
```

Es decir:

1. comprobar versiones;
2. preparar Qwen;
3. conseguir que un subagente de OpenCode corra realmente en Ollama.

Sólo después construiremos `bulk_read`.

Esto permite aislar los problemas uno a uno.

---

# 22. Fuentes oficiales consultadas

OpenCode — Agents  
https://opencode.ai/docs/agents/

OpenCode — Providers / Ollama  
https://opencode.ai/docs/providers/

OpenCode — Custom Tools  
https://opencode.ai/docs/custom-tools/

OpenCode — Plugins  
https://opencode.ai/docs/plugins/

Ollama — OpenAI compatibility  
https://docs.ollama.com/api/openai-compatibility

Ollama — Context length  
https://docs.ollama.com/context-length

Ollama — Modelfile  
https://docs.ollama.com/modelfile

> Nota: OpenCode está evolucionando rápidamente. Antes de escribir los hooks definitivos comprobaremos la versión instalada y ajustaremos el código exactamente a su API, en vez de copiar código de una versión diferente.

---

# Resumen ejecutivo

Construiremos:

```text
1 modelo frontier
2 subagentes Qwen
1 bulk-read tool
1 Shunt plugin
```

Sin infraestructura adicional.

La pieza crítica será:

```text
bulk_read
```

porque evita físicamente que miles de líneas entren en el contexto caro.

Después:

```text
Shunt
```

convertirá esa optimización en una política automática.

El objetivo final no es que Qwen sustituya al modelo frontier.

El objetivo es que el modelo frontier deje de gastar inteligencia y tokens en trabajo que tu GPU puede hacer localmente.
