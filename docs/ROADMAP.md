# Roadmap

Spanish: [PROXIMAS_MEJORAS.es.md](PROXIMAS_MEJORAS.es.md)

Ordered by what each item would buy against what it costs. Each one states the
evidence behind it, because several that looked obvious turned out unnecessary
once the data was checked.

---

## Discarded after measuring: unify the two decision thresholds

**Conclusion: not worth it, and the check came free.** It stays written because
the hypothesis was convincing, because it will look like a good idea again, and
because the condition under which it *would* be worth revisiting is identified.

The payoff, replaying all real telemetry against both thresholds:

| | |
|---|---|
| Reads in the band | **5 of 81** (6%), 60,486 bytes |
| Concentrated in | 3 sessions, two of them the most expensive in the repo |
| Modelled saving, best case | **$0.0346** on a $0.4156 session, **8.3%** |
| Modelled saving, full history | **$0.1136** |
| In `testcode` (Gemini Pro orchestrator) | **zero** reads in the band |

Eight percent cannot be measured here. `bench.py` itself documents that the
control arm of a two-session A/B varied by **146%** between runs, because the
model picks a different strategy each time. Spending $1 or $3 on an A/B to
estimate an 8% effect produces a number indistinguishable from noise.

The asymmetry decides: the benefit is **modelled**, while the cost of getting
blocking wrong is **measured in real money**, +84% on one task. A modelled 8%
does not justify risking a measured 84%.

**When this would change.** The saving depends mostly on how many turns content
survives in context, which in measured use is 2 because almost everything runs
as one-shot `opencode run`:

| Turns in context | Modelled saving | % of session |
|---:|---:|---:|
| 2 (measured use) | $0.0346 | 8.3% |
| 5 | $0.0454 | 10.9% |
| 12 | $0.0708 | 17.0% |
| 20 (long interactive session) | $0.0998 | 24.0% |

If use shifts to long interactive sessions, this comes back with 24% behind it,
which is measurable. Until then, no.

This also explains why the 400-line constant has not hurt despite being four
times above the economic floor: in practice almost no file falls in the band
between the two.

---

## The original diagnosis that led here

Found when installing the system in a real repository (11,235 lines) and running
a normal task: "explain the flow of a run". Cost $0.4156, against a $0.1214
average in that repo. The system delegated nothing, and not from a bug: every
decision it made was correct under its own rule.

What happened, from that session's telemetry:

- Read `runs.py` (12,027 bytes) and `workflows.py` (12,144). Both approved as
  `allow-small`.
- Tried `bulk_read` on 3 files totalling 3,373 bytes. Rejected as too small,
  and **correctly**: the economic floor was 9,931 bytes and delegating that would
  have lost $0.0107.
- Ended up reading 10 different files, **39,904 bytes total**, none large enough
  on its own for anyone to object.

The cause is that the system's two thresholds are computed differently:

| Decision | Threshold | Source |
|---|---|---|
| Do I block this read? | 400 lines / 40,960 bytes | hand-written constant |
| Is delegating worth it? | 9,931 bytes in this repo | computed from prices and your habits |

There is a 10 KB to 40 KB band where delegating pays and nothing pushes the
model to delegate. The two 12 KB files sat right in the middle, and ten of them
added up to 40 KB of expensive context. `lib/economics.ts` exists for exactly
this and the read shunt does not use it.

Of the 39,904 bytes in that session, only 24,171 were actually above the
economic floor: the rest were files that did not merit delegating either. That
nuance is what turned the hypothesis into the negative result above.

**The method stayed, and is now a command:** `shunt replay`. Replaying
historical telemetry against an alternative threshold answers "how much would
this have changed?" for zero dollars and zero variance, and is the sensible
alternative to an A/B when the effect you want is smaller than model noise. It
reports a modelled ceiling, says that is what it is, and includes the
turn-sensitivity table.

It carries a rule inside: if the expected effect sits below the 146% variance
measured in a two-arm A/B, it refuses to recommend the A/B and says so in those
words. That is exactly what would have saved the four iterations of review
recorded in [RESULTADOS_PRUEBA_REAL.md](RESULTADOS_PRUEBA_REAL.es.md).

---

## What was already measured, and how it reorders the list

These figures are a floor, not a ceiling: they come from one 6,453-line
repository with a 547-line maximum per file.

**Per operation** (telemetry, deterministic):

| Operation | Median saving | Sample |
|---|---|---|
| `bulk_read` | 88% of content never enters context | 7 calls |
| Output summarising | 92% | 5 calls |
| `delegate_write` | 76% of output tokens | 3 calls |
| `delegate_edit` | 78% of output tokens | 1 case, 21 sites |

**Per session** (A/B with shunt on and off): 22%.

The session figure being far below the per-operation one is not a contradiction,
and understanding that is what orders this list: the first measures what is saved
*from the content*; the second includes the prompts, the orchestrator's
reasoning, and the turns that delegation itself adds.

**And the bill breakdown**, over 60 real Opus sessions, is what changed
priorities most:

| Category | % of bill |
|---|---|
| Cache writes (content entering for the first time) | 46.6% |
| Output tokens | 34.1% |
| Cache reads (re-sent each turn) | 19.3% |
| Uncached input | ~0% |

Three things follow. First, the read shunt attacks both cache blocks, which are
66% of the bill. Second, **the 34% of output has a hard ceiling** that only
`delegate_write` and `delegate_edit` touch, and only their mechanical part: the
orchestrator's reasoning is untouchable, and should be, because that is what you
are paying for. Third, cache reads on every turn are why delegating small files
*loses* money, which is what the economic floor now calculates.

---

## Next distribution step: npm plugin packaging

This is worth doing and was undervalued at first.

Today we copy 17 files into your `.opencode/` and maintain a
`.shunt-manifest.json` with hashes to know which ones you touched. All that
machinery exists *only* because we copy files. With `"plugin": ["opencode-shunt"]`
in `opencode.json`, OpenCode installs it with Bun into its own cache and
updating stops being our problem.

Three concrete risks, in order of severity:

**Double load.** OpenCode's documentation says it literally: a local plugin and
an npm one with similar names load separately. During migration, anyone with
both runs every hook twice: double blocking, double telemetry, double worker
calls. No error, just a doubled bill. An explicit guard is needed, and
`shunt update` should detect and remove the local copy.

**Worktree resolution gets worse.** We just fixed a bug where OpenCode passed
`worktree: "/"`, and the fix relies on `process.cwd()` because the plugin lives
inside the repo it serves. From `~/.cache/opencode/node_modules/` that safety
net disappears. This must be resolved **before** moving the package, not after.

**`init` does not go away.** Agent prompts are customisable per repo and still
need to land in `.opencode/agents/`.

In favour: tools stop being loose files in `.opencode/tools/` and register from
the plugin with the `tool:` key, which versions them together.

This is distribution, not a rewrite of the Python CLI. The npx wrapper already
covers people who do not want to think about Python; see [DISTRIBUTION.md](DISTRIBUTION.md).

---

## High priority

### 1. Passive measurement in production

**Why first.** Everything above is benchmarks or per-operation telemetry. None
answers the question that matters: *how much did this save me last week doing
real work?* The benchmark is an expensive proxy (40 minutes and real API money)
and its questions are the ones I chose, not the ones that arise while working.

**Status.** `shunt report` already exists and joins telemetry with OpenCode's own
accounting. What is missing is real usage time to observe. It reports a range
instead of a number because the upper bound assumes the content would have stayed
in context, which is a counterfactual, not a measurement.

**What is left.** No code. Use it for a few weeks and compare with what the
provider says.

### 2. Tests for the remaining pure functions

**Status.** Of eleven pure functions in the runtime, three have their own suites
(`coverage`, `economics`, `guards`, 80 checks). Missing: `verify-paths.ts` and
`output-shunt.ts`.

**Why it matters.** These are exactly the kind of code whose failure is silent.
If deterministic extraction of the error line in output summarising stops
capturing it, the summary still looks correct and the critical information
disappears without anything complaining.

### 3. Make `allowedProviders` bind the orchestrator too

**The gap.** It is enforced where worker profiles resolve, which covers the
reader and writer. The orchestrator's model is chosen by OpenCode and does not
pass through there, so marking a repository as "does not leave this machine"
**does not stop** a cloud orchestrator seeing it.

**Status.** `shunt doctor` warns in that state, and `shunt config` warns when
you choose it. Warning is not blocking.

**What it would cost.** The plugin knows the session provider in `chat.params`,
so the check is cheap. What is not cheap is deciding what to do on detection:
blocking the orchestrator entirely makes the repository unusable, and it is the
most delicate code path we have.

---

## Medium priority

### 4. Latency: the problem is not the one I said

I stated this as "parallelise batches". **I checked and batches do not exist: 0
of 35 real calls were ever split**, because the worker's context budget (1M on
Gemini) is enormous compared to 5–54 KB reads. That code would never run.

Measured properly, with the same 24 KB prompt: asking for 30 tokens takes 1.03s
and asking for 1,500 takes 5.19s. So **latency is driven by what the worker
writes, not what it reads**: roughly 2.5s fixed plus 5 ms per generated token.

That leaves two real levers and one discarded:

- **Parallelise per file** would give ~2x, because generation parallelises across
  independent requests. But it destroys cross-file synthesis, which is the main
  reason to use `bulk_read` ("how does this orchestrate between the workflow,
  its activities, and persistence"). Four independent summaries do not see the
  connections. **Not done for that reason**: speed does not compensate losing
  what the tool exists for.
- **Shorten the output** is linear and costs no quality if the format is inflated.
  Not yet measured how much padding there is.
- **On a single GPU is an open question.** Theory pulls both ways: decoding is
  bandwidth-limited, so serving several sequences amortises weight loading; but
  they share compute and KV cache. Only measurement decides, and it is the
  question of whether a shared Qwen on a company machine pays off with concurrent
  requests.

### 5. Local cache: three different things with the same name

"Local cache" means three things in this system and only one is what people
actually ask about.

**(a) Worker response cache — measured, not worth it.**

The idea: the same question on the same unchanged files returns the stored
summary. Key: content hash plus question.

This is no longer a guess. Over 56 real calls with the question recorded:

| | |
|---|---|
| Distinct questions | 52 of 56 |
| Exact question repeats | 4 (**7%**) |
| Total worker cost | $0.2351 |
| What a **perfect** cache would avoid | $0.0137 — **6% of worker** |
| Of the total bill | **0.11%** |

The 7% is a **generous ceiling for two reasons**: a real hit needs the same
question *and* unchanged files, which is stricter; and three of those four
repeats came from A/B tests where I ran the same question twice on purpose. In
normal use the rate is even lower.

The reasoning error behind it is worth recording: I was thinking the cache saved
"the whole call". It does — but **the whole call is 2% of the bill**. The summary
enters the expensive context exactly the same whether it comes from cache or from
the worker, so the other 98% is untouched. It is a **latency** improvement
(3–14 seconds per hit) dressed up as savings, and at 7% hit rate it is not much
as latency either.

**Verdict: discarded as a savings measure.** If it is ever built, it will be for
latency and must be justified on that basis.

**(b) Provider cache — 66% of the bill, already being attacked.**

This is where the money is, and it was never framed as "cache" by accident of
language. The bill breakdown is 46.6% cache writes and 19.3% cache reads:
**two thirds of spend is cache**, Anthropic's or Google's, not ours.

We cannot build it, because it belongs to the provider. But we influence it two
ways, and both are covered:

- **Every KB kept out of context is a cache write you do not pay, plus a read on
  every following turn.** That *is* the saving mechanism of the whole system.
  Saying we had not touched cache was wrong: the economic floor in
  `lib/economics.ts` is literally computed from the provider's cache write and
  read prices.
- **Do not break it.** A plugin that modified the prompt prefix on every request
  would invalidate cache and silently multiply the bill. Verified: the
  `chat.params` hook in `plugins/shunt.ts` **only reads** — it stores the
  session agent and model in a Map and returns no modified parameters. Read
  blocks append an error message at the end of the conversation, which *adds*
  to the prefix rather than altering it, so they do not invalidate anything either.

**(c) A repository map — the real gap.**

This is what is actually missing, and what separates this project from Spotify's
Portal. Today **every new session rediscovers the repository from scratch**:
runs the explorer again, summarises the same files again, pays the cache write
for the same content again. A persistent map — what modules exist, what each
does, where the boundaries are — would attack exploration turns and those
repeated writes, which is the big block.

Why it is not done: the hard problem is not building it but **staleness**. An
outdated map is worse than no map, because it sends the model to the wrong place
with confidence instead of sending it to look. Solving that (invalidation by
content hash, or regeneration on git changes) is the real work, and it is a
project, not a tweak.

**Of the three, this is the only one that genuinely remains open.**

### 6. A registry of installed repositories

`shunt update` works inside one repository, like git. That means **nothing tells
you that eight other repos are still on an old version**. A registry in
`~/.config` would fix that, at the cost of maintaining it.

The first was chosen for simplicity knowing what was lost. If this is used across
many projects at once, the calculus changes.

---

## Remaining work from the plan

External review received 11/09/2026, verified finding by
finding and reordered by cost and benefit. Version 3.4.0 shipped the P0 fixes
(path guards, coverage accounting, atomic writes, billing, config merge
behaviour, CI branch name, `stats` exit code, `report --since`). What follows
is still open, in order:

1. **Native permissions.** Tools access disk without going through
   `context.ask`, so plan mode and OpenCode's edit permissions do not bind them.
   This is the remaining P0. It changes the contract with OpenCode and has not
   been touched yet because it needs measuring first.
2. **Respect `context.abort`** instead of fabricating a custom timeout.
3. **Worktree in the tools**, not only in the plugin — required before npm plugin
   packaging moves the runtime out of the repo.
4. **Remove the 146% threshold from `replay`.** A one-time observed variance was
   turned into a universal rule for rejecting A/B tests, and that does not hold.
   Keep replay as simulation; drop the rule.
5. **Invalidate coverage by content**, not only by session.
6. **CI on macOS** and integration tests against a real OpenCode install.

---

## Low priority, or discarded

### What a real project install surfaced

Four defects no test suite would find, because all four consisted of the system
working and overcharging:

**The summary longer than the code.** A broad question over 17.7 KB of source
returned 27.5 KB of prose. `bulk_read` promised compression and nothing
enforced it, so delegation paid for a worker call and a round trip to put *more*
in the expensive context than reading the files would have. Fixed with a character
budget in the prompt and a 25% cap on the original, plus a cheap second pass if
it overruns. The same review went from $0.2549 to $0.2228, summary at 81%.

**`shunt update --force` ate the config.** `shunt.json` was shipped as if it were
code, so updating replaced it with the template. That left `writerProfile`
unset, which sent delegations to a profile nobody chose, which made Opus write
everything itself in silence. Config now installs once and is never touched again.

**The wizard offered models that did not exist.** It picked from the catalogue
without checking what Ollama actually had pulled, and `doctor` passed because it
checked the server responded, not that the model was there. Every delegation died
with a 404 and work fell back to the expensive model. The wizard now offers what
exists and `doctor` verifies the model, not the port.

**Blocking always costs.** The first attempt to stop re-reading already-summarised
files blocked all of them: five refusals bought five extra turns to keep out a
few kilobytes, and the review went from $0.25 to $0.47. One refusal costs one
turn, and a turn is what is expensive in this whole system. Blocking now has its
own economic threshold, from the same arithmetic as the delegation floor.

The lesson that repeats: **every mechanism that spends a turn has to justify
that turn**, and none of these failures threw an error.

### `delegate_edit` on business code — done

Was here waiting for "accumulated confidence". A test project provided it: the
same instruction, 26 docstrings across two business modules, cost **$0.3375 with
Opus writing them against $0.2065 delegating**, 39% less and 3,733 fewer output
tokens, tests still green, not a single line of code changed beyond the
docstrings.

What was missing was separating two risks that had been mixed. `delegate_write`
invents a whole file nobody reads, and the test allowlist is the right control
for that. `delegate_edit` changes a file that already exists and returns the
diff of what it moved, after copying, limiting change size, and running a parser:
there the control is review, not the allowlist. They now have different defaults,
and underneath sits a veto list (CI, migrations, lockfiles, `.opencode/` itself)
that no configuration can override.

### Tiered orchestrator

Orchestrating "find this, summarise that" does not need the most expensive model.
A cheap orchestrator for routine work and the good one for design would reduce
the 34% of output by lowering unit price instead of volume.

**Why not.** Switching orchestrator mid-session loses context, and deciding
*a priori* whether a task is routine is exactly the judgement you pay the
expensive model for. Without a better idea, it is complexity with uncertain
saving.

### Parallelise batches

**Discarded by data**, not difficulty. See item 4 above.

---

## What is still not measured

Worth stating plainly so it is not confused with what we know:

- The project has been used in two repositories, both mine.
- The criterion task (Opus 3/3, Gemini 3.1 Pro 2/3, Astra 3/3) is **one task
  and one pass per arm**. It is not a capacity measure.
- Per-operation compression figures (78–88%) and per-session bill savings
  (12–25%) measure different things and must not be presented together without
  saying which is which.
- Nobody has installed this on a machine that is not mine.

---

## What we are not doing

**Rewriting the Python CLI in TypeScript.** That is 3,721 lines and where all
the analysis lives: `config` opens `opencode.db` with SQL to measure how long
your conversations run, `costs` reconstructs the bill, `replay` re-evaluates past
decisions against other thresholds, `doctor` validates credentials. Rewriting buys
"you do not need Python" for people who already have a coding assistant installed.
The npx wrapper already covers people who do not want to think about Python.

If someone complains for real, reconsider. Until then it is 90% of the effort for
10% of the benefit.
