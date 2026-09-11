> **Revisión externa recibida, conservada tal cual como documento de entrada.**
> Los hallazgos se verificaron uno a uno; ocho de los nueve concretos eran
> reales. El plan de trabajo que salió de aquí, reordenado por coste y
> beneficio y con lo ya ejecutado, está en [docs/PLAN.md](docs/PLAN.md).

# Plan Astra — Evolución de opencode-shunt

**Fecha del análisis:** 11 de septiembre de 2026  
**Estado:** propuesta para revisión; ejecución pendiente de aprobación.  
**Base analizada:** árbol de trabajo de la versión `3.3.0`, incluidos los cambios locales existentes durante el análisis.

## Resumen ejecutivo

La idea tiene potencial y el proyecto ya contiene una base útil. Mi valoración es la de un prototipo avanzado, con buenas decisiones de arquitectura, que necesita consolidar fiabilidad, medición y distribución antes de presentarse como una solución general.

El diferencial que buscaría sería este:

> Reducir el coste total de completar correctamente una tarea en OpenCode, con delegación verificable y un ahorro que el usuario pueda entender y contrastar.

El orden recomendado es:

**Fiabilidad → contabilidad completa → plugin nativo → optimización → evaluación → beta.**

## 1. Objetivo y alcance acordados

Construir un paquete open source para desarrolladores individuales que reduzca la factura API de OpenCode conservando la calidad del trabajo.

Decisiones confirmadas con el usuario:

| Aspecto | Decisión |
|---|---|
| Público inicial | Desarrolladores individuales |
| Modelo de producto | Open source gratuito |
| Consumo a optimizar | API por uso |
| Prioridad | Calidad primero |
| Sistemas operativos | Linux y macOS |
| Distribución | Plugin nativo de OpenCode; CLI Python avanzado inicialmente conservado |
| Proveedores prioritarios | OpenAI, Anthropic, Vertex AI, DeepSeek y endpoints compatibles |
| Proyectos prioritarios | Python, TypeScript/JavaScript y Java/Kotlin |
| Compatibilidad existente | Migrar las instalaciones personales actuales |
| Presupuesto inicial de evaluación | Máximo 20 USD |

La compatibilidad debe distinguir entre:

- **Lectura y exploración:** ampliamente agnósticas al lenguaje.
- **Generación y edición:** verificadas mediante adaptadores y herramientas del proyecto.
- **Proveedores:** compatibilidad explícita por modelo, protocolo y rol.

«Funciona en cualquier proyecto» debe significar que puede instalarse, adaptarse y explicar sus límites de forma fiable.

## 2. Opinión sobre la solución actual

### Lo que conservaría

1. **Separación efectiva del contexto.** `bulk-read.ts` lee los archivos directamente y devuelve un resultado condensado. El contenido original realmente queda fuera del contexto principal.
2. **Delegación mediante herramientas acotadas.** Un worker que recibe una operación concreta es más fácil de controlar y medir que un subagente autónomo generalista.
3. **Combinación de procesamiento determinista e IA.** Calcular el diff localmente y preservar fragmentos de errores literalmente son buenas decisiones.
4. **Reconocimiento del coste de coordinar.** El proyecto comprende que bloquear, reintentar y verificar también cuesta dinero.
5. **Telemetría, diagnóstico y actualización.** `doctor`, el manifiesto de instalación y los informes son una base valiosa.
6. **Documentación de experimentos y resultados negativos.** Registrar qué empeoró el coste ayuda a evitar repetir errores.

### El cambio conceptual más importante

**Resumir código también requiere criterio.** Un modelo potente puede razonar perfectamente sobre un resumen y aun así equivocarse porque el worker omitió la evidencia decisiva.

Por tanto, la calidad debe protegerse durante la selección y compresión de información, además de durante la revisión final.

Asimismo, un test generado que pasa puede comprobar una expectativa incorrecta. La validación debe comprobar el comportamiento solicitado, no limitarse a que el archivo compile o la suite quede verde.

## 3. Hallazgos prioritarios

**P0:** resolver antes de recomendar el producto para uso general.  
**P1:** necesario para una beta distribuible y mantenible.  
**P2:** optimización posterior, condicionada a evidencia.

Las rutas de código de las tablas son relativas a `src/opencode_shunt/`, salvo las que comienzan por `.github/`. Las referencias de línea corresponden al estado examinado durante el análisis.

### 3.1 Economía y medición

| Prioridad | Hallazgo | Evidencia |
|---|---|---|
| P0 | La decisión económica omite el coste del worker, sus reintentos y la salida adicional del orquestador. | `runtime/lib/economics.ts:98–140` |
| P0 | El informe aplica una tarifa de worker al conjunto de operaciones, aunque lector y escritor utilicen modelos distintos. | `costs.py:117–142` |
| P0 | Los precios cero pueden convertirse en tarifas de respaldo por usar `or`. | `costs.py:130–131` |
| P1 | Hay costes de operaciones rechazadas que no conservan sus tokens, y el resumen de comandos no registra los tokens de salida del worker. | `runtime/tools/delegate-edit.ts`, `runtime/tools/delegate-write.ts`, `runtime/plugins/shunt.ts:335–358` |
| P1 | Se mezclan bytes UTF-8, caracteres JavaScript y estimaciones de tokens; también contenido original con contenido que OpenCode ya habría truncado. | `runtime/tools/bulk-read.ts`, `stats.py`, `report.py` |
| P1 | `report` utiliza precios antiguos, omite `delegate_edit` y no aplica el repositorio recibido por el CLI. | `report.py`, `cli.py:135–143` |
| P1 | El modelo económico se configura con datos históricos aproximados y no se adapta al modelo efectivo de cada petición. | `configure.py:54–104`, `runtime/plugins/shunt.ts:129` |

**Comprobación realizada:** con tarifas sintéticas diferentes para lector y escritor, operaciones que sumarían **10,30 USD** se valoraron en **0,60 USD** al aplicar la tarifa del lector a ambas. Es una reproducción del defecto, no una estimación del gasto real del usuario.

La contabilidad de OpenCode tampoco debe presentarse como factura del proveedor verificada: es un registro de consumo y coste que hay que distinguir de una conciliación real.

### 3.2 Calidad e integridad del trabajo

| Prioridad | Hallazgo | Evidencia |
|---|---|---|
| P0 | Se marcan como cubiertos todos los archivos leídos, incluidos los que pueden haber fallado o quedado sin analizar. | `runtime/tools/bulk-read.ts:504–510` |
| P0 | La cobertura usa coincidencias parciales: declarar `src/a.py` puede dar por cubierto también `a.py`. | `runtime/lib/coverage.ts:49–58`; reproducido |
| P0 | El control de contención comprueba los directorios ascendentes, pero no el enlace simbólico del archivo final. | `runtime/lib/guards.ts:180–200`; reproducido |
| P0 | Las escrituras se aplican antes de validar, sin comprobación de cambios concurrentes ni sustitución atómica. | `runtime/tools/delegate-edit.ts:270–297`, `runtime/tools/delegate-write.ts:253–275` |
| P0 | La reversión de una edición con error sintáctico depende de que exista backup. La generación puede dejar un archivo inválido en disco. | Mismos módulos |
| P0 | Las herramientas acceden directamente al disco sin solicitar los permisos correspondientes mediante `context.ask`. Hay que integrar y verificar el respeto a permisos de lectura, edición y modo plan. | Herramientas del runtime; contrato SDK `1.18.29` |
| P1 | Las herramientas de escritura ignoran `context.abort` y crean su propia señal de timeout. | `runtime/tools/delegate-edit.ts:190`, `runtime/tools/delegate-write.ts:198` |

Además:

- El registro de cobertura no se invalida por cambios de contenido.
- El «tope del 25%» del resumen es un intento de compresión, no un límite garantizado.
- La cobertura no vuelve a verificarse después de comprimir.
- Verificar que una ruta existe no demuestra que la afirmación ni sus líneas sean correctas.
- El mecanismo de recuperación de salidas confía en una ruta incluida en texto. Debe usar metadatos confiables y comprobar el origen del artefacto.
- Su expresión regular no coincide con el aviso emitido por el truncador consultado de OpenCode `1.18.29`.

### 3.3 Configuración, distribución y experiencia de uso

| Prioridad | Hallazgo | Evidencia |
|---|---|---|
| P0 | `shunt config` reemplaza la configuración compartida y local; no conserva las políticas que el README afirma conservar. | `configure.py:507–518` |
| P1 | `shunt report --since` falla porque su implementación acepta `--days`. | Reproducido |
| P1 | `shunt bench` no expone ni reenvía correctamente pregunta y repositorio. | Reproducido |
| P1 | `shunt stats` termina con error cuando no existe telemetría, precisamente el caso de instalación nueva. | Reproducido |
| P1 | El instalador no contempla correctamente JSONC ni la configuración efectiva combinada de OpenCode. | `installer.py:140–168` |
| P1 | El runtime no respeta `SHUNT_DATA_DIR`, aunque el CLI sí. | `paths.py` frente a módulos TypeScript |
| P1 | La reparación de `worktree` solo se aplica al plugin; las herramientas siguen usando directamente `context.worktree`. | `runtime/lib/worktree.ts`, herramientas |
| P1 | Faltan pruebas de integración con OpenCode real y validación en macOS. | `.github/workflows/ci.yml` |
| P1 | El workflow escucha pushes a `main`, mientras la rama examinada es `master`. | Workflow y estado Git |

Otros ajustes necesarios:

- Unificar nombres reales de herramientas, prompts y telemetría: actualmente aparecen guiones y guiones bajos.
- Corregir el manifiesto para que represente fielmente archivos retenidos y actualizaciones parciales.
- Separar personalización de agentes y código actualizable.
- Revisar `general`: puede introducir delegación a un modelo caro sin un rol económico explícito.
- Comprobar autenticación y capacidades por modelo; tener una variable de entorno no prueba que una petición funcione.
- Evitar que el catálogo confunda precio, capacidad y disponibilidad.

## 4. Arquitectura recomendada

### 4.1 Plugin nativo como producto principal

Distribuir el runtime como paquete npm cargable por OpenCode.

El uso habitual debe poder instalarse sin Python. Se conservaría inicialmente el CLI Python para análisis e informes avanzados.

Propuesta:

- Plugin con hooks y herramientas registrados explícitamente.
- Inicializador pequeño en TypeScript para instalación y configuración.
- Una única fuente del runtime.
- Artefactos npm y, mientras sea necesario, wheel generados desde esa misma fuente.
- Migración de las instalaciones actuales con detección de duplicados.
- Configuración del usuario separada del código distribuido.

OpenCode admite oficialmente plugins npm y herramientas registradas desde el plugin.

### 4.2 Roles claros

Mantener una estructura sencilla:

| Rol | Responsabilidad |
|---|---|
| Orquestador | Decisiones, especificación, interpretación y revisión |
| Explorador | Localizar archivos y relaciones cuando una búsqueda directa no basta |
| Lector | Extraer evidencia y responder preguntas sobre fuentes delimitadas |
| Escritor | Generar o transformar contenido siguiendo un contrato verificable |

Explorador y lector pueden compartir modelo. Sus necesidades de herramientas y validación son diferentes.

La delegación debe identificarse por rol y sesión. Las exenciones basadas únicamente en proveedor o modelo no representan correctamente esa intención.

### 4.3 Servicios internos compartidos

Centralizar:

- Configuración validada y versionada.
- Resolución de proyecto, directorios y rutas.
- Integración con permisos de OpenCode.
- Transporte hacia proveedores.
- Registro de consumo por petición.
- Decisión económica.
- Gestión de artefactos.
- Validación de resultados.
- Estado de sesión y cobertura.

Esto reduce las discrepancias actuales entre runtime, `doctor`, informes y configuración.

## 5. Plan de ejecución

### Fase 1 — Corregir los bloqueantes de fiabilidad

**Prioridad:** P0.

Trabajo:

1. Convertir los fallos identificados en pruebas de regresión.
2. Integrar permisos nativos en las herramientas.
3. Corregir la contención de rutas, incluidos enlaces simbólicos finales.
4. Validar candidatos antes de aplicarlos.
5. Comprobar que el archivo original no cambió mientras respondía el worker.
6. Aplicar cambios mediante sustitución atómica.
7. Respetar cancelaciones.
8. Corregir cobertura, resultados parciales e invalidación por contenido.
9. Conservar políticas y ajustes locales al reconfigurar.
10. Establecer una salida de recuperación que permita continuar cuando falle el worker.

**Criterios de aceptación:**

- Una delegación no escribe si los permisos efectivos lo impiden.
- Un error de validación deja intacto el archivo original.
- Un cambio concurrente se detecta y no se sobrescribe.
- Los archivos fallidos o pendientes no constan como analizados.
- Reconfigurar modelos conserva los ajustes ajenos al cambio solicitado.
- Un worker caído no provoca ciclos de bloqueo y reintento.

### Fase 2 — Hacer fiable la contabilidad y la decisión económica

**Prioridad:** P0/P1.

Crear un registro por petición e intento, con:

- Proyecto, sesión, operación y rol.
- Proveedor y modelo efectivos.
- Tokens de entrada, salida y caché, normalizados.
- Estado: éxito, parcial, rechazo, error o cancelación.
- Duración.
- Tarifa aplicada, fecha y procedencia.
- Identificación de cantidades desconocidas o estimadas.

Separar claramente:

1. Consumo registrado.
2. Coste reconstruido a partir de tarifas.
3. Coste conciliado con el proveedor, cuando exista.
4. Ahorro contrafactual estimado.

La decisión debe aproximar:

```text
Ahorro neto esperado =
    coste esperado de resolver directamente
  − coste esperado de resolver mediante delegación
```

El segundo término debe incluir worker, coordinación, resumen, verificaciones adicionales, reintentos y recuperación.

También debe considerar:

- Modelo efectivo del orquestador.
- Caché observada y tarifas por tramos.
- Contexto y tipo de tarea.
- Herramienta de edición real: GPT puede usar `apply_patch`; no siempre existe un baseline de búsqueda y reemplazo.
- Incertidumbre de los parámetros.

**Criterios de aceptación:**

- Una operación se tarifa con su propio modelo.
- Un precio desconocido no se interpreta como gratuito.
- Los ceros legítimos se conservan.
- Los intentos fallidos con consumo no desaparecen.
- Los informes funcionan por proyecto, sin historial y ante esquemas no compatibles.
- Una delegación fallida no se presenta como ahorro conseguido.

### Fase 3 — Empaquetar e integrar de forma nativa

**Prioridad:** P1.

Trabajo:

1. Publicar la interfaz del runtime como plugin npm.
2. Añadir un inicializador mínimo sin Python.
3. Soportar JSON y JSONC preservando configuración existente.
4. Utilizar nombres propios para agentes y herramientas, evitando colisiones.
5. Hacer coherentes los directorios de datos entre runtime y CLI.
6. Resolver correctamente proyectos iniciados desde subdirectorios, worktrees y sesiones de servidor.
7. Migrar instalaciones actuales evitando cargar simultáneamente runtime copiado y plugin npm.
8. Añadir desinstalación y actualización que respeten personalizaciones.
9. Documentar versiones de OpenCode realmente compatibles.
10. Indicar cuándo es necesario reiniciar OpenCode.

La compatibilidad con proveedores debe basarse en adaptadores y capacidades explícitas. Incluir Anthropic como worker requerirá su protocolo correspondiente; el soporte actual como orquestador no lo proporciona.

**Criterios de aceptación:**

- Instalación normal sin Python en Linux/macOS.
- Una sola instancia activa del plugin.
- Herramientas correctamente descubiertas por OpenCode.
- Configuración existente preservada.
- Migración verificable de los proyectos actuales del usuario.
- Matriz de proveedor, protocolo y rol con estados comprobables.

### Fase 4 — Mejorar el ahorro conservando evidencia

**Prioridad:** P1.

Orden recomendado de decisión:

1. Resolver mecánicamente cuando sea posible.
2. Buscar símbolos y rutas.
3. Leer directamente fragmentos pequeños o decisivos.
4. Delegar volumen cuando el beneficio esperado lo justifique.
5. Recuperar más evidencia cuando el resultado sea insuficiente.
6. Escalar al modelo principal cuando haga falta criterio.

Mejoras:

- Considerar el volumen agregado de la tarea.
- Dar al orquestador instrucciones breves y coherentes con el runtime.
- Evitar gastar llamadas para descubrir restricciones que ya se conocen.
- Usar resultados estructurados con fuentes, rangos, cobertura y limitaciones.
- Verificar las citas contra el contenido entregado al worker.
- Distinguir evidencia comprobada de interpretación del modelo.
- Acotar salida mediante presupuesto, preservando hallazgos críticos.
- Recuperar evidencia mediante artefactos y lecturas dirigidas.
- Aplicar límites de reintentos y suspender temporalmente un worker que falla repetidamente.
- Utilizar codemods o herramientas del lenguaje cuando resuelvan una transformación con mayor fiabilidad.

Los modos de observación y aplicación deben permitir comparar decisiones antes de endurecer umbrales.

**Criterios de aceptación:**

- Ninguna lectura necesaria para verificar una conclusión queda bloqueada indefinidamente.
- El resumen indica qué quedó fuera.
- Los cambios mecánicos cumplen invariantes verificables.
- El coste de recuperación forma parte del resultado.
- El comportamiento con archivos pequeños evita coordinación innecesaria.

### Fase 5 — Evaluar calidad y ahorro con un máximo de 20 USD

**Prioridad:** P1.

#### Pruebas sin consumo API

- Regresiones de los defectos identificados.
- Integración con OpenCode usando un proveedor simulado.
- Respuestas vacías, truncadas, inválidas y parciales.
- Errores de autenticación, límites de uso y cancelaciones.
- Configuraciones incompletas.
- Instalación, actualización y desinstalación.
- Contratos de consumo y tarifas.
- Validación en Linux y macOS.

#### Piloto con API

Distribución orientativa:

| Uso | Presupuesto |
|---|---:|
| Comprobaciones pequeñas de proveedores | Hasta 2 USD |
| Comparaciones pareadas | Hasta 14 USD |
| Repeticiones y reserva | Hasta 4 USD |

El número de ejecuciones se ajustará al coste observado, reservando margen para las peticiones en curso.

Tareas representativas:

- Comprender un flujo entre archivos.
- Localizar un defecto que depende de una relación o ruta de error.
- Interpretar una salida larga de tests.
- Aplicar una transformación mecánica.
- Generar tests a partir de comportamientos definidos.
- Resolver una tarea pequeña donde delegar probablemente no compense.

Condiciones:

- Mismo estado inicial del repositorio.
- Mismo modelo principal dentro de cada comparación.
- Capacidades y permisos equivalentes.
- Orden de ejecución alternado o aleatorizado.
- Inclusión de costes de workers y subagentes.
- Registro de ejecuciones fallidas.
- Evaluación del resultado con criterios definidos antes del experimento.

**Métricas principales:**

- Coste total por tarea correctamente completada.
- Ahorro agregado y distribución por tarea.
- Calidad del resultado.
- Latencia.
- Reintentos, recuperaciones y verificaciones adicionales.

**Objetivo exploratorio:** buscar alrededor de un 20% de ahorro mediano en tareas de volumen, sin regresiones de calidad detectadas. Es una hipótesis de producto, no una promesa.

Con 20 USD se puede obtener evidencia preliminar útil. Los resultados deberán conservar la etiqueta de piloto cuando la muestra sea insuficiente.

### Fase 6 — Preparar una beta distribuible

**Prioridad:** P1.

Trabajo:

- Corregir y ampliar CI para Linux/macOS.
- Fijar explícitamente las versiones de runtime utilizadas.
- Evitar que errores inesperados de pruebas se conviertan en omisiones silenciosas.
- Probar los artefactos empaquetados en entornos limpios.
- Validar ejemplos de instalación y argumentos del CLI.
- Añadir diagnóstico del estado efectivo: activo, sin configurar, degradado o incompatible.
- Mostrar un resumen económico básico sin obligar al usuario a interpretar varios informes.
- Actualizar README y tutoriales.
- Documentar cambios, compatibilidad y migración.

La documentación debe expresar con precisión:

- Qué se ha medido.
- En qué tareas.
- Con qué muestra.
- Qué coste se incluye.
- Qué calidad se verificó.

Las cifras de compresión por operación deben diferenciarse visualmente del ahorro de factura por sesión.

## 6. Revisar las conclusiones experimentales actuales

### El 146% no es un umbral estadístico universal

`replay.py` convierte una variación observada en un experimento en una regla fija para descartar A/B en otros contextos.

Esa inferencia no está justificada. La capacidad de detectar una mejora depende del diseño, dispersión, emparejamiento y número de observaciones.

Acción:

- Conservar replay como simulación de políticas sobre trazas registradas.
- Mostrar supuestos e incertidumbre.
- Retirar el umbral universal del 146%.
- Dimensionar nuevas pruebas con datos del piloto correspondiente.

### Los descartes anteriores son resultados locales

Los datos actuales permiten decir que una caché exacta o determinada paralelización aportaron poco en esas muestras.

Su utilidad puede cambiar con sesiones interactivas, otros tamaños de repositorio o distintas combinaciones de modelos. Conviene registrar las condiciones para reconsiderarlas.

## 7. Segunda etapa: mejoras con mayor potencial diferencial

### 7.1 Mapa incremental del repositorio

Es la mejora futura que considero más prometedora.

Empezar por información verificable:

- Estructura.
- Símbolos y firmas.
- Relaciones de importación.
- Ubicación de tests.
- Convenciones detectadas.

Después, añadir descripciones semánticas pequeñas cuando aporten valor.

Requisitos:

- Invalidación por cambios de contenido.
- Actualización parcial.
- Presupuesto de tamaño.
- Recuperación bajo demanda.
- Indicación explícita de información obsoleta.

Su valor debe medirse como reducción de exploración repetida y mejora de localización.

### 7.2 Calibración basada en uso

Aprender, por proyecto y combinación de modelos:

- Qué operaciones generan ahorro.
- Qué workers fallan.
- Cuánta verificación adicional provocan.
- Qué tamaños y tipos de tarea funcionan mejor.

Los cambios de política deben ser explicables y reversibles.

### 7.3 Caché y concurrencia selectivas

Evaluarlas donde exista una oportunidad medida:

- Caché para entradas idénticas y artefactos estables.
- Concurrencia para operaciones independientes.
- Síntesis adicional cuando existan relaciones entre archivos.

### 7.4 Modelo principal económico para tareas declaradamente rutinarias

Puede explorarse mediante perfiles elegidos por el usuario o clasificación previa de tareas muy delimitadas. Requiere conservar el mismo contrato de calidad y contabilizar la escalada.

## 8. Criterios para considerar terminada la primera beta

- [ ] Instalación nativa sin Python en Linux y macOS.
- [ ] Configuración y permisos existentes preservados.
- [ ] Bloqueantes P0 corregidos y cubiertos.
- [ ] Escrituras cancelables, verificadas y resistentes a cambios concurrentes.
- [ ] Cobertura y citas sin falsas afirmaciones de completitud.
- [ ] Costes atribuidos por petición, proveedor, modelo y rol.
- [ ] Consumo desconocido identificado como tal.
- [ ] Recuperación del worker sin ciclos de bloqueo.
- [ ] CLI coherente y funcional desde una instalación limpia.
- [ ] Proveedores y roles documentados según las pruebas realizadas.
- [ ] Piloto reproducible dentro del presupuesto.
- [ ] Resultados de ahorro acompañados de evaluación de calidad.
- [ ] Actualización, migración y desinstalación verificadas.
- [ ] Artefactos publicables y documentación consistente.

## 9. Validación realizada durante el análisis

Se revisaron el runtime, herramientas, prompts, configuración, instalador, proveedores, informes, benchmark, pruebas, empaquetado y CI.

Se contrastó la integración con documentación oficial y código de OpenCode correspondiente a `1.18.29`.

Comprobaciones ejecutadas sin escribir archivos ni consumir API durante el análisis:

- Suite pura de economía: pasa.
- Suite pura de cobertura: pasa.
- Fallos de argumentos de `report` y `bench`: reproducidos.
- Error de tarificación de workers distintos: reproducido en memoria.
- Tratamiento incorrecto de precio cero: reproducido.
- Coincidencia falsa de rutas en cobertura: reproducida.
- Aceptación de enlace simbólico final externo: reproducida.
- Error de `stats` sin telemetría: reproducido.

Quedan pendientes las pruebas integrales con efectos de escritura, proveedores reales, instalación y macOS.

Referencias externas principales:

- [Plugins de OpenCode](https://opencode.ai/docs/plugins/)
- [Herramientas personalizadas](https://opencode.ai/docs/custom-tools/)
- [Configuración](https://opencode.ai/docs/config/)
- [Permisos](https://opencode.ai/docs/permissions/)
- [Contrato de herramientas del SDK 1.18.29](https://unpkg.com/@opencode-ai/plugin@1.18.29/dist/tool.d.ts)

## Conclusión

**Invertiría en este proyecto.** Ya tiene el mecanismo fundamental para ahorrar y experiencia práctica que merece conservarse.

La gran oportunidad está en convertir la delegación en una experiencia confiable: que el usuario instale el paquete, trabaje normalmente y pueda saber cuánto gastó, qué aportó la delegación y con qué evidencia se validó el resultado.

Este plan queda listo para revisión. La ejecución de las mejoras está pendiente de aprobación del usuario.
