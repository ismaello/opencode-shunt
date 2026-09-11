# Próximas mejoras

Ordenadas por lo que aportarían frente a lo que cuestan. Cada una dice qué
evidencia la justifica, porque varias de las que parecían obvias resultaron no
hacer falta cuando fui a mirar los datos.

---

## Descartado tras medirlo: unificar los dos umbrales de decisión

**Conclusión: no vale la pena, y la comprobación salió gratis.** Se queda escrito
porque la hipótesis era convincente, porque volverá a parecer buena idea, y
porque la condición bajo la que sí merecería la pena está identificada.

El premio, reproduciendo toda la telemetría real contra los dos umbrales:

| | |
|---|---|
| Lecturas en la banda | **5 de 81** (6%), 60.486 bytes |
| Concentradas en | 3 sesiones, dos de ellas las más caras del repo |
| Ahorro modelado, caso mejor | **$0,0346** en una sesión de $0,4156, un **8,3%** |
| Ahorro modelado, todo el histórico | **$0,1136** |
| En `testcode` (orquestador Gemini Pro) | **cero** lecturas en la banda |

Ocho por ciento no se puede medir aquí. El propio `bench.py` documenta que el
brazo de control de un A/B de sesiones varió un **146%** entre ejecuciones,
porque el modelo elige estrategia distinta cada vez. Gastar $1 o $3 en un A/B
para estimar un efecto del 8% produce un número indistinguible del ruido.

Y la asimetría decide: el beneficio es **modelado**, mientras que el coste de
equivocarse con el bloqueo está **medido en dinero real**, +84% en una tarea.
Un 8% modelado no justifica arriesgar un 84% medido.

**Cuándo cambiaría esto.** El ahorro depende sobre todo de cuántos turnos
sobrevive el contenido en contexto, que en tu uso medido son 2 porque trabajas
casi todo con `opencode run` de un tiro:

| Turnos en contexto | Ahorro modelado | % de la sesión |
|---:|---:|---:|
| 2 (tu uso medido) | $0,0346 | 8,3% |
| 5 | $0,0454 | 10,9% |
| 12 | $0,0708 | 17,0% |
| 20 (sesión interactiva larga) | $0,0998 | 24,0% |

Si el uso pasa a sesiones interactivas largas, esto vuelve a la mesa con un 24%
detrás, que ya sí es medible. Hasta entonces, no.

De paso, esto explica por qué la constante de 400 líneas no ha hecho daño pese a
estar cuatro veces por encima del suelo económico: en la práctica casi ningún
fichero cae en la banda entre los dos.

---

## El diagnóstico original, que llevó a lo anterior

Encontrado al instalar el sistema en un repositorio de verdad (11.235 líneas) y
lanzar una tarea normal: «explícame el flujo de un run». Coste $0,4156, contra
una media de $0,1214 en ese repo. El sistema no delegó nada, y no por un fallo:
cada decisión que tomó fue correcta según su propia regla.

Lo que pasó, según la telemetría de esa sesión:

- Leyó `runs.py` (12.027 bytes) y `workflows.py` (12.144). Ambos aprobados como
  `allow-small`.
- Intentó `bulk_read` sobre 3 ficheros que sumaban 3.373 bytes. Rechazado por
  pequeño, y **con razón**: el suelo económico eran 9.931 bytes y delegar eso
  habría perdido $0,0107.
- Acabó leyendo 10 ficheros distintos, **39.904 bytes en total**, ninguno lo
  bastante grande por separado para que nadie objetara.

La causa es que los dos umbrales del sistema se calculan de forma distinta:

| Decisión | Umbral | De dónde sale |
|---|---|---|
| ¿Bloqueo esta lectura? | 400 líneas / 40.960 bytes | constante, escrita a mano |
| ¿Merece la pena delegar? | 9.931 bytes en este repo | calculado con los precios y tus hábitos |

Hay una banda de 10 KB a 40 KB donde delegar sale a cuenta y nada empuja al
modelo a delegar. Los dos ficheros de 12 KB caían justo en el centro de esa
banda, y diez de ellos sumaron 40 KB de contexto caro. `lib/economics.ts` existe
precisamente para esto y el shunt de lecturas no lo usa.

De los 39.904 bytes de esa sesión, solo 24.171 estaban de verdad por encima del
suelo económico: el resto eran ficheros que tampoco merecía la pena delegar. Ese
matiz es el que convirtió la hipótesis en el resultado negativo de arriba.

**El método se quedó, y ahora es un comando:** `shunt replay`. Reproducir la
telemetría histórica contra un umbral alternativo contesta «¿cuánto habría
cambiado esto?» por cero dólares y sin varianza, y es la alternativa sensata a un
A/B cuando el efecto buscado es menor que el ruido del modelo. Reporta un techo
modelado, dice que lo es, e incluye la tabla de sensibilidad a los turnos.

Trae una regla dentro: si el efecto esperado queda por debajo del 146% de
varianza medida en un A/B de dos brazos, se niega a recomendar el A/B y lo dice
con esas palabras. Que es exactamente lo que habría hecho falta antes de las
cuatro iteraciones de la revisión que aparecen en
[RESULTADOS_PRUEBA_REAL.md](RESULTADOS_PRUEBA_REAL.md).

---

## Lo que ya se midió, y cómo reordena la lista

Estas cifras son el suelo, no el techo: salen de un repositorio de 6.453 líneas
con un máximo de 547 por fichero.

**Por operación** (telemetría, determinista):

| Operación | Ahorro mediano | Muestra |
|---|---|---|
| `bulk_read` | 88% del contenido no entra en contexto | 7 llamadas |
| Resumen de salidas | 92% | 5 llamadas |
| `delegate_write` | 76% de tokens de salida | 3 llamadas |
| `delegate_edit` | 78% de tokens de salida | 1 caso, 21 sitios |

**Por sesión** (A/B con el shunt encendido y apagado): 22%.

Que la cifra por sesión sea muy inferior a la por operación no es una
contradicción, y entenderlo es lo que ordena esta lista: la primera mide lo que
se ahorra *del contenido*, la segunda incluye los prompts, el razonamiento del
orquestador y los turnos que la propia delegación añade.

**Y el desglose de la factura**, sobre 60 sesiones reales de Opus, es lo que más
cambió mis prioridades:

| Concepto | % de la factura |
|---|---|
| Escrituras de caché (contenido entrando por primera vez) | 46,6% |
| Tokens de salida | 34,1% |
| Lecturas de caché (reenvío en cada turno) | 19,3% |
| Entrada sin cachear | ~0% |

Tres cosas se siguen de aquí. La primera, que el shunt de lectura ataca los dos
bloques de caché, que son el 66% de la factura. La segunda, que **el 34% de
salida tiene un techo duro** que solo `delegate_write` y `delegate_edit` tocan, y
solo en su parte mecánica: el razonamiento del orquestador es intocable, y debe
serlo, porque es lo que se está pagando. La tercera, que la lectura de caché en
cada turno es la razón por la que delegar ficheros pequeños *pierde* dinero, que
es lo que ahora calcula el suelo económico.

---

## Prioridad alta

### 1. Medición pasiva en producción

**Por qué es la primera.** Todo lo anterior son benchmarks o telemetría por
operación. Ninguno responde a la pregunta que importa: *¿cuánto me ahorró la
semana pasada trabajando de verdad?* El benchmark es un proxy caro (40 minutos y
dinero real de API) y sus preguntas son las que yo elegí, no las que surgen
trabajando.

**Estado.** `shunt report` ya existe y cruza la telemetría con la contabilidad
interna de OpenCode. Lo que falta es tiempo de uso real que observar. Reporta un
rango en vez de un número porque el límite superior asume que el contenido se
habría quedado en contexto, y eso es un contrafactual, no una medida.

**Qué falta.** Nada de código. Usarlo unas semanas y comparar con lo que dice el
proveedor.

### 2. Tests de las funciones puras que quedan sin cubrir

**Estado.** De once funciones puras del runtime, tres tienen suites propias
(`coverage`, `economics`, `guards`, 80 comprobaciones). Faltan las de
`verify-paths.ts` y `output-shunt.ts`.

**Por qué importa.** Son exactamente el tipo de código cuyo fallo es silencioso.
Si la extracción determinista del resumen de salidas deja de capturar la línea
del error, el resumen sigue pareciendo correcto y la información crítica
desaparece sin que nada avise.

### 3. Que `allowedProviders` obligue también al orquestador

**El hueco.** Está aplicado donde se resuelven los perfiles de worker, que cubre
al lector y al escritor. El modelo del orquestador lo elige OpenCode y no pasa
por ahí, así que marcar un repositorio como "no sale de esta máquina" **no
impide** que un orquestador en la nube lo vea.

**Estado.** `shunt doctor` avisa cuando estás en ese estado, y `shunt config`
avisa al elegirlo. Pero avisar no es impedir.

**Qué costaría.** El plugin conoce el proveedor de la sesión en `chat.params`,
así que la comprobación es barata. Lo que no es barato es decidir qué hacer al
detectarlo: bloquear al orquestador entero deja el repositorio inservible, y es
la ruta de código más delicada que tenemos.

---

## Prioridad media

### 4. Latencia: el problema no es el que yo dije

Enuncié esto como "paralelizar lotes". **Fui a mirar y los lotes no existen: 0
de 35 llamadas reales se dividieron nunca**, porque el presupuesto de contexto
del worker (1M en Gemini) es enorme frente a lecturas de 5-54 KB. Ese código no
se ejecutaría jamás.

Midiendo de verdad, con el mismo prompt de 24 KB: pedir 30 tokens tarda 1,03s y
pedir 1.500 tarda 5,19s. O sea **la latencia la manda lo que el worker escribe,
no lo que lee**: unos 2,5s fijos más 5 ms por token generado.

Eso deja dos palancas reales y una descartada:

- **Paralelizar por fichero** daría ~2x, porque la generación paraleliza entre
  peticiones independientes. Pero destruye la síntesis entre ficheros, que es el
  caso de uso principal de `bulk_read` ("cómo se orquesta esto entre el workflow,
  sus activities y la persistencia"). Cuatro resúmenes independientes no ven las
  conexiones. **Por eso no está hecho**: la velocidad no compensa perder aquello
  para lo que existe la herramienta.
- **Acortar la salida** es lineal y no cuesta calidad si el formato está inflado.
  Sin medir aún cuánto hay de relleno.
- **En una sola GPU es una incógnita.** La teoría tira en dos direcciones: el
  decodificado está limitado por ancho de banda, así que atender varias
  secuencias amortiza la carga de los pesos; pero comparten cómputo y caché KV.
  Solo se sabe midiendo, y es la decisión de si un Qwen compartido en una máquina
  de empresa rinde con varias peticiones a la vez.

### 5. La caché local: tres cosas distintas con el mismo nombre

Esto se despachó una vez en un párrafo y merecía más, porque "caché local"
significa tres cosas en este sistema y solo una de ellas es la que la gente
pregunta.

**(a) Caché de respuestas del worker — medida, y no vale la pena.**

La idea: la misma pregunta sobre los mismos ficheros sin cambios devuelve el
resumen guardado. Clave: hash del contenido más la pregunta.

Esto ya no es una intuición. Sobre 56 llamadas reales con la pregunta
registrada:

| | |
|---|---|
| Preguntas distintas | 52 de 56 |
| Repeticiones exactas de pregunta | 4 (**7%**) |
| Coste total del worker | $0,2351 |
| Lo que evitaría una caché **perfecta** | $0,0137 — **6% del worker** |
| Sobre la factura total | **0,11%** |

Y el 7% es un **techo generoso por dos motivos**: un acierto real necesita la
misma pregunta *y* los ficheros intactos, que es más estricto; y de esas cuatro
repeticiones, tres salieron de pruebas A/B en las que lancé la misma pregunta
dos veces a propósito. En uso normal la tasa es todavía menor.

El error de razonamiento que había detrás merece quedar escrito: yo estaba
pensando que la caché ahorraba "la llamada entera". Y la ahorra — pero **la
llamada entera es el 2% de la factura**. El resumen entra en el contexto caro
exactamente igual venga de la caché o del worker, así que el 98% restante no se
toca. Es una mejora de **latencia** (3–14 segundos por acierto) disfrazada de
ahorro, y con un 7% de aciertos tampoco es gran cosa como mejora de latencia.

**Veredicto: descartada como medida de ahorro.** Si algún día se construye,
será por latencia y habrá que justificarla con eso.

**(b) La caché del proveedor — es el 66% de la factura, y ya se está atacando.**

Aquí es donde está el dinero, y nunca se planteó como "caché" por descuido de
lenguaje. El desglose de la factura es 46,6% escrituras de caché y 19,3%
lecturas: **dos tercios del gasto son caché**, la de Anthropic o Google, no una
nuestra.

No se puede construir, porque es del proveedor. Pero se influye en ella de dos
maneras, y las dos están cubiertas:

- **Cada KB que se mantiene fuera del contexto es una escritura de caché que no
  se paga, más una lectura por cada turno siguiente.** Eso *es* el mecanismo de
  ahorro del sistema entero. Decir que no hemos tocado la caché era falso: el
  suelo económico en `lib/economics.ts` está literalmente calculado sobre los
  precios de escritura y lectura de caché del proveedor.
- **No romperla.** Un plugin que modifique el prefijo del prompt en cada
  petición invalidaría la caché y multiplicaría la factura en silencio.
  Verificado: el hook `chat.params` de `plugins/shunt.ts` **solo lee** — guarda
  el agente y el modelo de la sesión en un Map y no devuelve parámetros
  modificados. Los bloqueos de lectura añaden un mensaje de error al final de la
  conversación, que *añade* al prefijo en vez de alterarlo, así que tampoco
  invalidan nada.

**(c) Un mapa del repositorio — el hueco de verdad.**

Esto sí falta, y es lo que separa este proyecto del Portal de Spotify. Hoy
**cada sesión nueva redescubre el repositorio desde cero**: vuelve a correr el
explorador, vuelve a resumir los mismos ficheros, vuelve a pagar la escritura
de caché del mismo contenido. Un mapa persistente —qué módulos hay, qué hace
cada uno, dónde están las fronteras— atacaría los turnos de exploración y esas
escrituras repetidas, que es el bloque grande.

Por qué no está hecho: tiene un problema difícil que no es la construcción sino
**el envejecimiento**. Un mapa desactualizado es peor que no tener mapa, porque
manda al modelo al sitio equivocado con confianza en vez de mandarlo a mirar.
Resolver eso (invalidación por hash de contenido, o regeneración por cambios en
git) es el trabajo real, y es un proyecto, no una mejora.

**De las tres, esta es la única que queda pendiente de verdad.**

### 6. Un registro de repositorios instalados

`shunt update` funciona dentro de un repositorio, como git. Eso significa que
**nada te avisa de que otros ocho repos siguen con una versión vieja**. Un
registro en `~/.config` lo resolvería, a cambio de mantenerlo.

Elegí lo primero por simplicidad sabiendo lo que se pierde. Si esto se usa en
muchos proyectos a la vez, la cuenta cambia.

---

## Prioridad baja, o descartadas

### 0. Lo que encontró montar un proyecto de verdad

Cuatro defectos que ninguna suite de tests iba a encontrar, porque los cuatro
consistían en que el sistema funcionaba y cobraba de más:

**El resumen que era más largo que el código.** Una pregunta amplia sobre 17,7 KB
de fuente devolvió 27,5 KB de prosa. `bulk_read` prometía comprimir y no había
nada que lo obligara, así que la delegación pagó una llamada al worker y un
viaje de ida y vuelta para meter *más* en el contexto caro del que habría metido
leer los ficheros. Ahora la respuesta tiene un presupuesto de caracteres en el
prompt y un tope del 25% del original, con una segunda pasada barata si se
excede. La misma revisión pasó de $0,2549 a $0,2228, con el resumen al 81%.

**`shunt update --force` se comía la configuración.** `shunt.json` se enviaba
como si fuera código, así que actualizar lo reemplazaba por la plantilla. Eso
dejaba `writerProfile` sin poner, lo que mandaba las delegaciones a un perfil
que nadie eligió, lo que hacía que Opus escribiera todo él en silencio. Ahora la
configuración se instala una vez y no se toca nunca más.

**El asistente ofrecía modelos que no existían.** Elegía del catálogo sin mirar
qué tiene Ollama pulled de verdad, y `doctor` daba el visto bueno porque
comprobaba que el servidor respondía, no que el modelo estuviera. Cada
delegación moría con un 404 y el trabajo volvía al modelo caro. Ahora el
asistente ofrece lo que hay y `doctor` verifica el modelo, no el puerto.

**Bloquear siempre sale caro.** El primer intento de impedir la relectura de
ficheros ya resumidos bloqueaba todas: cinco negativas compraron cinco turnos
extra para dejar fuera unos pocos kilobytes, y la revisión subió de $0,25 a
$0,47. Una negativa cuesta un turno, y un turno es lo caro en todo este sistema.
Ahora el bloqueo tiene su propio umbral económico, sacado de la misma
aritmética que el suelo de delegación.

La lección que se repite: **todo mecanismo que gasta un turno tiene que
justificar ese turno**, y ninguno de estos fallos daba un error.

### 7. `delegate_edit` sobre código de negocio — hecho

Estaba aquí a la espera de "confianza acumulada". La acumuló de golpe al montar
un proyecto de prueba: la misma instrucción, 26 docstrings sobre dos módulos de
negocio, costó **$0,3375 escribiéndolos Opus contra $0,2065 delegándolos**, un
39% menos y 3.733 tokens de salida menos, con los tests igual de verdes y sin
una sola línea de código alterada.

Lo que faltaba no era confianza sino distinguir dos riesgos que estaban
mezclados. `delegate_write` inventa un fichero entero que nadie lee, y para eso
la lista de tests es la correcta. `delegate_edit` cambia un fichero que ya
existe y devuelve el diff de lo que movió, después de copiarlo, limitar el
tamaño del cambio y pasarle un parser: ahí el control es la revisión, no la
allowlist. Ahora tienen defaults distintos, y por debajo hay una lista de veto
(CI, migraciones, lockfiles, el propio `.opencode/`) que ninguna configuración
puede levantar.

### 8. Escalonar el orquestador

Orquestar "busca esto, resume aquello" no necesita el modelo más caro. Un
orquestador barato para lo rutinario y el bueno para diseño reduciría el 34% de
salida por la vía de bajar el precio unitario en vez del volumen.

**Por qué no está.** Cambiar de orquestador a media sesión pierde el contexto, y
decidir *a priori* si una tarea es rutinaria es exactamente el juicio que se le
paga al modelo caro. Sin una idea mejor, es una complicación con un ahorro
incierto.

### 9. Paralelizar lotes

**Descartada por los datos**, no por dificultad. Ver el punto 4.
