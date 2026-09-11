# Montar un proyecto real con el sistema, y lo que salió mal

Aplicación FastAPI de gestión de biblioteca, creada desde cero en
`/home/ismaello/cofers/data/testcode` usando únicamente el sistema instalado con
`pip install opencode-shunt`.

Todas las cifras salen de la tabla `session` de `opencode.db` y de la telemetría
del shunt. No son estimaciones: es lo que se facturó. Reproducibles con
`shunt costs` desde ese directorio.

| | |
|---|---|
| Fecha | 10 de septiembre de 2026 |
| Orquestador | Claude Opus 4.8 (y al final Gemini 2.5 Pro, ver abajo) |
| Worker | Gemini 2.5 Flash (Vertex AI, ADC) |
| Gasto total del ejercicio | **$4,37** en 14 sesiones |
| Construir la app | $1,51 en 5 tareas |
| Código generado | 642 líneas de Python, 4/4 tests en verde |
| Defectos reales encontrados | **4**, los cuatro silenciosos |

---

## 1. El titular: delegar las ediciones mecánicas venía apagado de fábrica

La misma instrucción, ejecutada dos veces: una con el orquestador escribiendo a
mano y otra delegando en el worker. Dos tareas distintas, dos medidas
independientes.

| Tarea | A mano | Delegado | Ahorro |
|---|---:|---:|---:|
| Escribir 26 docstrings sobre lógica de negocio | $0,3375 | $0,2065 | **39%** |
| Traducir esos docstrings al inglés | $0,2989 | $0,1852 | **38%** |

Los tests siguen en verde en los cuatro casos y el worker no alteró ni una línea
de código ejecutable. La segunda medida se hizo con la configuración recién
generada por `shunt config`, sin retoques a mano.

El detalle importante es *por qué* estaba apagado: el allowlist por defecto de
`delegate_edit` era `tests/**`, así que la herramienta que más ahorra no podía
tocar el código fuente. Es el defecto 4 de más abajo.

## 2. La misma revisión, cuatro veces

La tarea «hazme una revisión completa del código» delegaba la lectura en el
worker y **después leía los seis ficheros igual**. Intenté impedirlo. Los dos
primeros intentos empeoraron la factura.

| Versión | Coste | Cache write | Resultado |
|---|---:|---:|---|
| 1. Original | $0,2549 | 12.393 | punto de partida |
| 2. Bloqueo total de la relectura | $0,4687 | 22.385 | **84% peor** |
| 3. Bloqueo con umbral económico | $0,3109 | 25.132 | sigue peor |
| 4. Tope al tamaño del resumen | $0,2228 | 11.541 | **13% mejor** |

La versión 2 es la lección de todo el ejercicio: **bloquear parece gratis y no lo
es**. Cuesta el turno que el modelo pasa recibiendo la negativa y volviéndolo a
intentar. Cinco negativas compraron cinco turnos para dejar fuera unos pocos
kilobytes, y la revisión subió un 84%.

Es la misma aritmética que el proyecto ya aplicaba para decidir si delegar,
aplicada a un sitio donde no se me había ocurrido que hiciera falta. El bloqueo
tiene ahora su propio umbral: por debajo de unos 11 KB por fichero, callarse
sale más barato que avisar.

Y la versión 4 revela que perseguía la causa equivocada. El problema no era que
el modelo releyera: era que el resumen que recibía no valía nada.

## 3. Las cinco instrucciones que construyeron la aplicación

| # | Instrucción | Coste | Salida | Qué hizo el sistema |
|---|---|---:|---:|---|
| 1 | Construir la app FastAPI | $0,5546 | 11.120 | 516 líneas de lógica de negocio, escritas por Opus. Correcto: eso no se delega |
| 2 | Explicar el flujo de préstamos | $0,1261 | 2.445 | 14 KB, bajo el umbral. Leyó acotado en vez de delegar. Decisión correcta |
| 3 | Revisión completa del código | $0,2549 | 3.672 | Delegó por su cuenta y luego leyó los 6 ficheros igual |
| 4 | Escribir y ejecutar tests | $0,2338 | 2.820 | `delegate_write` generó los tests. Los 4 pasan |
| 5 | Docstrings sobre código de negocio | $0,3375 | 6.928 | Intentó delegar, el allowlist lo bloqueó, los escribió Opus |

Total $1,5069.

**Cuánto costó el worker, corregido.** Una versión anterior de este documento
decía 0,8%, y estaba mal por un motivo que merece quedar escrito: las llamadas
al worker son HTTP directo desde nuestras herramientas, así que **no crean
sesión en OpenCode y no aparecen en `opencode.db`**. Contando solo lo que la
base de datos ve —las sesiones del subagente explorador— el worker parecía
costar $0,014. Sumando los tokens que registra nuestra propia telemetría, el
coste real del worker en este repositorio es **$0,0959, un 2,2% del gasto**.

En el repositorio real la proporción es la misma: $0,2297 de worker sobre $11,95,
un **1,9%**. Sigue siendo el margen del sistema —cuanto más trabajo cruza esa
frontera, más barata sale la sesión— pero al triple de lo que decía antes.

La lección no es el número, es de dónde salía: medir un sistema con la
contabilidad de otro deja fuera justo lo que ese otro no ve.

---

## 4. Los cuatro defectos

Ninguno daba un error. En los cuatro el sistema seguía funcionando y cobrando de
más, que es exactamente la clase de fallo que una suite de tests no encuentra.

### 4.1 El resumen salía más largo que el código

Una pregunta amplia sobre 17,7 KB de fuente devolvió **27,5 KB de prosa**.
`bulk_read` prometía comprimir y nada lo obligaba, así que la delegación pagaba
una llamada al worker y un viaje de ida y vuelta para meter *más* en el contexto
caro del que habría metido leer los ficheros directamente.

Arreglado: la respuesta lleva un presupuesto de caracteres en el propio prompt y
un tope del 25% del tamaño de la fuente, con una segunda pasada de compresión
—barata, la hace el worker— si se pasa. La misma revisión bajó a $0,2228 con el
resumen al 81% de compresión.

### 4.2 Actualizar se comía la configuración

`shunt.json` se distribuía como si fuera código, así que `shunt update --force`
lo sustituía por la plantilla. Eso dejaba `writerProfile` sin poner, lo que
mandaba las delegaciones a un perfil que nadie eligió, lo que hacía que el
orquestador escribiera todo él sin decir nada.

Lo pillé porque una edición delegada falló con un 404. Sin ese 404 habría pasado
desapercibido indefinidamente: el sistema no parecía roto, solo caro.

Arreglado: la configuración se instala una vez y no se toca jamás, ni con
`--force`. Hay un test que lo comprueba.

### 4.3 El asistente ofrecía modelos que no existían

`shunt config` elegía del catálogo sin mirar qué tiene Ollama descargado de
verdad, y `shunt doctor` daba el visto bueno porque comprobaba que el servidor
respondía, no que el modelo estuviera. Cada delegación moría con un 404 y el
trabajo volvía al modelo caro.

Arreglado: el asistente ofrece solo lo que hay descargado y `doctor` verifica el
modelo, no el puerto.

### 4.4 El allowlist por defecto apagaba `delegate_edit`

Dejaba las ediciones en `tests/**`, así que la herramienta que más ahorra no
podía tocar el código fuente. Mezclaba dos riesgos que no son el mismo:
`delegate_write` inventa un fichero que nadie lee, mientras que `delegate_edit`
devuelve un diff que sí se revisa, después de haber hecho copia de seguridad,
rechazado un cambio desproporcionado y pasado un parser por el resultado.

Arreglado: tienen defaults distintos, con una lista de veto por debajo —CI,
migraciones, lockfiles, el propio `.opencode/`— que ninguna configuración puede
levantar.

---

## 5. Cambiar de orquestador a Gemini Pro

Después del ejercicio repetí la tarea de traducción con Gemini 2.5 Pro
orquestando en lugar de Opus, con el mismo worker y sin tocar nada más que
`shunt config`.

| Orquestador | Coste | Tiempo | Resultado |
|---|---:|---:|---|
| Claude Opus 4.8 | $0,1852 | 61 s | 2 ficheros delegados, tests en verde |
| Gemini 2.5 Pro | **$0,0144** | **16 s** | idéntico |

**92% más barato y cuatro veces más rápido, con el mismo resultado.** Es una sola
tarea y es mecánica: Opus sigue siendo mejor en lo que necesita criterio, que es
justamente el rol que no se delega. Pero para trabajo mecánico bien orquestado la
diferencia no es sutil, y explica por qué las pruebas de este proyecto pasan a
hacerse con Gemini Pro.

Ese cambio destapó dos cosas más:

**El suelo de delegación se mueve con el jefe, y así debe ser.** El catálogo
tenía un precio único por proveedor, y para Google eran los de Flash. Con Pro
orquestando, el umbral se habría calculado con precios cuatro veces menores de
los reales. Ahora hay precios por modelo, y el resultado es que el mismo
repositorio delega a partir de 12,6 KB con Opus y a partir de **23,4 KB con
Gemini Pro**: delegar existe para proteger un contexto caro, y con un jefe barato
hay menos que proteger. Depende de la relación entre lo que cobra el proveedor
por meter un token en caché y por reenviarlo —Anthropic descuenta un token
cacheado un 90%, Google un 75%— y se recalcula solo.

**Un mismo proveedor puede ocupar dos roles.** Gemini Pro de jefe con Gemini
Flash de lector es una configuración normal, y es el caso que el diseño original
tenía mal: las exenciones de lectura iban por proveedor, así que eximir a Google
habría eximido también al jefe y habría apagado el sistema entero en silencio.
Van por modelo, y `doctor` falla en voz alta si el orquestador acaba en esa lista.

---

## 6. Opus contra Gemini 3.1 Pro contra Astra 6, en una tarea de criterio

Todo lo anterior es trabajo mecánico, donde delegar gana siempre. Queda la
pregunta que de verdad decide si hace falta un orquestador caro: **en una tarea
de juicio, ¿el modelo caro encuentra cosas que el barato no?**

### Montaje

Una copia del repositorio real con **tres defectos inyectados a mano** en
`services/runs.py`, un servicio multi-tenant. Del tipo que pasa una revisión
humana distraída:

| | Defecto | Por qué es difícil |
|---|---|---|
| **D1** | `signal_run` usa `client.get_workflow_handle` directamente, saltándose `_authorized_handle` y con él la comprobación de tenant | Se ve comparando con las otras cinco funciones del fichero, no leyendo esta |
| **D2** | `input_overrides` se aplica **después** de `validate_inputs`, así que los overrides no se validan | Las dos líneas son correctas; lo que está mal es el orden |
| **D3** | En `_authorized_handle` la comprobación de tenant quedó dentro del `try`, así que un `RPCError` que no sea NOT_FOUND se traga y devuelve un handle sin verificar | Exige razonar sobre una ruta de error que casi nunca se ejecuta |

Mismo prompt para los tres, sin pistas: *"busca fallos reales... para cada fallo
di el sitio exacto, el mecanismo y la consecuencia. Si no encuentras nada,
dilo."* Mismo worker (Gemini Flash), mismo shunt, una sola pasada por brazo.

### Resultado

| | Opus 4.8 | Gemini 3.1 Pro | GPT-6 Astra |
|---|---|---|---|
| **Defectos encontrados** | **3 de 3** | **2 de 3** | **3 de 3** |
| Cuáles se dejó | — | D3 (la ruta de error) | — |
| Falsos positivos | 0 | 0 | 0 |
| De propina | 2 defectos preexistentes reales | — | razonamiento entre ficheros |
| Coste | $0,3883 | **$0,1692** | $0,6112 |
| Tiempo | 95 s | 88 s | 85 s |
| Precio | $5 / $25 por millón | $2 / $12 | **$10 / $50** |

**Gemini 3.1 Pro encontró D1 y D2 y se dejó D3**, el único que exige razonar
sobre una ruta de error que casi nunca se ejecuta. No afirmó que el resto
estuviera limpio, que es la diferencia importante: informó de lo que encontró
sin firmar lo que no miró.

### Nota sobre una versión anterior de esta tabla

La primera vez la corrí con **Gemini 2.5 Pro**, que estaba desactualizado: 3.1
Pro ya existía. El resultado con 2.5 fue **1 de 3**, y además cerraba con *"el
resto del servicio está bien diseñado desde la perspectiva de seguridad
multi-inquilino"* — un falso negativo dicho con confianza.

Vale la pena dejarlo escrito por dos motivos. Uno, porque la diferencia entre
generaciones no es cosmética: 1 de 3 con un falso "está limpio" contra 2 de 3
sin él. Dos, porque el error de partida era mío y era estructural: el catálogo
de este proyecto tenía los modelos escritos a mano y se había quedado dos
generaciones atrás en **todos** los proveedores. Eso se arregló leyendo la tabla
de modelos de OpenCode en vez de competir con ella, y de paso apareció que el
precio de caché que yo tenía puesto para Gemini Pro estaba mal por un factor de
2,5, lo que movía su suelo de delegación de 11,7 KB a 23,4 KB.

**Astra encontró los tres y fue más lejos que Opus en un punto.** No se quedó en
el fichero que se le dio: para D1 buscó qué señales existen realmente y encontró
que `ApprovalGateWorkflow` acepta `approve` y `reject`
(`worker/workflows.py:154-160`), convirtiendo "se pueden enviar señales ajenas"
en "se puede aprobar el flujo de otro cliente". Para D2 dio una entrada concreta
que rompe (`{"delay_seconds": "not-an-int"}`) y dónde revienta
(`worker/workflows.py:45`). **Verifiqué las tres citas a mano: exactas.**

### Qué se concluye, y qué no

Es **una tarea y una pasada por brazo**: no es una medida de capacidad. Y con
los modelos actuales la separación es más estrecha de lo que parecía: 3 / 2 / 3
sobre 3, no 3 / 1 / 3.

Aun así la tesis del proyecto se sostiene, y de hecho queda mejor planteada. El
trabajo mecánico lo hace igual de bien un modelo 50 veces más barato, con 78-88%
de ahorro medido. El de criterio se separa, pero se separa **en el defecto
difícil**: los tres vieron la autorización que falta, el más barato no vio la
ruta de error. **El sistema existe para poder pagar el caro donde importa sin
pagarlo donde no**, y el margen está justo ahí.

Sobre el coste: Astra costó **un 57% más que Opus para el mismo marcador**,
siendo el modelo más caro del catálogo. Y Gemini 3.1 Pro sacó 2 de 3 por
**$0,1692, un 56% menos que Opus**. Si lo que haces es sobre todo revisión
mecánica con algún juicio ocasional, 3.1 Pro es defendible; si la revisión de
seguridad es el trabajo, el tercer defecto es exactamente el que quieres que
alguien encuentre.

### El fallo que esta prueba destapó, que no era de los modelos

El brazo de Gemini registró **cero eventos de telemetría**. Según nuestro propio
README eso es indistinguible de una instalación rota, así que lo perseguí.

La causa: **OpenCode pasó `worktree: "/"` al plugin.** Con eso
`path.resolve("/", "src/app.py")` da `/src/app.py`, que no existe. El `stat`
fallaba y el hook hacía `return` sin decir nada.

El daño iba mucho más allá de las lecturas perdidas: `loadExempt` y `loadConfig`
leen del mismo raíz, así que **la lista de exentos venía vacía y la aritmética
económica caía a valores por defecto de ningún repositorio**. El shunt estaba
cargado, pasaba todas las comprobaciones de `doctor`, y no hacía nada.

Lo que lo mantuvo escondido es una asimetría entre modelos que nadie habría
adivinado. Sobre 193 lecturas registradas:

| | Lecturas | Ruta que pasa el modelo |
|---|---|---|
| Claude Opus | 171 | **absoluta** — resuelve bien desde cualquier raíz |
| GPT-6 Astra | 11 | **absoluta** |
| Gemini (Pro y Flash) | 9 | **relativa** — depende de que el worktree sea correcto |

Es decir: **la misma instalación funciona o no según quién orqueste.** Todas las
cifras de este documento son de sesiones con Opus y rutas absolutas, así que no
están afectadas.

Tres arreglos, en `3.3.0`:

1. `lib/worktree.ts` valida la raíz en lugar de confiarse: solo se acepta la que
   contenga el `.opencode` donde estamos instalados, y si no, se recurre al cwd.
   Con siete tests, porque el fallo no daba ni un síntoma.
2. Los dos `return` mudos de la rama de lectura ahora registran `measure-failed`
   y `not-a-file` con la ruta resuelta. Eso permitió diagnosticarlo en una sola
   pasada de $0,01.
3. El *fail open* de sesión desconocida registra `session-unknown`, y `doctor`
   avisa si pasa del 20% de las lecturas.

La lección es incómoda y merece quedar escrita: llevo todo el proyecto diciendo
que el peligro son los fallos silenciosos, y el peor de todos estaba dentro del
propio guardia. Lo encontró una prueba que buscaba otra cosa.

## 7. Lo que queda abierto

**La delegación aditiva sigue ahí en repos de ficheros pequeños.** El orquestador
resume y después lee de todas formas, y por debajo de 11 KB por fichero nadie se
lo impide, porque impedírselo costaría más de lo que ahorra. Con el resumen ya
acotado el daño es menor, pero no es cero. La salida probablemente no sea
bloquear más, sino que el resumen cite rangos tan precisos que releer deje de
apetecer.

**El ahorro es de entrada, sobre todo.** La salida es un tercio de la factura y
`delegate_write` y `delegate_edit` solo alcanzan la parte de ella que es
mecánica. El razonamiento del orquestador queda intacto, y debe quedarse así:
eso es lo que se está pagando.

**Una tarea es un dato.** Las dos medidas A/B de ediciones son consistentes entre
sí (38% y 39%), y eso da cierta confianza. La comparación de orquestadores es una
sola ejecución y hay que tratarla como tal.

## Cómo reproducir estas cifras

```bash
cd /home/ismaello/cofers/data/testcode
shunt costs              # escribe shunt-costes.md con el desglose de la factura
shunt stats              # ahorro por operación, desde la telemetría
shunt doctor             # comprobar que sigue bien montado
```
