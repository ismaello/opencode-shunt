# opencode-shunt

Keep bulk work off your expensive model.

A frontier model is worth its price for judgement and worthless for volume. This
installs the machinery that enforces that split inside [OpenCode](https://opencode.ai):
reading code in bulk, generating files and applying repetitive edits all go to a
cheap model, and only the result comes back to the expensive one.

```
npx opencode-shunt init          # or: pipx install opencode-shunt && shunt init

cd your-project
shunt init                       # put the runtime in ./.opencode
shunt config                     # decide who orchestrates, who reads, who writes
shunt doctor                     # confirm it actually works
```

Then use OpenCode as normal: `opencode --agent orchestrator`.

Three ways in, same tool and same version in each. `npx` is listed first only
because anyone who can run OpenCode already has it; the tool itself is Python,
and the npm package is a launcher that keeps its virtualenv in your cache
directory. `pipx install` or `uv tool install` skip that indirection.

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

## Choosing an orchestrator

`shunt config` offers the list below and configures everything downstream from
your answer. You do not edit the model anywhere by hand: the wizard writes the
agent prompts, the provider block in `opencode.json`, the exemption list and the
delegation economics together, and getting any one of those out of step with the
others is how this system breaks quietly.

| Orchestrator | Credentials it needs | Price per million | Delegation floor it produces |
|---|---|---|---|
| `anthropic/claude-opus-5` | `ANTHROPIC_API_KEY`, or `opencode auth login` | $5 / $25 | 9.7 KB |
| `openai/gpt-6-astra` | `OPENAI_API_KEY` | $10 / $50 | 9.7 KB |
| `google-vertex/gemini-3.1-pro-preview` | `gcloud auth application-default login`, plus `GOOGLE_CLOUD_PROJECT` and `VERTEX_LOCATION` | $2 / $12 | 11.7 KB |
| `openai/gpt-5.2` | `OPENAI_API_KEY` | $1.75 / $14 | 11.7 KB |
| `deepseek/deepseek-v4-pro` | `DEEPSEEK_API_KEY` | $0.435 / $0.87 | 1.2 KB |

**These prices are read, not remembered.** OpenCode maintains a model table and
`shunt config` prices from it, falling back to a baked-in list only when it
cannot be read. That indirection exists because the alternative was caught
failing: the catalogue here offered Gemini 2.5 Pro while 3.1 Pro was current,
priced GPT-5.2 at 5.1's rates, and had Gemini's cache discount wrong by a
factor of two and a half. None of that shows up as an error — it shows up as a
delegation floor computed for a model nobody is running.

Reading that table has its own trap, worth knowing about because the fix is not
obvious: it lists every reseller of a model, not one price per vendor.
`claude-opus-4-8` appears under 21 providers and one of them quotes it at zero.
A model believed to be free drives the floor to zero and delegates everything,
so the vendor we know about wins, and among strangers the dearest quote wins.

**For mechanical work, the cheap orchestrator wins outright.** The same
instruction — translate the docstrings in two modules — run under Opus and
under Gemini Pro, with identical delegation and identical tests passing
afterwards, came out **92% cheaper and four times faster** on Gemini.

**For work that needs judgement, it does not.** Three defects planted in a
multi-tenant service, one prompt, one pass each: Opus found 3 of 3, GPT-6 Astra
found 3 of 3, Gemini 3.1 Pro found 2 of 3. That is the split the whole system
is built around, and [the detail is
here](docs/REAL_RESULTS.md#6-opus-vs-gemini-31-pro-vs-astra-on-a-judgement-task).

**The floor moves with the orchestrator, and that is the point.** Delegating
exists to protect an expensive context, so a cheaper boss means less worth
protecting, and the break-even where `bulk_read` starts refusing rises on its
own. It is driven by the ratio between what a provider charges to put a token in
cache and to re-send it. Anthropic bills a cache write at 1.25x input and reads
at a tenth of it; Vertex bills no separate write, so the write is the input
price and a read is a tenth. Those ratios, not the headline price, are why the
same repository delegates at 9.7 KB under Opus and 11.7 KB under Gemini Pro.
Nothing to configure; `shunt config` prices it from whichever model you chose.

**One vendor can hold two roles.** Gemini Pro orchestrating with Gemini Flash
reading is a normal setup and the one case the original design got wrong, since
read exemptions used to be keyed by provider — which would have exempted the
orchestrator too and switched the whole system off in silence. Exemptions are
keyed by model now, and `shunt doctor` fails loudly if the orchestrator ends up
on that list.

**Switching later** is `shunt config` again. It rewrites the prompts, the
economics and the exemptions to match; your `writePaths`, `editPaths` and cloud
policy survive, and `shunt update` never touches `shunt.json`.

## What it does

**`bulk_read(question, paths)`** reads files from disk, has the worker analyse
them, and returns a summary with exact line ranges. The file contents never
enter the expensive context. Measured across 7 real calls: **88% less content
ingested**, median.

The answer is capped at a quarter of the source it describes, and a second
cheap pass shortens it if the worker overruns. Without that cap a broad
question turned 17.7 KB of code into a 27.5 KB essay — a delegation that paid
for a worker call and a round trip to put *more* in the expensive context than
reading the files would have. Adding the cap took the same review from $0.25
to $0.22 with the summary at 81% compression.

**Read blocking.** The plugin refuses to let the orchestrator read a large file
whole, and tells it what to do instead. Bulk reading you have to remember to
delegate is bulk reading you will forget to delegate.

It also refuses a full re-read of a large file the worker already summarised in
this session, because delegating and then reading the file anyway puts the
content in the context regardless, on top of the summary you paid for. The
refusal has its own break-even, from the same economics as the delegation
floor, and it is deliberately high: a refusal costs the turn the model spends
being told no, and blocking every re-read regardless of size took a $0.25
review to $0.47. Below the threshold the prompt asks and the hook says nothing.
Reads with `offset` and `limit` are never touched, which is what the summary's
line ranges are for.

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

The two tools have deliberately different reach. `delegate_write` is confined to
tests and scaffolding, because a file nobody reads is only safe where a test run
can judge it. `delegate_edit` reaches source code, because the change comes back
as a reviewable diff. Measured on a real repository, holding edits to the
test-only list made the orchestrator hand-write 26 docstrings the worker could
have written, at **39% more for that task**. Underneath both sits a list nothing
can widen: CI definitions, migrations, lockfiles and `.opencode/` itself.

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
shunt costs      # write shunt-costs.md: what this repository has cost, and where it went
shunt stats      # what each delegation saved, from the shunt's own telemetry
shunt report     # what it saved across real sessions, against opencode's accounting
shunt replay     # re-judge your recorded reads against a different threshold. Free
shunt bench      # A/B with the shunt on and off. Slow, and it costs real money
```

`costs` is the one to reach for when somebody asks what this is costing. It
writes a Markdown file scoped to the repository you run it in, splitting the
bill by token class — output, cache write, cache read — because those are
attacked by different tools and an undifferentiated total tells you nothing
about what to do next. The spend comes from OpenCode's own accounting rather
than a token estimate of ours; the avoided column is an estimate and is labelled
as one. It also surfaces failed worker calls, which matter more than they look:
when a delegation fails the work silently returns to the expensive model.

`stats` is per-operation and deterministic, so it is the one to iterate against.
`report` joins telemetry with OpenCode's session records to estimate savings in
real use, and is honest that the upper bound assumes the content would have
stayed in context — which is why it reports a range.

`bench` measures whole sessions with and without the shunt. On this repository
it came out at **22% of session tokens**, which is far below the per-operation
88% and not a contradiction: the per-operation figure is what a delegation saves
on the content, the session figure includes the prompts, the reasoning and the
turns that delegation itself adds.

`replay` is the one to reach for before changing a threshold, and it exists
because `bench` cannot answer most of the questions people want to ask it. A
two-arm run on this project varied by **146%** between executions of an
identical task, since the model picks a different strategy every time, so any
effect smaller than that comes back as noise however much you spend on repeats.
`replay` re-judges reads already in your telemetry against a threshold you name.
It costs nothing, has no variance, and reports a modelled ceiling rather than a
measurement — and it says which of the two you are looking at, including a flat
refusal to endorse an A/B whose expected effect sits under the noise floor.

It has already earned itself. Unifying the read floor with the economic one
looked obviously right and would have cost a few dollars to test; the replay
showed it would have touched 5 of 81 reads and been worth 8.6% of three
sessions, against a *measured* 84% penalty the last time this guard was
hardened. The change was dropped, and [the reasoning is
recorded](docs/PROXIMAS_MEJORAS.es.md) because it will look like a good idea again.

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
pytest                  # 120 checks: Python and the TypeScript runtime
```

The runtime's pure functions have their own deterministic suites, run through
pytest so one command covers everything. They cover the parts where failures are
silent: coverage parsing, the break-even arithmetic, and the guards that stop a
worker deleting code it was asked to edit.

## Documentation

Primary docs are in English. Spanish originals are kept as `*.es.md`.

- [**Tutorial**](TUTORIAL.md) — start here if you have not seen this before. Six
  worked flows showing what actually happens to a request
  ([español](TUTORIAL.es.md))
- [How it works](docs/HOW_IT_WORKS.md) — the whole system, in plain terms
  ([español](docs/COMO_FUNCIONA.es.md))
- [Results from a real build](docs/REAL_RESULTS.md) — a FastAPI app built with
  it, what it cost, and the silent defects that only showed up under real use
  ([español](docs/RESULTADOS_PRUEBA_REAL.es.md))
- [What is next](docs/PROXIMAS_MEJORAS.es.md) — what is missing and what was
  discarded after measuring (Spanish)
- [Distribution](docs/DISTRIBUTION.md) — publishing to PyPI and npm, and what
  was verified before calling it ready
  ([español](docs/DISTRIBUCION.es.md))

## Credit

The idea comes from Spotify's write-up of cutting Claude Code token usage by 90%
with Portal. This is an attempt at the same thing in OpenCode, generalised so it
can be dropped into any repository.
