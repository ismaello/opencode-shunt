# Tutorial: what this is and how to use it

> Spanish: [TUTORIAL.es.md](TUTORIAL.es.md)

Written for someone who has never seen the project. You do not need to understand
language models to follow it.

---

## 1. The problem, in one sentence

When you ask a strong model (Claude, GPT, Gemini Pro) for something, **you pay
for every character that goes in and every character that comes out**. And a
lot of those characters never needed a strong model.

An analogy: you hire an architect at €200/hour for a renovation. The architect
is essential for where the wall goes. But if you also make them **read 400 pages
of building code out loud**, you are paying €200/hour for a reader. That is what
happens when an expensive model reads your whole repository to answer “where is
the user validated?”.

**This project puts an intern next to the architect.** The intern costs ~50×
less. The intern reads the 400 pages and says: “page 212, paragraph 3, and it
contradicts page 87”. The architect only gets that sentence and keeps deciding.

That is the whole idea. The rest of this document is how you set it up and what
it looks like when it works.

---

## 2. The three pieces

| Piece | Who | What they do | What they cost |
|---|---|---|---|
| **Orchestrator** | Your expensive model | Thinks, decides, reviews, writes the important code | Expensive. The only place worth paying frontier rates |
| **Worker** | A cheap or local model | Reads a lot and summarises, applies repetitive edits | Nearly free. Measured: **~2% of the bill** |
| **You** | You | Ask for things in plain language | — |

You talk **only to the orchestrator**. You never see the worker: the
orchestrator calls it when it should. You do not have to remember anything.

You fill the three roles when you configure. Setups that work:

- Claude Opus as orchestrator + Gemini Flash as worker
- Gemini 3.1 Pro as orchestrator + Gemini Flash Lite as worker (cheaper)
- GPT-6 Astra as orchestrator + Qwen on your own GPU as worker (worker is free)
- DeepSeek doing both roles

---

## 3. What you need before you start

1. **OpenCode installed.** That is where you type requests.
   → https://opencode.ai
2. **Credentials for an expensive model** for the orchestrator. One of:
   - Claude: `ANTHROPIC_API_KEY` or `opencode auth login`
   - Gemini: `gcloud auth application-default login` + `GOOGLE_CLOUD_PROJECT`
   - OpenAI: `OPENAI_API_KEY`
3. **A worker.** Either credentials for a cheap cloud model, or
   [Ollama](https://ollama.com) on your machine (free, but needs a GPU to be fast).
4. **Python 3.10+**, only to install. The runtime itself is TypeScript.

---

## 4. Install: four commands

```bash
pipx install opencode-shunt     # or: uv tool install … / npx opencode-shunt …

cd your-project
shunt init                      # copy the system into ./.opencode
shunt config                    # ask who does each role
shunt doctor                    # check that it actually works
```

From then on you work as usual:

```bash
opencode --agent orchestrator
```

### What `shunt config` asks

Three questions, and nothing else:

```
Measured from 126 of your own sessions: conversations run about 17k tokens,
and what you read stays in context for roughly 2 more turns.

This repository looks like: python (1)
Ready on this machine: google-vertex, ollama, anthropic

Who orchestrates? This one reasons, designs and reviews. Its own output is
the expensive part of the bill, which is the whole reason the other two exist.
  1. Anthropic (Claude) - claude-opus-5 (default)
  2. Anthropic (Claude) - claude-sonnet-4-6
  3. Google Gemini (Vertex AI) - gemini-3.1-pro-preview
  4. OpenAI - gpt-6-astra  [not configured on this machine]
  5. DeepSeek - deepseek-v4-pro  [not configured on this machine]
choice [1]:

Who reads in bulk? The job is obedience to a rigid format and exact line
citations, so cheap and fast wins here.
  1. Ollama (local GPU) - qwen3-coder:30b  free (default)
  2. Google Gemini (Vertex AI) - gemini-3.1-flash-lite  $0.25/M
choice [1]:

Who writes? ...
```

The models and prices in that list **are not hard-coded in our catalogue**: they
come from the table OpenCode maintains. A hand-maintained list always goes stale,
and here a stale price is not cosmetic — the point at which delegating pays is
computed from those numbers.

**Everything else it measures instead of asking.** It reads OpenCode’s database
for how long your conversations run, looks at your repository for language and
test layout, and from that computes the size at which delegating pays. Nobody
knows those facts about themselves, so asking would give worse answers than
measuring.

---

## 5. What files land in your project

After `shunt init` and `shunt config`:

```
your-project/
├── .opencode/
│   ├── agents/
│   │   └── orchestrator.md        ← instructions for the boss
│   ├── plugins/
│   │   └── shunt.ts               ← the guard: intercepts expensive reads
│   ├── tools/
│   │   ├── bulk-read.ts           ← tool: “read a lot and summarise”
│   │   ├── delegate-edit.ts       ← tool: “repetitive change”
│   │   └── delegate-write.ts      ← tool: “write this file”
│   ├── lib/
│   │   └── economics.ts           ← arithmetic for when delegating pays
│   ├── shunt.json                 ← project policy → DO commit this
│   └── shunt.local.json           ← this machine → do NOT commit (gitignored)
└── opencode.json                  ← OpenCode config (a block is merged in)
```

The two files that matter:

**`shunt.json`** — decisions about *the project*. Commit it so a teammate gets
the same rules:

```json
{
  "_roles": {
    "orchestrator": "anthropic/claude-opus-5",
    "reader":       "google-vertex/gemini-3.1-flash-lite",
    "writer":       "google-vertex/gemini-3.8-flash"
  },
  "writePaths": ["tests/**", "**/test_*.py"],
  "editPaths":  ["**/*.py", "**/*.js", "tests/**"],
  "economics": {
    "assumedConversationTokens": 18000,
    "remainingTurns": 2,
    "cacheWritePerMillion": 6.25,
    "cacheReadPerMillion": 0.5,
    "workerInPerMillion": 0.3,
    "workerOutPerMillion": 2.5
  }
}
```

- `writePaths`: where the worker may **create files from scratch**. Short on
  purpose: a file nobody reviews is only safe where a test run can judge it.
- `editPaths`: where it may **modify** files. Wider, because a change comes back
  as a reviewable diff.
- `economics`: the numbers that decide whether delegating pays. `shunt config`
  sets them by measurement; do not hand-edit them lightly.

**`shunt.local.json`** — your machine. Gitignored automatically, because
committing it either leaks something or breaks the next person to clone.

**API keys are never written to either file.** Profiles store the *name* of the
environment variable that holds the key, never its value.

---

## 6. Example flows

What actually happens. Six cases, from most common to rarest.

### Flow A — You ask about code that is large

**You write:**

```
How do we make sure one customer cannot see another’s data?
```

**Inside:**

```
1. The orchestrator does not know which files matter. It calls @explorer
   (cheap worker), which returns: services/runs.py, auth/tenant.py, api/router.py

2. The orchestrator calls:
      bulk_read(
        question = "How is tenant isolation enforced?",
        paths    = ["src/services/runs.py", "src/auth/tenant.py", "src/api/router.py"]
      )

3. The tool reads the 3 files from disk: 1,240 lines, 48 KB.
   It adds line numbers and sends them to the worker.
   >>> These 48 KB NEVER enter the expensive model. <<<

4. The worker answers in a rigid format.

5. The orchestrator receives only this (~5.8 KB):
```

```
COVERAGE:
- src/services/runs.py: analysed - starts and queries runs; tenant check lives here
- src/auth/tenant.py: analysed - extracts tenant from the token
- src/api/router.py: not relevant - routing only, no validation

FINDINGS

src/services/runs.py:112-118  _authorized_handle()
  Compares the token’s tenant with the workflow’s before returning the handle.
  That is the only place this check happens.

src/services/runs.py:203  signal_run()
  WARNING: calls client.get_workflow_handle() directly, skipping
  _authorized_handle(). Bypasses the tenant check.

---
bulk_read: 1240 lines / 48 KB analysed by gemini-2.5-flash in 6.2s.
These lines did not enter your context.
paths: 3 correct
```

**What you gain:** the expensive model saw 5.8 KB instead of 48 KB. **~88% less**
(median measured on real calls). With line numbers it can still open exactly
`runs.py:203` if it wants, spending 15 lines instead of 1,240.

---

### Flow B — The file is small

**You write:**

```
Read src/config.py and tell me which environment variables it uses
```

**What happens:** nothing special. `config.py` is 80 lines / 2 KB. The system
**lets it through** to the expensive model.

If the orchestrator tries to delegate it anyway, the tool declines and explains:

```
bulk_read declined: these files total 80 lines / 2.1 KB, below the 12.6 KB at
which delegating starts to pay for itself. Read them directly.
Delegating this would cost about $0.011 more than reading it.
```

**Why it matters:** delegating costs a round trip. Below a size, that costs more
than it saves. The threshold is not invented: it comes from your provider’s
prices and how long your conversations run. A system that delegates *everything*
costs more than having no system.

---

### Flow C — The orchestrator tries to read something large and is blocked

**You write:**

```
Review src/engine/pipeline.py and tell me if there are problems
```

`pipeline.py` is 2,100 lines / 80 KB. The orchestrator goes to read it whole.
**The guard cuts it before the request goes out:**

```
SHUNT: read of src/engine/pipeline.py blocked (2100 lines / 80 KB).
This would burn frontier context on bulk reading. Instead:
  - bulk_read(question, paths) to answer a question about these files locally, or
  - @explorer if you do not yet know which files matter, or
  - read with offset/limit if you already know the exact lines you need.
```

The orchestrator reads the message, takes the alternative, and calls `bulk_read`.
**You see none of this**: you see the answer, at roughly a tenth of the cost.

**Why a block and not advice:** advice the model has to remember to follow is
advice it sometimes will not. And when it does not, there is no warning: you get
a perfectly good answer with the full bill.

---

### Flow D — A repetitive change in many places

**You write:**

```
Add docstrings to every function in src/services/runs.py
```

That is 21 functions. The expensive part here is **not reading, it is writing**:
the expensive model would have to emit 21 docstrings *plus the surrounding code
twice* (search text and replace text).

**What happens:**

```
1. The orchestrator calls:
      delegate_edit(
        path        = "src/services/runs.py",
        instruction = "Add a one-line docstring to each public function.
                       Imperative style. Change nothing else."
      )

2. Check that src/services/runs.py is in editPaths.  ✓
3. Backup the file.
4. The worker returns the whole modified file.
5. >>> The diff is computed HERE, not reported by the worker. <<<
6. Automatic checks:
      - still valid Python?                         ✓
      - secrets inserted?                           ✓
      - code deleted under the guise of editing?    ✓
      - diff touching half the file (disguised rewrite)? ✓
7. Write. The orchestrator gets a short diff preview (~30 lines).
```

**What you gain: ~78% fewer output tokens** (measured on this exact case: 21
docstrings in a 308-line file, tests passing, file identical to the original
apart from the docstrings).

**The key point:** we compute the diff by comparing before and after. If the
worker says “I only added docstrings” but rewrote half the file, the diff shows
it and the change is refused. You do not have to trust the worker.

---

### Flow E — Create a new file

**You write:**

```
Write tests for the start_run function
```

```
1. The orchestrator calls:
      delegate_write(
        path            = "tests/test_runs.py",
        instruction     = "Tests for start_run: happy path, wrong tenant,
                           invalid inputs. Use pytest and conftest fixtures.",
        reference_paths = ["src/services/runs.py", "tests/conftest.py"]
      )

2. Is tests/test_runs.py in writePaths?   ✓
3. Does the file already exist? No. (If it did, backup first.)
4. Worker writes it. Syntax and secret checks.
5. Orchestrator receives: path, line count, test names.
   NOT the body of the file.
```

**What you gain: ~76% fewer output tokens.**

**Important:** `delegate_write` may only write under `writePaths`, which by
default is tests and scaffolding. The reason is honest: **a generated test that
passes can still be asserting the wrong thing.** A file nobody reviews is only
safe where running the suite can judge it. For real source code use
`delegate_edit`, because that returns a reviewable diff.

Underneath both sits a list **nobody can widen**: CI files, database migrations,
lockfiles and `.opencode/` are never touched by a worker even if you configure
otherwise.

---

### Flow F — Tests fail and the output is huge

**You write:**

```
Run the tests and fix whatever fails
```

The orchestrator runs `pytest`. Out come **1,800 lines**: 3 failures and 1,700
lines of noise, repeated traces and deprecation warnings.

**What happens:** before that output reaches the expensive model it is summarised.
With one rule: **the critical part is extracted mechanically and kept character
for character.** The error message and stack trace are never “paraphrased”,
because a paraphrased error is a useless error.

The orchestrator gets about 140 lines: the 3 failures complete and exact, plus a
count of the rest.

**What you gain: ~92% less** (median measured).

---

## 7. How you know it is working

This is the part that matters most.

```bash
shunt doctor     # is it set up correctly? Yes/no, and what to fix
shunt costs      # write a human bill: what this repo has cost, and where
shunt stats      # what each operation saved
shunt report     # estimated savings across real sessions
shunt replay     # what if I change a threshold? Free, no API spend
```

### Why `shunt doctor` is not decoration

**Every way this system breaks is silent, and every way falls on the expensive
side.** These three are verified, not hypothetical:

| If this happens… | …what you see |
|---|---|
| Malformed `shunt.json` (extra comma) | OpenCode starts, answers normally, says nothing. The system simply is not there |
| Worker profile does not exist | `bulk_read` fails, the orchestrator reads the files itself, gives a great answer, bills full price |
| Orchestrator is on the exempt list | Nothing is ever shunted. And because the exemption check runs *before* telemetry, there is no record |

So: **a broken install and an unused one look identical — an empty report.**
Nothing inside the system can tell them apart. That is what `doctor` is for, and
why it checks role assignment, not only that files exist.

When something is wrong it says what and how to fix it:

```
[ FAIL ] vertex credentials expired
           ADC token rejected: invalid_rapt.
           Fix: gcloud auth application-default login
```

### `shunt costs`: the bill in plain language

Writes Markdown for spend in **this** repository:

```markdown
## Summary

- **Total spend: $12.18** across 96 sessions
  - orchestrator: $11.95
  - workers: $0.2297 in 46 calls (1.9%)
```

Then splits by token class — output, cache write, cache read — because each is
attacked by a different tool and an undifferentiated total does not tell you what
to do next.

Orchestrator spend comes from OpenCode’s accounting, not our estimate. Worker
spend is computed from our telemetry, because worker calls are direct HTTP and
create no OpenCode session: **they do not appear on any invoice**. Counting only
what OpenCode sees put the worker at 0.8% when the real figure is ~2%, and the
error flattered the system.

---

## 8. When this does NOT help you

Said plainly, so you do not waste time:

- **If your repository is small.** If nothing you read reaches ~12 KB, there is
  nothing to delegate and the system will do nothing. It does not hurt; it does
  not help.
- **If what costs money is thinking, not reading.** Orchestrator reasoning is
  about a third of the bill and is untouched — correctly, because that is what
  you are paying for.
- **If you cannot tolerate 3–14 seconds** when a delegation runs. That latency
  is dominated by the worker *writing* the summary, not reading the input.
- **If your code must not leave the machine.** Possible, but then the
  orchestrator must be local too. `allowedProviders` binds workers, **not** the
  orchestrator: OpenCode chooses the orchestrator model and this system does not
  override it. `doctor` warns if you are in that state thinking you are protected.

---

## 9. Typical problems

**“I don’t notice any difference.”**
Run `shunt doctor`. If it says 0 delegations, either it is mis-installed or you
have not asked for anything large enough. Those two look the same; `doctor` is
what distinguishes them.

**“It got more expensive.”**
Look at `shunt costs`. If there are failed delegations, that is why: when a
delegation fails, the work silently returns to the expensive model. And if the
worker is writing summaries longer than the code it summarises, `shunt stats`
shows that as negative saving.

**“The worker is wrong.”**
It will be, sometimes. Every receipt says so, and the orchestrator prompt is
explicit that worker output is **a lead to verify, not a conclusion**. Syntax is
checked, write reach is limited, backups are taken and bad edits are refused —
but none of that is a code review.

**“I want to change model.”**
`shunt config` again. It rewrites prompts, economics and exemptions so they stay
in step. Your `writePaths`, `editPaths` and cloud policy survive, and
`shunt update` never overwrites `shunt.json`.

**“I edited shunt.json by hand and now it doesn’t work.”**
`shunt doctor` tells you where. The most common failure is that the system does
not load and says nothing.

---

## 10. Five-line summary

1. Install with `pipx install opencode-shunt` (or `npx`), then in your project:
   `shunt init`, `shunt config`, `shunt doctor`.
2. Work as before: `opencode --agent orchestrator`.
3. When something is bulky, it goes to the cheap worker alone. You do nothing.
4. `shunt costs` tells you what you spent; `shunt doctor`, whether it is working.
5. What you save is the bulk. Judgement is still paid for, and that is correct.

---

## Where next

- [README](README.md) — overview and measured figures
- [How it works](docs/HOW_IT_WORKS.md) — the full system in more detail
- [Real results](docs/REAL_RESULTS.md) — an app built with this, what it cost, and
  the silent defects that only showed up in real use
- [Distribution](docs/DISTRIBUTION.md) — publishing to PyPI and npm
