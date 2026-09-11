Spanish: [COMO_FUNCIONA.es.md](COMO_FUNCIONA.es.md)

# How it works

A guide to what opencode-shunt does, why, and how to check that it is actually
saving you money. For installation, see the [README](../README.md).

> **First run.** Three commands: `shunt init` puts the runtime in `.opencode/`,
> `shunt config` decides who orchestrates, reads and writes, and `shunt doctor`
> confirms it works. That last step is not a formality — this system fails
> quietly and in the expensive direction, so a broken install and an unused one
> look identical.

---

## The problem

When you ask a frontier model to work on a repository, the first thing it does
is read code. A lot of it. Every line enters its context window and gets billed.

The cost is not the whole story. **Context is finite and degrades.** A model that
has absorbed hundreds of thousands of lines to find one function reasons worse
about the bug in front of it than one that arrived at that function directly.
You are paying a high price for work that does not require judgement: searching,
filtering and summarising.

The idea is simple: **let a cheap worker handle volume; let the frontier model
handle judgement.**

---

## Three roles, and only one is worth paying for

| Role | Job | What it needs |
|---|---|---|
| Orchestrator | Reasons, designs, reviews, decides | Capability. This is where a frontier model earns its price, and the only place it does. |
| Reader | Summarises code in bulk | Obedience to a rigid format and exact line citations. Cheap and fast wins. |
| Writer | Generates tests, applies mechanical edits | Genuinely harder: the result has to compile and match your conventions. |

`shunt config` asks which model fills each role and works out the rest. Any mix
works: Claude orchestrating with Gemini Flash reading, a local model on your own
GPU via Ollama, DeepSeek doing both, an OpenCode Zen model — the worker layer
speaks three API shapes (Ollama, OpenAI-compatible, Vertex) rather than knowing
about vendors, so adding a provider is configuration, not code.

Then use OpenCode as normal:

```
opencode --agent orchestrator
```

---

## The pieces

### `bulk_read` — the central tool

A tool the orchestrator calls when it needs to understand code without ingesting
it:

```
bulk_read(
  question = "Where are transactions deduplicated and what can fail?",
  paths    = ["src/matching.py", "src/bq.py", "src/workflows.py"]
)
```

What happens inside:

```
Orchestrator asks
     │
     ▼
bulk-read.ts  ── reads files from DISK (they never enter the orchestrator)
     │
     ├─ rejects secrets (.env, *.pem, credentials*)
     ├─ rejects binaries
     ├─ checks paths stay inside the repository
     ├─ adds line numbers to every line
     └─ chunks if content exceeds the worker's context budget
     │
     ▼
Worker model  ── analyses and responds
     │
     ▼
Orchestrator receives ONLY the summary
```

The first arrow is the point. `bulk-read.ts` reads from disk directly, so **the
file contents never enter the expensive conversation**. It is not that the
orchestrator reads them and forgets — they never arrive.

The worker returns, for each finding: file, symbol, exact line range, one
sentence of evidence, and a confidence level. The orchestrator uses that map to
read only the lines it actually needs, with `offset` and `limit`.

**Answer size cap.** The summary is capped at a quarter of the source it
describes, and a second cheap pass shortens it if the worker overruns. Without
that cap, a broad question can produce a delegation that puts *more* into the
expensive context than reading the files would have.

**Hard limits.** Up to 20 files per call. Secret paths, binaries and paths
outside the repository are refused.

### Read blocking — the guard

The plugin refuses to let the orchestrator read a large file whole, and tells it
what to do instead:

```
SHUNT: refusing to read src/engine/chat/__init__.py in full (548 lines).
This would burn frontier context on bulk reading. Instead:
  - bulk_read(question, paths) ...
  - @explorer ...
  - read with offset/limit ...
```

Bulk reading you have to remember to delegate is bulk reading you will forget to
delegate. Prompts asking the model to behave do not survive a long session; a
refusal does.

**When it blocks:**

| Situation | Result |
|---|---|
| Read a file over 400 lines | Blocked |
| Read a file over 40 KB | Blocked |
| `cat` / `less` / `more` on a large file | Blocked |
| `head -n 5000 file` | Blocked |
| Read with `offset` and `limit` of 400 lines or less | Allowed |
| Read a small file | Allowed |
| `head -n 20 file` | Allowed |
| Any session running on an exempt model | Always allowed |

That last row matters. The shunt exists to protect expensive context, not to
stop cheap workers from reading. Exemptions are keyed by **model**, not
provider — because one vendor can hold two roles (Gemini Pro orchestrating with
Gemini Flash reading is normal), and exempting the provider would silently
switch the whole system off.

**Re-read blocking.** The plugin also refuses a full re-read of a large file
the worker already summarised in this session. Delegating and then reading the
file anyway puts the content in the context regardless, on top of the summary
you paid for. This refusal has its own break-even from the same economics as
the delegation floor, and it is deliberately high: blocking every re-read
regardless of size can cost more than it saves.

**Modes.** By default the shunt enforces (`SHUNT_MODE=enforce`). Set
`SHUNT_MODE=observe` to record what would have been blocked without blocking.
`SHUNT_MAX_LINES` and `SHUNT_MAX_BYTES` override the fixed thresholds.

### Output summarising

Large tool output — a 400-line test failure, a big diff — gets condensed before
it reaches the expensive context. The critical part (the error, the stack trace,
hunk headers) is extracted mechanically and preserved character for character.
The worker model only summarises the surrounding noise.

This runs automatically on large `bash` results. Thresholds are separate from
read thresholds because a 400-line command output is often the answer itself.

### `delegate_write` — generate without emitting

Creates a file without the orchestrator writing its body. Pass the module under
test and an existing test file as `reference_paths`, and the worker copies the
repository's conventions. The orchestrator receives a receipt: path, line count,
symbols defined, and whether syntax checked out.

**Reach is deliberately narrow.** By default, `delegate_write` is confined to
tests, fixtures and scaffolding — code whose correctness a test run can judge,
and whose being wrong does not quietly change what the product does. A file
nobody reads is only safe where a test run can judge it.

**Guards:**

| Guard | What it prevents |
|---|---|
| Allowlist (`writePaths`) | Worker touching business logic |
| No silent overwrite | Accidental replacement of existing files |
| Backup | Any write being irreversible |
| Secret scan | Credentials in generated content |
| Syntax check | Broken files landing on disk (reported honestly) |

### `delegate_edit` — mechanical edits at scale

Applies the same mechanical change at many sites without the orchestrator
emitting every search-and-replace pair.

**Not for single small edits.** OpenCode's edit tool works by search and replace,
so changing one line already costs only that line. What costs money is
repetition: each site needs its surrounding context emitted twice, as the text
to find and the text to replace it with.

**The diff is computed here, not reported by the worker.** The worker returns
the whole file; the tool computes the diff. It cannot misreport its own edit,
and a rewrite dressed up as a tidy-up shows up as a diff touching most of the
file — which is refused.

**Reach is wider than `delegate_write`.** `delegate_edit` reaches source code,
because the change comes back as a reviewable diff. Underneath both tools sits
a list nothing can widen: CI definitions, migrations, lockfiles and
`.opencode/` itself.

**Guards:**

| Guard | What it prevents |
|---|---|
| Allowlist (`editPaths`) | Edits outside permitted paths |
| Abbreviation detection | **Deleting code** — models return "rest unchanged" and that would be written to disk |
| Maximum change fraction | Rewrites and reformatting smuggled alongside a small change |
| Syntax check | Broken files; reverted automatically if they do not parse |
| Backup | Any edit being irreversible |

### `explorer` — when you do not know where to look

A subagent for when you do not yet know which files matter:

> "Find where transaction checksums are calculated."

It searches with `grep` and `glob`, reads what it needs, follows references, and
returns a map: paths, symbols, line ranges, relationships. It cannot edit
anything. It runs in its own session, so everything it reads stays there and
does not contaminate the orchestrator's context.

---

## The economic floor

Delegating costs a round trip, and below a certain size that costs more than it
saves. The floor is **computed from prices**, not guessed, in `lib/economics.ts`.

The arithmetic has two sides:

- **Saved:** content that never enters the context — the cache write it avoids,
  plus the cache read it avoids on every following turn, because anything left
  in context is re-sent for the rest of the session.
- **Paid:** the extra round trips delegation adds, each re-sending the whole
  conversation at the cache-read rate, **plus the worker's own cost**.

That last term matters. Omitting worker cost made delegation look free on the
cheap side and biased every marginal decision towards delegating. Worker cost is
not large — around a tenth of the saving at break-even with a cloud Flash
model, zero for a model on your own hardware — but it is the term whose omission
always flatters the system.

**Prices come from OpenCode's model table**, read at config time. The baked-in
catalogue is a fallback only. Stale prices fail silently: the delegation floor
shifts without an error, and every marginal decision shifts with it. The table
lists every reseller of a model, not one price per vendor; among unknown
resellers the dearest quote wins, so a model believed to be free drives the
floor to zero and delegates everything.

**The floor moves with the orchestrator**, and that is the point. Delegating
exists to protect an expensive context, so a cheaper orchestrator means less
worth protecting, and the break-even where `bulk_read` starts refusing rises on
its own. It is driven by the ratio between what a provider charges to put a token
in cache and to re-send it — not the headline input price.

Below the floor, the tool declines and shows the arithmetic:

```
bulk_read declined: these files total 163 lines / 5.1 KB, below the 12.6 KB at
which delegating starts to pay for itself.
...delegating this would cost about $0.013 more than reading it.
```

Two values in `.opencode/shunt.json` describe **how you work**, not what things
cost:

```json
"economics": {
  "assumedConversationTokens": 25000,
  "remainingTurns": 3
}
```

Longer conversations raise the floor (extra turns get dearer). Content that
survives more turns in context lowers it (delegating pays off sooner).
`shunt config` reads both from OpenCode's own database when it can.

---

## Exemptions: by model, not provider

The shunt decides who may read freely by comparing the **exact model** serving
a session against the `bulkExempt` list in `shunt.json`. Entries are either a
provider (`ollama`) or a specific model (`google-vertex/gemini-2.5-flash`).

This used to be keyed by provider, which was a serious bug: as soon as one
vendor occupied two roles — Gemini Pro orchestrating with Gemini Flash reading —
exempting the provider exempted the orchestrator too, and the whole system
switched off in silence. `shunt doctor` fails loudly if the orchestrator ends up
on that list.

If the plugin cannot identify a session, it **lets the read through**. Blocking
by mistake is worse than missing one.

---

## Telemetry

Every operation appends one JSON line to
`~/.local/share/opencode-shunt/telemetry.jsonl`:

- Tool or event name, agent, provider, model, session id
- For reads: file, lines, bytes, verdict (blocked, allowed, etc.)
- For `bulk_read`: content size vs returned size, worker profile, duration,
  coverage statistics
- For output shunting: raw size vs summary size, kind (pytest, git-diff, generic)
- For `delegate_write` / `delegate_edit`: path, diff size, guard outcomes

**Never stored:** file contents, full prompts, or any credential.

---

## Daily use

Nothing changes in how you work. Open OpenCode and ask what you want. A typical
flow:

```
1. Orchestrator does not know where the code is
   └─► @explorer          worker searches the repo
                          returns files and line ranges

2. Orchestrator needs to understand those files
   └─► bulk_read(...)     worker reads 1,200 lines
                          returns a summary with exact ranges

3. Orchestrator reads only lines 107–161 of matching.py
   (bounded read: the shunt allows it)

4. Orchestrator reasons, finds the cause, proposes the fix

5. Mechanical change goes to delegate_edit; comes back as a diff for review
```

### Commands worth knowing

```bash
shunt doctor     # is it set up correctly? Run when something smells wrong
shunt costs      # writes shunt-costs.md: what this repo has cost, and where
shunt stats      # per-operation savings from the shunt's own telemetry
shunt report     # session-level savings, joined against OpenCode's accounting
shunt replay     # re-judge recorded reads against a different threshold. Free
shunt config     # change who orchestrates, reads and writes
shunt bench      # A/B with the shunt on and off. Slow, and it costs real money
```

**`shunt costs`** answers "what is this costing me?" It writes a Markdown file
scoped to the repository, splitting the bill by token class — output, cache
write, cache read — because those are attacked by different tools and an
undifferentiated total tells you nothing about what to do next. Spend comes
from OpenCode's own accounting; the avoided column is an estimate and is
labelled as one. It also surfaces failed worker calls, which matter more than
they look: when a delegation fails, the work silently returns to the expensive
model.

**`shunt stats`** is per-operation and deterministic — the one to iterate
against when tuning thresholds or worker choice.

**`shunt report`** joins telemetry with OpenCode's session records to estimate
savings in real use. It reports a range because the upper bound assumes the
content would have stayed in context for the whole session.

**`shunt replay`** re-judges reads already in your telemetry against a
threshold you name. It costs nothing, has no variance, and reports a modelled
ceiling rather than a measurement — and it says which of the two you are
looking at. Reach for this before changing a threshold; session-level A/B
(`bench`) cannot resolve effects smaller than the noise between runs.

### Switching orchestrator

Run `shunt config` again. You do not edit the model by hand anywhere: the wizard
rewrites the agent prompts, the provider block in `opencode.json`, the exemption
list and the delegation economics together. Getting any one of those out of step
with the others is how this system breaks quietly. Your `writePaths`, `editPaths`
and cloud policy survive; `shunt update` never touches `shunt.json`.

---

## Measuring savings

Two numbers that sound like they should agree often do not, and both are real.

**Per-operation savings** (`shunt stats`) compare the characters the orchestrator
would have ingested by reading files directly against what it ingested instead.
This is what each delegation saves on the content itself. Median figures in the
high double digits are typical for `bulk_read` and output shunting when the
source is large enough to be above the economic floor.

**Per-session savings** (`shunt report`, `shunt bench`) measure whole
conversations. This number is always lower, because a session includes the
prompts, the orchestrator's reasoning, and the turns that delegation itself adds.
A per-operation saving of, say, 80% on the content does not translate to 80%
off the session bill — and that is not a contradiction.

**Token savings vs dollar savings** diverge further. Output tokens cost more than
input tokens, and the read shunt targets input. A session can show a large token
reduction and a modest cost reduction; the context space you recover may matter
more than the dollars on a single task.

**How to measure honestly:**

- Use **`stats`** to check that individual delegations are doing their job.
- Use **`report`** to estimate what that adds up to across real sessions.
- Use **`replay`** before changing thresholds — free, no variance.
- Use **`bench`** only when you need a whole-session A/B and can afford both
  the time and the money. Expect high variance between runs of the same task.

Do not treat benchmark numbers from one repository as a product promise. Savings
grow with repository size, conversation length, and how much of your work
involves reading or generating bulk content. Small files below the economic
floor correctly see no benefit — the system is working as designed when it
declines them.

---

## Run `shunt doctor`

Not a formality. **Every way this system breaks, it breaks quietly and in the
expensive direction:**

- Malformed `shunt.json` means the plugin never loads. OpenCode starts, answers
  normally, and says nothing.
- A profile name that does not resolve means `bulk_read` fails. The orchestrator
  quietly reads the files itself, gives a perfectly good answer, and bills full
  price.
- An orchestrator whose model sits in the exempt list means nothing is ever
  shunted — and because the exemption check runs before any telemetry is written,
  that setup produces no records at all.

A broken install and an unused one look identical: an empty report. That is what
`doctor` is for.

---

## Honest limits

**It saves input, mostly.** Output is a significant share of the bill and
`delegate_write` and `delegate_edit` only reach the part of it that is bulk. The
orchestrator's own reasoning is untouched, and it should be — that is what you
are paying for.

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

**Latency.** A delegation costs a few seconds to tens of seconds, dominated by
the worker writing its response rather than reading the input.

**It does not help on small work.** Below the economic floor, delegating costs
more than reading directly. Short conversations with no bulk reading have
nothing to save.

---

## What the system does NOT do

- **It does not replace the orchestrator.** The goal is not for the worker to do
  the thinking; it is for the frontier model to stop spending intelligence on
  tasks that do not require it.
- **It does not review worker output.** Guards catch syntax errors, secret leaks,
  and wholesale rewrites; they do not judge correctness. That is the
  orchestrator's job.
- **It does not enforce cloud policy on the orchestrator.** `allowedProviders`
  applies to worker profiles only.
- **It does not reduce the orchestrator's reasoning cost.** That is the part
  worth paying for.
- **It is not magic on small repositories.** The savings grow with how much
  bulk reading and bulk writing your work involves.

---

## Configuration files

`shunt config` writes two files, deliberately separate:

- **`.opencode/shunt.json`** — shared policy. Path allowlists, provider
  restrictions, economics, exemptions. Decisions about the repository.
  **Commit this.**
- **`.opencode/shunt.local.json`** — this machine. Active profile, local
  endpoints, project ids. Gitignored automatically.

API keys are never written to either. Profiles record the *name* of the
environment variable holding a key, never its value.

---

## Further reading

- [Tutorial](../TUTORIAL.md) — worked flows showing what happens to a request
- [README](../README.md) — installation and quick overview
- [Distribution](DISTRIBUTION.md) — publishing to PyPI and npm
- [Real results](REAL_RESULTS.md) — a real build, what it cost, and defects
  that only showed up under use
