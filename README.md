# opencode-shunt

Keep bulk work off your expensive model.

A frontier model is worth its price for judgement and worthless for volume. This
installs the machinery that enforces that split inside [OpenCode](https://opencode.ai):
reading code in bulk, generating files and applying repetitive edits all go to a
cheap model, and only the result comes back to the expensive one.

```
pipx install opencode-shunt      # or: uv tool install opencode-shunt

cd your-project
shunt init                       # put the runtime in ./.opencode
shunt config                     # decide who orchestrates, who reads, who writes
shunt doctor                     # confirm it actually works
```

Then use OpenCode as normal: `opencode --agent orchestrator`.

## Three roles, and only one of them is worth paying for

| Role | Job | What it needs |
|---|---|---|
| Orchestrator | Reasons, designs, reviews, decides | Capability. This is where a frontier model earns its price, and the only place it does. |
| Reader | Summarises code in bulk | Obedience to a rigid format and exact line citations. Cheap and fast wins. |
| Writer | Generates tests, applies mechanical edits | Genuinely harder: the result has to compile and match your conventions. |

`shunt config` asks which model fills each, and works out the rest. Anything it
can measure, it measures rather than asking — the two numbers that most affect
when delegating pays off are how long your conversations run and how many turns
a file survives in context, and both are readable from OpenCode's own database.
Nobody knows those about themselves.

Any mix works. Claude orchestrating with Gemini Flash reading, a local Qwen on
your own GPU, DeepSeek doing both, an OpenCode Zen model — the worker layer
speaks three API shapes rather than knowing about vendors, so adding a provider
is configuration, not code.

## What it does

**`bulk_read(question, paths)`** reads files from disk, has the worker analyse
them, and returns a summary with exact line ranges. The file contents never
enter the expensive context. Measured across 7 real calls: **88% less content
ingested**, median.

**Read blocking.** The plugin refuses to let the orchestrator read a large file
whole, and tells it what to do instead. Bulk reading you have to remember to
delegate is bulk reading you will forget to delegate.

**Output summarising.** A 400-line test failure or a large diff gets condensed
before it reaches the expensive context, with the critical part — the error, the
stack trace — extracted mechanically and preserved character for character.
Measured across 5 calls: **92% less**, median.

**`delegate_write(path, instruction, reference_paths)`** generates a file
without the orchestrator emitting its body. Measured: **76% fewer output
tokens**, median of 3.

**`delegate_edit(path, instruction)`** applies the same mechanical change at
many sites. Note what it is *not* for: OpenCode's edit tool works by search and
replace, so changing one line already costs only that line. What costs money is
repetition, because each site needs its surrounding context emitted twice, as
the text to find and the text to replace it with. Measured on 21 docstrings
across a 308-line file: **78% fewer output tokens**, tests still passing, and
the file byte-identical to the original apart from the docstrings.

The worker returns the whole file and **the diff is computed here**, so it
cannot misreport its own edit, and a rewrite dressed up as a tidy-up shows up as
a diff touching most of the file — which is refused.

## When delegating is not worth it

Delegation costs a round trip, and below a certain size that costs more than it
saves. So there is a floor, and it is **computed from prices** rather than
guessed, in `lib/economics.ts`.

Measured over 60 real Opus sessions, the bill breaks down as 47% cache writes,
34% output, 19% cache reads and essentially no fresh input. Everything ingested
is written to cache once at a premium and then re-sent every following turn. So
delegating saves the write plus all those re-reads, and costs the extra turns it
adds. With the conversation length actually observed, the crossover lands near
**13 KB** — which is within a kilobyte of the 400-line threshold originally
chosen from evidence alone. Two independent routes agreeing is the best signal
either is right.

Below it, the tool declines and shows you the arithmetic:

```
bulk_read declined: these files total 163 lines / 5.1 KB, below the 12.6 KB at
which delegating starts to pay for itself.
...delegating this would cost about $0.013 more than reading it.
```

## Run `shunt doctor`

Not a formality. **Every way this system breaks, it breaks quietly and in the
expensive direction.** All three of these are verified, not hypothetical:

- Malformed `shunt.json` means the plugin never loads. OpenCode starts, answers
  normally, and says nothing.
- A profile name that does not resolve means `bulk_read` fails. The orchestrator
  quietly reads the files itself, gives a perfectly good answer, and bills full
  price.
- An orchestrator whose provider sits in the exempt list means nothing is ever
  shunted — and because the exemption check runs before any telemetry is
  written, that setup produces no records at all.

Which means a broken install and an unused one look identical: an empty report.
Nothing inside the system can tell you which you have. That is what `doctor` is
for, and why it checks the role assignment rather than just the files.

## Measuring it

```
shunt stats      # what each delegation saved, from the shunt's own telemetry
shunt report     # what it saved across real sessions, against opencode's accounting
shunt bench      # A/B with the shunt on and off. Slow, and it costs real money
```

`stats` is per-operation and deterministic, so it is the one to iterate against.
`report` joins telemetry with OpenCode's session records to estimate savings in
real use, and is honest that the upper bound assumes the content would have
stayed in context — which is why it reports a range.

`bench` measures whole sessions with and without the shunt. On this repository
it came out at **22% of session tokens**, which is far below the per-operation
88% and not a contradiction: the per-operation figure is what a delegation saves
on the content, the session figure includes the prompts, the reasoning and the
turns that delegation itself adds.

## Honest limits

**It saves input, mostly.** Output is a third of the bill and `delegate_write`
and `delegate_edit` only reach the part of it that is bulk. The orchestrator's
own reasoning is untouched, and it should be — that is what you are paying for.

**The worker will be wrong sometimes.** A generated test that passes may be
asserting the wrong thing. Every receipt says so, and the agent prompt is
explicit that worker output is a lead to verify rather than a conclusion. Syntax
is checked, generated files are confined to an allowlist, edits are backed up
and reverted if they do not parse, but none of that is review.

**`allowedProviders` binds the workers, not the orchestrator.** It is enforced
where worker profiles resolve. The orchestrator's model comes from OpenCode's
own configuration and is not checked, so marking a repository as local-only does
not stop a cloud orchestrator seeing it. `doctor` warns when you are in that
state; choose a local orchestrator too if the restriction has to be real.

**Latency.** A delegation costs 3–14 seconds, and it is dominated by the worker
*writing* the summary rather than reading the input: measured at roughly 2.5
seconds fixed plus 5 ms per generated token. Splitting one call into several
concurrent ones would help, but it destroys the cross-file synthesis that is the
main reason to use `bulk_read` at all, so it is not done.

## Configuration

`shunt config` writes two files, deliberately separate:

- **`.opencode/shunt.json`** — shared policy. Which paths may be generated,
  which providers may see the code, the economics. Decisions about the
  repository. **Commit this.**
- **`.opencode/shunt.local.json`** — this machine. The active profile, local
  endpoints, project ids. Gitignored automatically, because committing it either
  leaks something or breaks the next person to clone.

API keys are never written to either. Profiles record the *name* of the
environment variable holding a key, never its value.

## Contributing

```
pip install -e ".[dev]"
pytest                  # 39 checks: Python and the TypeScript runtime
```

The runtime's pure functions have their own deterministic suites, run through
pytest so one command covers everything. They cover the parts where failures are
silent: coverage parsing, the break-even arithmetic, and the guards that stop a
worker deleting code it was asked to edit.

## Documentation

- [How it works](docs/COMO_FUNCIONA.md) — the whole system, in plain terms
- [How it was built](docs/PLAN_EJECUCION.md) — the playbook, to repeat on another project
- [What is next](docs/PROXIMAS_MEJORAS.md) — what is missing and what it would take

## Credit

The idea comes from Spotify's write-up of cutting Claude Code token usage by 90%
with Portal. This is an attempt at the same thing in OpenCode, generalised so it
can be dropped into any repository.
