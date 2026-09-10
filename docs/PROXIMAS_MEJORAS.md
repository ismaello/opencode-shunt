# Próximas mejoras

Ordenadas por lo que aportarían frente a lo que cuestan. Cada una dice qué
evidencia la justifica, porque varias de las que parecían obvias resultaron no
hacer falta cuando fui a mirar los datos.

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

### 5. Caché de respuestas del worker

La misma pregunta sobre los mismos ficheros sin cambios debería devolver el
resumen guardado. Clave: hash del contenido más la pregunta. Ahorra la llamada
entera, no una parte.

**Cuándo.** Cuando la telemetría muestre repeticiones. Hoy no las he medido, y
construir una caché para un problema que no se ha observado es cómo se acumula
complejidad inútil.

### 6. Un registro de repositorios instalados

`shunt update` funciona dentro de un repositorio, como git. Eso significa que
**nada te avisa de que otros ocho repos siguen con una versión vieja**. Un
registro en `~/.config` lo resolvería, a cambio de mantenerlo.

Elegí lo primero por simplicidad sabiendo lo que se pierde. Si esto se usa en
muchos proyectos a la vez, la cuenta cambia.

---

## Prioridad baja, o descartadas

### 7. `delegate_edit` sobre código de negocio

Hoy la allowlist lo limita a tests y andamiaje. Ampliarla es una decisión por
repositorio y está documentada, pero el valor real está en editar código de
verdad. Lo que falta para que sea defendible no es código: es confianza
acumulada, medida en cuántas ediciones delegadas pasaron revisión sin sorpresas.

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
