# Valoración técnica de `PLAN_V2.md`

**Destinatario:** agente autor de `PLAN_V2.md`  
**Fecha:** 2026-09-09  
**Contexto:** revisión del plan V2 para OpenCode + Claude Opus 4.8 + Ollama + `qwen3-coder-shunt:30b` + RTX 4090.

---

# 1. Valoración general

La propuesta mejora claramente el plan V2 anterior.

Mi valoración es aproximadamente:

```text
9/10
```

La razón principal es que reordena prioridades apoyándose en **telemetría real**, elimina abstracciones que todavía no hacen falta y detecta un conflicto de diseño que el plan anterior no había cerrado.

En concreto, considero especialmente acertados estos cuatro cambios:

1. degradar la caché de `bulk_read` de prioridad;
2. implementar L0 inicialmente como regla de routing sobre `grep`/`glob`;
3. detectar el conflicto entre Output Shunt y Read Shunt;
4. introducir la idea de un nivel de escalada para contexto largo sin convertirlo en un agente visible adicional.

Adoptaría la mayor parte del plan.

Mis dos reservas principales son:

```text
1. no fijar todavía >=3 batches como regla automática hacia cloud;
2. controlar la escalada de código hacia proveedores externos mediante política explícita por repositorio.
```

---

# 2. La caché: el cambio de prioridad es correcto

El plan anterior situaba la caché de `bulk_read` demasiado arriba.

Los datos actuales indican:

```text
bulk_read calls:                  7
exact repeated (question, files): 0
```

Con esos datos, una caché basada en:

```text
question + paths
```

habría producido:

```text
0% hit rate
```

Además, dos consultas semánticamente parecidas pero textualmente distintas tampoco habrían producido hit.

Por tanto, estoy de acuerdo con:

```text
NO implementar la caché todavía
        ↓
shadow cache
        ↓
medir durante uso real
        ↓
si hit rate > 10%
    implementar
else
    descartar
```

Esto es mejor que construir una optimización porque intuitivamente "debería funcionar".

También es relevante el hallazgo de latencia:

```text
prefill     ≈ 2,4 s
generation  ≈ 4,0 s
```

Eso indica que, para la experiencia interactiva, puede aportar más reducir la longitud de la respuesta de Qwen que optimizar únicamente la lectura.

Conclusión:

```text
caché real -> experimento condicionado
shadow cache -> sí
```

---

# 3. L0: estoy de acuerdo en no crear `code_search` todavía

El plan anterior proponía crear:

```text
code_search()
    ↓
ripgrep
```

Pero si OpenCode ya expone `grep` y `glob`, crear otra tool ahora añade una capa sin añadir una capacidad nueva.

Lo correcto inicialmente es enseñar al orchestrator:

```text
"¿dónde está X?"
      ↓
grep / glob

"¿cómo funciona X?"
      ↓
local-explorer / bulk_read

"¿qué deberíamos hacer con X?"
      ↓
frontier
```

Es decir:

```text
L0 = exactitud determinista
L1 = lectura / interpretación barata
L2 = razonamiento
```

Estoy de acuerdo con implementar L0 primero como unas pocas reglas en `orchestrator.md`.

Sólo crearía después un wrapper `code_search` si la telemetría muestra un problema real, por ejemplo:

```text
grep devuelve 300 coincidencias
        ↓
esas 300 líneas entran en contexto
```

En ese caso sí tendría sentido una tool compacta con:

```text
limit
path:line
context_lines
```

Pero no antes.

---

# 4. Excelente hallazgo: Output Shunt y Read Shunt chocan

Este es probablemente el mejor punto de la propuesta.

El diseño anterior proponía:

```text
output gigante
    ↓
guardar raw
    ↓
resumir
    ↓
Claude recibe path del raw
```

Pero después:

```text
Claude:
read(raw)

Read Shunt:
> 400 líneas
    ↓
BLOCK
```

Los dos mecanismos entrarían en conflicto.

No recomiendo una exención del directorio de outputs.

Eso permitiría accidentalmente:

```text
Claude lee 5.000 líneas del raw
```

y rompería exactamente la garantía que estamos intentando preservar.

La solución correcta es:

```text
read_output(
    path,
    offset,
    limit
)
```

con:

```text
limit obligatorio
limit máximo duro
path limitado al directorio gestionado de outputs
```

Yo añadiría además:

```text
read_output no acepta paths arbitrarios
```

Sólo debería poder abrir archivos previamente generados por Output Shunt y registrados por el sistema.

Esto convierte el fichero raw en una especie de artefacto opaco con acceso paginado.

Lo adoptaría sin cambios conceptuales.

---

# 5. Escalada L1.5: buena idea, pero todavía es una hipótesis

La arquitectura propuesta:

```text
L0
grep / glob

L1
Qwen local

L1.5
modelo barato de contexto largo

L2
frontier
```

es conceptualmente muy buena.

Resuelve un techo real:

```text
Qwen local = 32k
```

y evita intentar resolverlo simplemente subiendo contexto local hasta romper VRAM o degradar calidad.

También me gusta que L1.5 sea invisible para el orchestrator.

Idealmente:

```text
bulk_read()
    │
    ├── cabe → Qwen
    │
    └── no cabe → long-context fallback
```

y no:

```text
orchestrator tiene que elegir
@qwen
@gemini
@deepseek
...
```

Menos decisiones visibles = menos routing errors.

---

# 6. Donde no estoy todavía de acuerdo: `>=3 batches → cloud`

La propuesta afirma aproximadamente:

```text
1 batch  -> Qwen
2 batches -> Qwen
>=3 batches -> long-context
```

La intuición es razonable:

```text
más batches
    ↓
más pérdida de relaciones cross-file
```

Pero con los datos disponibles esto todavía debería considerarse:

```text
HIPÓTESIS
```

no:

```text
POLÍTICA DEFINITIVA
```

Antes de automatizar ese threshold haría un benchmark específico.

---

# 7. Benchmark propuesto para decidir el threshold de L1.5

Elegir una pregunta cross-file donde sepamos que las relaciones importan.

Ejecutar variantes:

```text
A. contexto completo si cabe en 1 batch
B. 2 batches
C. 3 batches
D. 4 batches
E. modelo long-context con todo junto
```

Medir:

```text
referencias correctas
relaciones detectadas
hechos omitidos
conclusión final
latencia
tokens
coste
```

Especialmente evaluar:

```text
cross-file relationship recall
```

Si los resultados son:

```text
1 batch: excelente
2 batch: excelente
3 batch: cae
```

entonces sí:

```text
SHUNT_ESCALATION_BATCHES=3
```

Pero el número debería salir del benchmark.

Podría acabar siendo:

```text
2
3
4
```

según el repositorio, modelo y tipo de consulta.

---

# 8. Escalada cloud: añadiría política explícita por repositorio

La propuesta ya señala correctamente que enviar código completo a determinados servicios gratuitos puede tener implicaciones de uso de datos.

Yo iría un paso más allá.

No dependería únicamente de:

```text
SHUNT_ESCALATION_MODEL
```

porque el riesgo es olvidar que un repositorio concreto contiene código de cliente.

Añadiría una política explícita:

```text
allowCloudBulkCode: false
```

por defecto.

Por ejemplo:

```json
{
  "shunt": {
    "allowCloudBulkCode": false
  }
}
```

o equivalente en la configuración que resulte más natural para OpenCode.

Entonces:

```text
Qwen no puede procesar
        │
        ▼
allowCloudBulkCode?
      /       \
    no         sí
    │          │
fallback      L1.5
seguro
```

Para repositorios personales:

```text
true
```

Para código corporativo:

```text
false
```

salvo autorización explícita.

Esta decisión debe estar versionada junto al proyecto, no sólo en una variable global de la shell.

---

# 9. Escalonar el orchestrator: experimento excelente

Este punto me parece de las mejores ideas del documento.

Actualmente:

```text
todo razonamiento frontier
        ↓
Opus
```

Pero antes de seguir optimizando pequeños porcentajes de input, merece la pena medir si un orchestrator más barato mantiene la calidad.

Lo mejor es que ya existe:

```text
bench.py
```

y puede compararse sin implementar nueva arquitectura.

Yo haría inmediatamente:

```text
Opus
vs
Sonnet
vs
Gemini Pro / alternativa comparable
```

sobre:

```text
Test A
Test B
Test C
Test E real
```

---

# 10. Métricas para el benchmark del orchestrator

No miraría únicamente las citas.

Mediría:

```text
exactitud de referencias
número de pasos
herramientas utilizadas
tokens ingeridos
tokens de salida
coste
wall time
calidad de la solución
correcciones necesarias
```

Especialmente:

```text
number_of_steps
```

porque un modelo más barato que necesite el doble de pasos puede perder parte de la ventaja económica.

---

# 11. No automatizaría todavía el routing Opus/Sonnet

Sí al benchmark.

No todavía a:

```text
router:
"esto parece fácil" -> Sonnet
"esto parece difícil" -> Opus
```

Eso introduce otra heurística probabilística.

Primero probaría una selección explícita:

```text
orchestrator normal -> modelo barato que pase benchmark
orchestrator-opus   -> problemas difíciles
```

Si después de semanas de uso hay suficientes datos, entonces sí se puede estudiar routing automático.

Primero:

```text
determinismo
```

después:

```text
automatización
```

---

# 12. Sprint 1 propuesto: mejor que el anterior

Adoptaría prácticamente tal cual:

```text
1. routing L0 con grep/glob
2. OLLAMA_KEEP_ALIVE
3. path verification
```

Es un muy buen Sprint 1 porque:

```text
añade poca complejidad
elimina un fallo real
reduce cold-start
reduce delegaciones innecesarias
```

y puede ejecutarse rápidamente.

Es exactamente el tipo de evolución que conviene ahora.

---

# 13. Output Shunt merece su propio sprint

También coincido.

No intentaría soportar desde el primer día:

```text
pytest
git diff
kubectl
terraform
docker
dbt
gcloud
airflow
todo shell arbitrario
```

Empezaría por:

```text
pytest
git diff
```

Son formatos frecuentes y relativamente estructurados.

Después la telemetría nos dirá qué otros outputs realmente generan volumen.

Regla importante:

```text
errores
assertions
test names
stack frames relevantes
```

deben conservarse literalmente.

Qwen puede resumir ruido.

No debe reinterpretar evidencia de debugging.

---

# 14. Path verification sigue siendo P0

Aquí no cambiaría nada.

El único fallo de calidad real observado fue:

```text
path plausible
pero incorrecto
```

Eso es ideal para sustituir probabilística por determinismo.

Estados:

```text
VERIFIED
CORRECTED
AMBIGUOUS
NOT_FOUND
```

y nunca:

```text
corrección silenciosa de una ruta ambigua
```

Es una mejora pequeña que elimina una clase completa de errores.

---

# 15. Keep Alive: sí

También lo aplicaría inmediatamente.

La latencia de recarga de ~5 segundos es suficientemente grande como para notarse y suficientemente fácil de evitar.

```text
OLLAMA_KEEP_ALIVE=30m
```

es razonable para una estación de trabajo usada interactivamente.

Debe medirse:

```text
load_duration
```

antes y después para verificar que funciona como esperamos.

---

# 16. Arquitectura que recomiendo finalmente

```text
                            USER
                              │
                              ▼
                     ┌─────────────────┐
                     │  ORCHESTRATOR   │
                     │ frontier model  │
                     └────────┬────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
       L0                    L1                    L2
  grep / glob          bulk_read / explorer       reason
   exacto                   │                    decide
                             ▼
                         Qwen local
                         RTX 4090
                             │
                      ¿context overflow?
                             │
                     ┌───────┴───────┐
                     │               │
                    no              sí
                     │               │
                     │       ¿cloud permitido?
                     │          /        \
                     │        no          sí
                     │        │           │
                     │     fallback      L1.5
                     │                   │
                     │           long-context cheap
                     │
                     └───────────┬────────────
                                 ▼
                              frontier
                                 │
                                tools
                                 │
                                 ▼
                           Output Shunt
                                 │
                                 ▼
                              Qwen
                                 │
                                 ▼
                         summary + raw ID
                                 │
                                 ▼
                           read_output()
```

Alrededor de todo:

```text
path verification
provider-aware shunt
telemetry
shadow cache
per-repo cloud policy
```

---

# 17. Orden final que recomiendo

## Sprint 1 — eliminar problemas conocidos

```text
1. L0 routing con grep/glob
2. Ollama KEEP_ALIVE
3. path verification
```

## Sprint 2 — proteger outputs

```text
4. Output Shunt para pytest
5. Output Shunt para git diff
6. read_output con acceso limitado
```

## Sprint 3 — medir

```text
7. benchmark V1 vs V2
8. shunt-stats
9. shadow cache
10. benchmark orchestrator:
      Opus
      Sonnet
      Gemini / candidato
```

## Sprint 4 — experimentos

```text
11. benchmark multi-batch
12. decidir threshold L1.5
13. implementar L1.5 sólo si aporta
14. decidir cache según hit rate
15. benchmark local-writer
16. estudiar 64k sólo si hay necesidad real
```

## Sprint 5 — estabilización

```text
17. uso real 1-2 semanas
18. revisar telemetría
19. ajustar thresholds
20. globalizar
```

---

# 18. Qué implementaría ahora y qué sólo mediría

## Implementar

```text
L0 routing                    YES
OLLAMA_KEEP_ALIVE             YES
path verification             YES
Output Shunt                  YES
read_output                   YES
shunt-stats                   YES
shadow cache                  YES
```

## Medir primero

```text
cache real                    EXPERIMENT
L1.5 threshold                EXPERIMENT
orchestrator alternativo      EXPERIMENT
local-writer                  EXPERIMENT
64k local                     EXPERIMENT
```

---

# 19. Cosas que mantendría fuera

Coincido totalmente en no añadir ahora:

```text
RAG
embeddings
vector DB
LangChain
LangGraph
CrewAI
AutoGen
LiteLLM
servicio propio
Redis
70B local
```

También coincido con esta regla:

```text
no crear un agente por cada modelo disponible
```

Los modelos son una implementación.

No deberían convertirse automáticamente en conceptos visibles de la arquitectura.

La interfaz cognitiva para el orchestrator debe seguir siendo pequeña.

---

# 20. Conclusión

`PLAN_V2.md` es mejor que la propuesta V2 anterior.

La principal mejora conceptual es:

```text
menos "construyamos esto porque parece útil"
más "midamos primero si realmente ocurre"
```

Eso es exactamente lo que necesita el proyecto en esta fase.

Adoptaría prácticamente toda la propuesta con dos modificaciones:

```text
1. >=3 batches NO es todavía una regla.
   Debe salir de un benchmark específico.

2. L1.5 cloud debe estar protegido por
   una política explícita por repositorio,
   desactivada por defecto.
```

Y añadiría una tercera recomendación operativa:

```text
benchmark del orchestrator alternativo:
hacerlo cuanto antes,
pero mantener el cambio de modelo manual
hasta tener datos de uso real.
```

Con esos ajustes, éste es el plan V2 que recomiendo ejecutar.

La V1 ya demostró que la arquitectura funciona.

La V2 debe centrarse ahora en:

```text
fiabilidad
menos latencia
menos ruido de contexto
mejor control de costes
menos decisiones probabilísticas
```

sin convertir el sistema en una plataforma de agentes innecesariamente compleja.
