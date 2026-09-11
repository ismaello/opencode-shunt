English: [DISTRIBUTION.md](DISTRIBUTION.md)

# Distribuir este proyecto

Tres caminos, y el orden importa: el primero es el único obligatorio, y los
otros dos no aportan nada hasta que el primero está hecho.

---

## 0. Antes de nada: subirlo a GitHub

```bash
cd /ruta/al/proyecto
git init                                  # si no estaba ya
git add -A && git commit -m "..."
gh repo create opencode-shunt --public --source=. --push
```

Con eso **ya se puede instalar**, sin publicar en ningún registro:

```bash
uv tool install git+https://github.com/ismaello/opencode-shunt
pipx install git+https://github.com/ismaello/opencode-shunt
```

Si nunca vas a tener más de unos pocos usuarios, este es el final del camino.
Lo de abajo solo compra comodidad.

---

## 1. PyPI: `pipx install opencode-shunt`

El paquete ya está listo para esto; solo falta subirlo.

```bash
pip install build twine
python -m build                  # produce dist/*.whl y dist/*.tar.gz
twine check dist/*               # valida los metadatos como los valida PyPI
twine upload dist/*              # necesita un token de https://pypi.org/manage/account/token/
```

**Publica primero en TestPyPI**, porque un número de versión en PyPI no se puede
reutilizar nunca, ni borrándolo:

```bash
twine upload --repository testpypi dist/*
pipx install --index-url https://test.pypi.org/simple/ opencode-shunt
```

El nombre `opencode-shunt` hay que comprobar que esté libre. Si está cogido,
cambia `name` en `pyproject.toml` — y entonces cámbialo también en
`npm/package.json`, que tiene que coincidir.

---

## 2. npm: `npx opencode-shunt init`

Esto es lo que preguntabas, y tiene una arruga que conviene entender antes de
montarlo: **npx ejecuta paquetes de npm, y esta herramienta es Python.** No hay
forma de que npx ejecute Python directamente.

Hay tres maneras de salvar eso, y el proyecto lleva montada la segunda:

| Opción | Qué implica | Por qué no / sí |
|---|---|---|
| Reescribir el CLI en TypeScript | ~3.000 líneas: instalador, asistente, doctor, análisis de costes, replay | Es el destino correcto a largo plazo, porque OpenCode garantiza Node y no garantiza Python. Pero es un proyecto en sí mismo y no compra ninguna funcionalidad |
| **Paquete npm que lanza el Python** | ~140 líneas en `npm/bin/cli.js` | **Lo que está hecho.** Coste casi nulo. Su única debilidad es que necesita Python en la máquina |
| Decir a la gente que use `uvx` | Nada | `uvx opencode-shunt init` ya hace exactamente lo mismo que `npx`. Funciona hoy. La única razón para el npm es que tu público ya tiene npx en los dedos |

### Cómo funciona el envoltorio

`npm/bin/cli.js` no contiene lógica del producto. En la primera ejecución:

1. Busca un Python ≥ 3.10, del más nuevo al más viejo.
2. Crea un virtualenv privado en la caché del usuario
   (`~/.cache/opencode-shunt/venv-3.2.0`, o el equivalente en macOS/Windows).
3. Instala dentro el wheel que viaja en el propio paquete npm.
4. Hace `exec` al `shunt` de ese venv.

Las ejecuciones siguientes van directas al venv. Nada se instala en tu proyecto
ni en el Python del sistema.

Tres detalles deliberados, cada uno por un fallo que si no ocurre:

- **El venv lleva la versión en el nombre.** Sin eso, actualizar el paquete npm
  seguiría ejecutando la versión anterior desde una caché rancia.
- **Un venv a medio construir se borra y se rehace**, en vez de reutilizarse.
  Un venv roto de una ejecución interrumpida produce errores que parecen bugs
  del producto.
- **El código de salida se propaga.** `shunt doctor` devuelve distinto de cero
  cuando algo está mal, y eso es precisamente lo que usa un script para
  decidir. Un envoltorio que se coma el código de salida convierte una
  comprobación en un adorno.

### Publicarlo

```bash
python npm/prepare.py           # construye el wheel y lo mete en npm/dist/
npm publish ./npm               # necesita npm login
```

`prepare.py` **no es opcional ni decorativo**. Lee la versión de
`src/opencode_shunt/__init__.py` y la escribe en `npm/package.json`, porque el
fallo específico que hay que evitar es que `npx opencode-shunt@3.2.0` ejecute en
silencio la 3.1.0 que quedó dentro del tarball. El trabajo `npx` del CI
comprueba que las dos versiones coincidan y que el wheel empaquetado sea
realmente el de esa versión.

### Probarlo antes de publicar

```bash
python npm/prepare.py --pack
cd /tmp && mkdir prueba && cd prueba
npx --yes --package=/ruta/a/npm/opencode-shunt-3.2.0.tgz opencode-shunt init
```

Con `--package` porque desde un tarball local npx no sabe deducir el binario.
Una vez publicado, `npx opencode-shunt init` funciona sin más: el `bin` se llama
igual que el paquete, que es el caso en que npx resuelve solo.

---

## Qué se verificó antes de dar esto por listo

No es una lista de intenciones. Cada línea se ejecutó, y las cuatro primeras
fallaron a la primera:

| Comprobación | Resultado |
|---|---|
| El wheel construye | Falló. `license = { text = "MIT" }` más `license-files` es inválido con PEP 639; ahora es `license = "MIT"` con setuptools ≥ 77 |
| Faltaba el fichero `LICENSE` | Faltaba. `pyproject.toml` declaraba MIT sin que existiera el fichero. Creado, y ahora viaja dentro del wheel |
| `doctor` en un repo recién instalado | Falló. Anunciaba 77 delegaciones y 248k tokens ahorrados de *otros* repositorios, porque la telemetría es un fichero por máquina y nada filtraba. Ahora `doctor` y `stats` filtran por repositorio |
| `config --dry-run` sin terminal | Falló. Traceback de `EOFError`, que parece un producto roto en vez de uno esperando una respuesta. Ahora toma los valores por defecto y lo dice |
| El wheel lleva el runtime TypeScript | 22 ficheros: plugin, herramientas, lib, agentes, modelfiles |
| El wheel y el sdist instalan en un venv limpio | Los dos |
| Rutas de mi máquina o secretos en el paquete | Ninguno. Hay un test que lo comprueba |
| `npx` desde caché vacía, e `init` después | Funciona |
| El `bin` de npm queda cableado al instalar en global | `opencode-shunt` y `shunt`, los dos |
| El mensaje cuando no hay Python | Explica qué instalar y ofrece `pipx` como alternativa. Sale con código 1 |
| `doctor` devuelve ≠ 0 a través del envoltorio npx | Sí |

Lo que **no** está verificado, dicho claro:

- **Python 3.10, 3.11 y 3.12.** `pyproject.toml` declara 3.10 como mínimo y en
  esta máquina solo hay 3.14. Un mínimo que nadie ejecuta es una suposición, y
  por eso el CI lleva una matriz de las cuatro versiones: es lo único que
  convierte esa declaración en un hecho.
- **Windows.** El envoltorio contempla `Scripts\` y `shunt.exe`, pero no se ha
  ejecutado allí.
- **El nombre en los dos registros.** Ni `opencode-shunt` en PyPI ni en npm se
  ha comprobado que esté libre.
