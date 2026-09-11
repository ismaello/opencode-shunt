# opencode-shunt

Keep bulk work off your expensive model, in any [OpenCode](https://opencode.ai)
repository.

```bash
cd your-project
npx opencode-shunt init      # put the runtime in ./.opencode
npx opencode-shunt config    # decide who orchestrates, who reads, who writes
npx opencode-shunt doctor    # confirm it actually works
```

Then use OpenCode as normal: `opencode --agent orchestrator`.

## About this package

This is a launcher. The tool itself is Python, and this package carries it: the
first run builds a private virtualenv in your cache directory and installs it
there, so nothing lands in your project or your system Python. Later runs go
straight to it.

It therefore needs **Python 3.10 or newer** on your machine. If you would rather
skip the wrapper:

```bash
uv tool install opencode-shunt      # then: shunt init
pipx install opencode-shunt
```

Same tool, same version. The npm package exists because everyone who can run
OpenCode already has `npx`.

Full documentation: https://github.com/ismaello/opencode-shunt
