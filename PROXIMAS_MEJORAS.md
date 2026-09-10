# Próximas mejoras

Ordenadas por lo que aportarían frente a lo que cuestan. Las de arriba las haría
esta semana; las de abajo esperarían a tener datos que las justifiquen.

---

## Lo que dijo el benchmark, y qué implica

Ya está ejecutado. Resultado agregado: **48,6% menos tokens ingeridos**, con 60,3%
en el caso multi-fichero y 0% en la exploración de un fichero pequeño.

Tres conclusiones que reordenan esta lista:

1. **El sistema funciona donde se esperaba y no estorba donde no.** El Test C no
   ahorró porque el fichero relevante tenía 162 líneas, por debajo del umbral. El
   shunt no se activó, que es el comportamiento correcto.
2. **El ahorro en dinero va muy por detrás del ahorro en tokens** (43% de tokens
   pero 9% de coste en el Test A), porque los tokens de salida dominan la factura
   y el shunt no los reduce. Cualquier mejora futura que quiera notarse en el
   recibo tiene que atacar la salida, no la entrada.
3. **Cuesta entre 6 y 7 segundos por consulta.** Es asumible, pero las mejoras que
   añadan más latencia local tienen que justificarse.

Queda pendiente repetirlo en un repositorio grande. `automation-cofers-engine`
tiene 6.453 líneas con un máximo de 547 por fichero, así que estas cifras son el
suelo, no el techo.

---

## Prioridad alta

### 1. Verificar las rutas que cita el modelo local

**Problema observado.** El explorador citó `reconciliation/reconciliation_activities.py`
cuando el fichero estaba en `worker/`. Nombre bien, directorio inventado. Ahora mismo
lo único que lo contiene es una instrucción en el prompt, y las instrucciones se
incumplen.

**Solución.** Post-procesar la respuesta antes de devolverla: extraer con una
expresión regular todo lo que parezca una ruta, comprobar contra el sistema de
ficheros cuáles existen, y para las que no, buscar el fichero por su nombre y
corregir o marcar. Añadir al final:

```
PATH CHECK: 3 verified, 1 corrected (reconciliation/x.py -> worker/x.py)
```

**Por qué primero.** Convierte el fallo más probable del modelo local en algo
imposible en vez de improbable, es determinista, y son unas 40 líneas en
`bulk-read.ts`. Mejor relación esfuerzo/beneficio de toda la lista.

### 2. Caché de `bulk_read`

**Problema.** Durante una sesión larga, Claude pregunta varias veces por los mismos
ficheros. Cada llamada vuelve a procesar las mismas 16.000 tokens y cuesta los
mismos 7,6 segundos.

**Solución.** Clave de caché = hash de la pregunta + rutas + `mtime` y tamaño de
cada fichero. Si el fichero no ha cambiado y la pregunta es idéntica, devolver el
resultado guardado. Invalidación automática al editar.

**Beneficio.** Respuesta instantánea en el caso repetido, que es frecuente. Cuesta
poco y no tiene riesgo.

### 3. Output shunt: logs, tests y diffs

Es la Fase 9 del plan original, el mayor sumidero de tokens que queda sin atacar,
y **el benchmark lo ha ascendido de prioridad**: es la única mejora de la lista que
puede reducir tokens de salida además de entrada, que es donde está el dinero.
`bulk_read` protege de leer código; nada protege todavía de esto:

```
pytest con 5.000 líneas de salida
git diff enorme
docker logs / kubectl logs
alembic upgrade con salida verbosa
```

**Solución.** Hook `tool.execute.after`, que en la versión 1.18.29 sí permite
modificar `output.output`. Si la salida supera N caracteres, guardarla íntegra en
disco, resumirla con Qwen y entregarle a Claude el resumen más la ruta del fichero
completo por si quiere consultarlo.

Un detalle que marca la diferencia: para salidas de tests, el resumen debe conservar
**literalmente** los mensajes de error y los nombres de los tests fallidos, y
resumir sólo el ruido. Un resumen que parafrasee un stack trace es inútil.

**Por qué no antes.** El código primero, porque es donde está el volumen previsible.
Los logs son más irregulares y el resumen es más delicado de acertar.

---

## Prioridad media

### 4. Nivel L0: búsqueda determinista antes que el LLM

La sección 18 del plan original propone tres niveles, y ahora mismo sólo hay dos:

```
L0   ripgrep / shell            coste 0, exacto
L1   Qwen local                 coste 0 en dinero, no exacto
L2   Claude                     coste alto, razona
```

Para una pregunta como "¿dónde aparece `transaction_checksum`?", ni siquiera hace
falta un LLM. Una tool `code_search(pattern)` que ejecute `ripgrep` y devuelva
coincidencias con contexto es más rápida, más barata y **más fiable** que Qwen,
porque no puede inventarse una ruta.

**Regla de reparto:** si la pregunta es "dónde está X" → L0. Si es "cómo funciona X"
→ L1. Si es "qué deberíamos hacer con X" → L2.

### 5. Contexto de 64k con KV cache cuantizado

Ahora mismo el techo son 32k porque a esa cifra el modelo ocupa 21 de 24,5 GB y
sólo quedan 1.535 MiB libres. Con estas dos opciones el KV cache ocupa la mitad:

```bash
sudo systemctl edit ollama
# Environment="OLLAMA_FLASH_ATTENTION=1"
# Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
# Environment="OLLAMA_KEEP_ALIVE=30m"
```

**Cuidado.** Más contexto no es automáticamente mejor: los modelos pierden precisión
en la parte central de contextos largos. Puede ser preferible seguir troceando en
consultas de 32k. Hay que medirlo con el test de precisión de citas antes de
adoptarlo, y `KEEP_ALIVE` conviene ponerlo igualmente porque ahorra 5 segundos de
recarga en cada llamada espaciada.

### 6. Reconsiderar `local-writer` — sólo con datos

Se dejó fuera del MVP a propósito. El razonamiento: `qwen3-coder:30b` es un MoE con
unos 3B de parámetros activos, y si genera un diff mediocre, Claude gasta tokens
leyéndolo, más tokens corrigiéndolo, y el balance sale negativo.

**Cómo decidirlo bien.** No por intuición. Darle tres tareas mecánicas reales
(tests siguiendo un patrón existente, fixtures, un rename repetitivo) y medir tres
cosas: tokens que Claude gasta revisando, número de correcciones necesarias, y
tiempo total frente a que lo hiciera Claude. Si necesita más de una corrección de
media, no compensa.

Si se adopta, con estas restricciones: `permission.bash` en `ask` salvo lista blanca,
prohibido `git push`, `git reset --hard`, `git clean`, `rm -rf` y `sudo`, y revisión
obligatoria del diff por el orchestrator antes de dar nada por bueno.

### 7. Globalizar a `~/.config/opencode/`

La Fase 8 del plan. Mover agentes, tool y plugin a la configuración global para que
funcionen en todos los repositorios, dejando por proyecto sólo los umbrales y las
excepciones.

**Requisito previo:** haberlo usado un par de semanas en un repositorio real. Un
shunt global mal calibrado te estorba en todos los proyectos a la vez, y el coste
de descubrirlo tarde es alto.

---

## Prioridad baja

### 8. Bash guard más robusto

El actual detecta `cat`, `less`, `more`, `head`, `tail`, `nl`, `strings` partiendo
el comando por `|`, `&&` y `;`. Se le escapan `python -c "print(open(f).read())"`,
`awk`, `sed -n '1,5000p'`, sustituciones de comandos y expansiones de variables.

**No merece la pena perseguirlos todos.** El shunt protege contra el descuido, no
contra un adversario, y Claude no está intentando burlarlo. Basta con ir añadiendo
los casos reales que aparezcan en la telemetría.

### 9. Umbral adaptativo

En lugar de 400 líneas fijas, ajustar según lo lleno que esté el contexto: permisivo
al empezar la sesión, estricto cuando queda poco. Interesante en teoría, pero un
umbral que cambia solo es difícil de razonar cuando algo falla. Primero hay que ver
si 400 es un buen número.

### 10. Resumen de telemetría

Un comando `shunt-stats` que lea el JSONL y muestre líneas evitadas, tokens locales
procesados, bloqueos por semana y ahorro acumulado estimado. Útil para saber si
sigue mereciendo la pena dentro de tres meses, cuando ya nadie se acuerde de por
qué está esto aquí.

---

## Lo que NO haría

**Embeddings, vector database o RAG sobre el repositorio.** Es lo primero que
sugiere todo el mundo y resuelve un problema que no tenemos. `ripgrep` más un modelo
local ya localiza código con precisión, sin índice que mantener, sin desincronización
y sin resultados aproximados. Un RAG sobre código introduce una capa de "documentos
parecidos" donde lo que quieres es una coincidencia exacta.

**LiteLLM, LangChain, LangGraph o un servicio propio.** El sistema entero son tres
ficheros y llamadas HTTP a `localhost`. Cualquier framework añade una capa de
indirección para resolver un problema de orquestación que OpenCode ya resuelve.

**Paralelizar los lotes de `bulk_read`.** Suena a mejora fácil, pero Ollama serializa
las peticiones sobre un mismo modelo por defecto, y forzar paralelismo en una sola
GPU compite por la misma VRAM. Ganancia dudosa a cambio de riesgo de offload a CPU,
que es exactamente lo que llevamos todo el proyecto evitando.

**Un modelo local más grande.** Un 70B no cabe en 24 GB con contexto útil. Y el
cuello de botella no es la capacidad de razonamiento del modelo local, porque no
queremos que razone: queremos que lea rápido y cite bien.

---

## Orden sugerido

```
benchmark A/B  ──►  verificación de rutas  ──►  caché
       │
       ▼
  output shunt  ──►  nivel L0  ──►  decidir local-writer con datos
       │
       ▼
   globalizar
```

Todo lo demás, sólo si la telemetría dice que hace falta.
