# Distribution

Spanish: [DISTRIBUCION.es.md](DISTRIBUCION.es.md)

Three paths, and the order matters: the first is the only one you must take. The
other two buy convenience and add nothing until the first is done.

---

## 0. First: push to GitHub

```bash
cd /path/to/project
git init                                  # if not already
git add -A && git commit -m "..."
gh repo create opencode-shunt --public --source=. --push
```

That is enough to **install**, without publishing to any registry:

```bash
uv tool install git+https://github.com/ismaello/opencode-shunt
pipx install git+https://github.com/ismaello/opencode-shunt
```

If you will never have more than a handful of users, stop here. What follows only
buys convenience.

---

## 1. PyPI: `pipx install opencode-shunt`

The package is ready for this; it just needs uploading.

```bash
pip install build twine
python -m build                  # produces dist/*.whl and dist/*.tar.gz
twine check dist/*               # validates metadata the way PyPI does
twine upload dist/*              # needs a token from https://pypi.org/manage/account/token/
```

**Publish to TestPyPI first**, because a version number on PyPI can never be
reused, not even by deleting it:

```bash
twine upload --repository testpypi dist/*
pipx install --index-url https://test.pypi.org/simple/ opencode-shunt
```

Check that the name `opencode-shunt` is available. If it is taken, change `name`
in `pyproject.toml` — and change it in `npm/package.json` too, which must match.

---

## 2. npm: `npx opencode-shunt init`

This is what most people reach for, and there is a wrinkle worth understanding
before you set it up: **npx runs npm packages, and this tool is Python.** There
is no way for npx to execute Python directly.

Three ways to bridge that gap, and the project ships the second:

| Option | What it implies | Why not / yes |
|---|---|---|
| Rewrite the CLI in TypeScript | ~3,000 lines: installer, wizard, doctor, cost analysis, replay | The right long-term destination, because OpenCode guarantees Node and does not guarantee Python. But it is a project in itself and buys no functionality |
| **npm package that launches Python** | ~140 lines in `npm/bin/cli.js` | **What is done.** Near-zero cost. Its only weakness is that it needs Python on the machine |
| Tell people to use `uvx` | Nothing | `uvx opencode-shunt init` does exactly what `npx` would. Works today. The only reason for npm is that your audience already has npx in muscle memory |

### How the wrapper works

`npm/bin/cli.js` contains no product logic. On first run:

1. Finds a Python ≥ 3.10, newest first.
2. Creates a private virtualenv in the user cache
   (`~/.cache/opencode-shunt/venv-3.2.0`, or the macOS/Windows equivalent).
3. Installs inside it the wheel bundled with the npm package itself.
4. `exec`s the `shunt` from that venv.

Later runs go straight to the venv. Nothing is installed into your project or
into the system Python.

Three deliberate details, each fixing a failure that otherwise looks like a product bug:

- **The venv name includes the version.** Without that, updating the npm package
  would keep running the old version from a stale cache.
- **A half-built venv is deleted and rebuilt**, not reused. A broken venv from an
  interrupted run produces errors that look like product bugs.
- **Exit codes propagate.** `shunt doctor` returns non-zero when something is
  wrong, and that is exactly what a script uses to decide. A wrapper that swallows
  exit codes turns a check into decoration.

### Publishing

```bash
python npm/prepare.py           # builds the wheel and puts it in npm/dist/
npm publish ./npm               # needs npm login
```

`prepare.py` is **not optional or decorative**. It reads the version from
`src/opencode_shunt/__init__.py` and writes it into `npm/package.json`, because
the specific failure to avoid is `npx opencode-shunt@3.2.0` silently running
the 3.1.0 that stayed inside the tarball. The CI `npx` job checks that the two
versions match and that the packaged wheel is actually from that version.

### Testing before publish

```bash
python npm/prepare.py --pack
cd /tmp && mkdir test && cd test
npx --yes --package=/path/to/npm/opencode-shunt-3.2.0.tgz opencode-shunt init
```

Use `--package` because from a local tarball npx cannot infer the binary. Once
published, `npx opencode-shunt init` works as-is: the `bin` name matches the
package name, which is the case where npx resolves on its own.

---

## What was verified before calling this ready

Not a list of intentions. Each line was run, and the first four failed on the
first attempt:

| Check | Result |
|---|---|
| Wheel builds | Failed. `license = { text = "MIT" }` plus `license-files` is invalid under PEP 639; now `license = "MIT"` with setuptools ≥ 77 |
| Missing `LICENSE` file | Missing. `pyproject.toml` declared MIT without the file existing. Created, and it now ships inside the wheel |
| `doctor` on a fresh install | Failed. Reported 77 delegations and 248k tokens saved from *other* repositories, because telemetry is one file per machine and nothing filtered. Now `doctor` and `stats` filter by repository |
| `config --dry-run` without a terminal | Failed. `EOFError` traceback, which looks like a broken product instead of one waiting for input. Now takes defaults and says so |
| Wheel carries the TypeScript runtime | 22 files: plugin, tools, lib, agents, modelfiles |
| Wheel and sdist install in a clean venv | Both do |
| Machine paths or secrets in the package | None. A test checks this |
| `npx` from empty cache, then `init` | Works |
| npm `bin` wired on global install | Both `opencode-shunt` and `shunt` |
| Message when Python is missing | Explains what to install and offers `pipx` as an alternative. Exits with code 1 |
| `doctor` returns ≠ 0 through the npx wrapper | Yes |

What is **not** verified, stated plainly:

- **Python 3.10, 3.11, and 3.12.** `pyproject.toml` declares 3.10 as minimum and
  this machine only has 3.14. A minimum nobody runs is an assumption, which is
  why CI carries a matrix of all four versions: that is the only thing that turns
  the declaration into a fact.
- **Windows.** The wrapper handles `Scripts\` and `shunt.exe`, but it has not
  been run there.
- **Name availability on both registries.** Neither `opencode-shunt` on PyPI nor
  on npm has been checked for availability.
