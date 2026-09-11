"""Work out the configuration, asking as little as possible.

The guiding rule: every question replaced by a measurement makes this better,
because people answer wrongly about their own habits. Nobody knows how long
their conversations run or how many turns a file survives in context, and those
are exactly the two numbers that move the delegation break-even most. They are
also sitting in OpenCode's database, so they get measured rather than asked.

The same goes for the repository. Which test directories exist, which language
dominates, whether there is a virtualenv - all readable. Asking would only
introduce a chance to get it wrong.

What is left is genuinely a decision rather than a fact, and there are only
three: who orchestrates, who reads, who writes. Plus one policy question, which
providers are allowed to see this code, that no measurement can answer because
it is somebody's rule and not a property of the machine.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import statistics
import subprocess
import sys
from dataclasses import dataclass, field

from . import paths, providers
from .providers import CATALOGUE, Provider


@dataclass
class Measured:
    """Facts gathered rather than requested."""

    conversation_tokens: int = 25_000
    remaining_turns: int = 3
    sessions: int = 0
    models_seen: list[str] = field(default_factory=list)
    languages: dict[str, int] = field(default_factory=dict)
    test_globs: list[str] = field(default_factory=list)
    source_globs: list[str] = field(default_factory=list)
    is_git: bool = False
    available: list[str] = field(default_factory=list)

    @property
    def measured_habits(self) -> bool:
        return self.sessions > 0


def measure_habits(measured: Measured) -> None:
    """Read conversation size and session length from OpenCode's own accounting.

    Cache reads are the best available proxy for conversation size: every turn
    re-sends the conversation, and that is what gets billed as a read. Dividing
    by the number of assistant turns gives the average size of the context those
    turns carried.
    """
    database = paths.opencode_db()
    if not database.is_file():
        return

    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT s.model, s.tokens_cache_read,"
                " (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id)"
                " FROM session s"
            ).fetchall()
    except sqlite3.Error:
        return

    turn_counts: list[int] = []
    conversation_sizes: list[float] = []
    models: dict[str, int] = {}

    for model, cache_read, messages in rows:
        name = model or ""
        if name.startswith("{"):
            try:
                name = json.loads(name).get("id", "")
            except json.JSONDecodeError:
                name = ""
        if name:
            models[name] = models.get(name, 0) + 1
        if not messages or messages < 3:
            continue
        turn_counts.append(messages)
        # Assistant turns are roughly half the messages; each one re-sent the
        # conversation once.
        turns = max(messages // 2, 1)
        if cache_read:
            conversation_sizes.append(cache_read / turns)

    measured.sessions = len(turn_counts)
    measured.models_seen = [name for name, _ in sorted(models.items(), key=lambda kv: -kv[1])[:6]]
    if conversation_sizes:
        measured.conversation_tokens = int(round(statistics.median(conversation_sizes) / 1000) * 1000)
    if turn_counts:
        # What a file read midway through survives for the rest of the session.
        measured.remaining_turns = max(int(statistics.median(turn_counts) // 2), 1)


def measure_repo(repo: pathlib.Path, measured: Measured) -> None:
    """Language mix and where the tests live, so the allowlist fits the repository."""
    extensions = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".js": "javascript", ".rs": "rust", ".java": "java", ".rb": "ruby"}
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".opencode", "target"}
    counts: dict[str, int] = {}
    test_dirs: set[str] = set()

    for path in repo.rglob("*"):
        parts = set(path.relative_to(repo).parts)
        if parts & skip:
            continue
        if path.is_dir() and path.name in ("tests", "test", "spec", "__tests__"):
            test_dirs.add(str(path.relative_to(repo)))
            continue
        if path.is_file() and (language := extensions.get(path.suffix)):
            counts[language] = counts.get(language, 0) + 1

    measured.languages = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    measured.is_git = (repo / ".git").exists()

    globs = sorted(f"{directory}/**" for directory in test_dirs)
    dominant = next(iter(measured.languages), None)
    # Conventions that put tests beside the code, which a directory scan misses.
    if dominant == "python":
        globs += ["**/test_*.py", "**/*_test.py", "**/conftest.py", "**/fixtures/**"]
    elif dominant == "go":
        globs += ["**/*_test.go"]
    elif dominant in ("typescript", "javascript"):
        globs += ["**/*.test.ts", "**/*.test.tsx", "**/*.spec.ts", "**/*.test.js"]
    if not globs:
        globs = ["tests/**", "test/**"]
    measured.test_globs = sorted(set(globs))

    # What delegate_edit may change. Wider than the test globs on purpose: that
    # tool returns a reviewable diff of an existing file, so the control is the
    # review, not the allowlist. Holding it to tests measurably cost 39% on a
    # mechanical edit. Extensions actually present, so the file says what this
    # repository is rather than listing languages it does not use.
    by_language = {
        "python": ["**/*.py"],
        "typescript": ["**/*.ts", "**/*.tsx"],
        "javascript": ["**/*.js", "**/*.jsx"],
        "go": ["**/*.go"],
        "rust": ["**/*.rs"],
        "java": ["**/*.java"],
        "ruby": ["**/*.rb"],
    }
    source: list[str] = []
    for language in measured.languages:
        source += by_language.get(language, [])
    measured.source_globs = sorted(set(source))


def detect_available(measured: Measured) -> None:
    """Which providers this machine could actually use right now."""
    found = []
    for provider in CATALOGUE.values():
        if provider.kind == "ollama":
            if _ollama_up(provider.base_url or ""):
                found.append(provider.id)
        elif provider.kind == "vertex":
            if (os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_VERTEX_PROJECT")) and shutil.which(
                "gcloud"
            ):
                found.append(provider.id)
        elif all(os.environ.get(variable) for variable in provider.requires):
            found.append(provider.id)
    # OpenCode may hold Anthropic credentials of its own, which no variable shows.
    if "anthropic" not in found and _opencode_has_auth("anthropic"):
        found.append("anthropic")
    measured.available = found


def _ollama_up(url: str) -> bool:
    import urllib.error
    import urllib.request

    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _opencode_has_auth(provider: str) -> bool:
    for candidate in (
        pathlib.Path.home() / ".local/share/opencode/auth.json",
        pathlib.Path.home() / ".config/opencode/auth.json",
    ):
        if candidate.is_file():
            try:
                if provider in json.loads(candidate.read_text()):
                    return True
            except (json.JSONDecodeError, OSError):
                continue
    return False


def ollama_models(url: str) -> list[str]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=5) as response:
            payload = json.loads(response.read())
        return [model["name"] for model in payload.get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return []


# --- asking, for the few things that are decisions ---------------------------


def interactive() -> bool:
    """Whether there is a human on the other end of stdin.

    Without this check the wizard called input() regardless and a scripted or
    piped run died on an EOFError traceback - including `config --dry-run`,
    which is the one invocation whose whole point is to be safe to run
    anywhere. A traceback is the worst possible answer here, because it looks
    like the tool is broken rather than waiting for an answer nobody can give.
    """
    return sys.stdin.isatty()


def choose(prompt: str, options: list[tuple[str, str]], default: int = 0) -> str:
    """A numbered choice. Returns the value of the chosen option."""
    print(f"\n{prompt}")
    for index, (_, label) in enumerate(options, 1):
        marker = " (default)" if index - 1 == default else ""
        print(f"  {index}. {label}{marker}")
    if not interactive():
        print(f"choice: {default + 1} (no terminal, taking the default)")
        return options[default][0]
    while True:
        answer = input(f"choice [{default + 1}]: ").strip()
        if not answer:
            return options[default][0]
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print("Pick a number from the list.")


def confirm(prompt: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    if not interactive():
        print(f"{prompt} [{suffix}]: {'y' if default else 'n'} (no terminal)")
        return default
    answer = input(f"{prompt} [{suffix}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def role_options(role: str, measured: Measured) -> list[tuple[str, str]]:
    """Providers for a role, the ones this machine can already reach first."""
    candidates = providers.for_role(role)
    candidates.sort(key=lambda p: (p.id not in measured.available, p.roles.index(role)))
    options = []
    for provider in candidates:
        # For a local server, offer what it has rather than what the catalogue
        # imagines. Choosing a model Ollama was never given produces a 404 on
        # every delegation, which the orchestrator reads as "worker down" and
        # answers by doing the work itself, at full price and without complaint.
        installed = (
            ollama_models(provider.base_url or "") if provider.id in measured.available and provider.kind == "ollama" else None
        )
        for model in _models_for(provider, role, installed):
            ready = "" if provider.id in measured.available else "  [not configured on this machine]"
            price = ""
            if role != "orchestrator":
                # Per model, not per provider. Gemini Pro shown at Flash's price
                # invites picking a worker four times dearer than the number on
                # screen, which is the one mistake this whole system exists to
                # avoid, made while reading a screen meant to prevent it.
                per_million = providers.prices_for(provider, model)["price_in"]
                price = f"  ~${per_million:g}/M in" if per_million else "  free"
            options.append((f"{provider.id}/{model}", f"{provider.label} - {model}{price}{ready}"))
    return options


def _models_for(provider: Provider, role: str, installed: list[str] | None) -> list[str]:
    """Catalogue models for a role, narrowed to what is really there when we can tell."""
    catalogue = list(provider.models.get(role, ()))
    if installed is None:
        return catalogue
    # Keep catalogue order for the ones that are present, then anything else the
    # machine has, since a pulled model the catalogue never heard of still works.
    present = [m for m in catalogue if m in installed or any(i.split(":")[0] == m for i in installed)]
    extra = [m for m in installed if m not in present and m not in catalogue]
    return present + extra


@dataclass
class Choices:
    orchestrator: str
    reader: str
    writer: str
    allowed: list[str] | None
    measured: Measured


def ask(repo: pathlib.Path, measured: Measured, assume_yes: bool = False) -> Choices:
    print("=" * 72)
    print("Three roles to fill. The orchestrator is the only one worth paying for.")
    print("=" * 72)

    if measured.measured_habits:
        print(
            f"\nMeasured from {measured.sessions} of your own sessions: conversations run about "
            f"{measured.conversation_tokens // 1000}k tokens,\nand what you read stays in context for "
            f"roughly {measured.remaining_turns} more turns. Those set the point at which\ndelegating "
            f"starts to pay, so they are measured rather than guessed."
        )
    else:
        print(
            "\nNo OpenCode history to measure yet, so the break-even uses defaults taken from "
            "sixty\nreal sessions. Run 'shunt config' again later and it will use your own numbers."
        )

    if measured.languages:
        summary = ", ".join(f"{language} ({count})" for language, count in list(measured.languages.items())[:3])
        print(f"\nThis repository looks like: {summary}")
        print(f"Generated files will be confined to: {', '.join(measured.test_globs[:4])}" + (" ..." if len(measured.test_globs) > 4 else ""))
    else:
        print("\nThis repository has no source files yet, so the test allowlist uses conventions.")

    if measured.available:
        print(f"\nReady on this machine: {', '.join(measured.available)}")

    if assume_yes:
        return _defaults(measured)

    orchestrator = choose(
        "Who orchestrates? This one reasons, designs and reviews. Its own output is\n"
        "the expensive part of the bill, which is the whole reason the other two exist.",
        role_options("orchestrator", measured),
    )
    reader = choose(
        "Who reads in bulk? Summarises code for bulk_read. The job is obedience to a\n"
        "rigid format and exact line citations, so cheap and fast wins here.",
        role_options("reader", measured),
    )
    writer = choose(
        "Who writes and edits? Generates tests and applies mechanical edits. Harder\n"
        "than summarising, because the result has to compile and match conventions.",
        role_options("writer", measured),
    )

    allowed = None
    print()
    if not confirm("May this code be sent to cloud providers?", default=True):
        allowed = ["ollama"]
        if not any(p.startswith("ollama") for p in (orchestrator, reader, writer)):
            print(
                "\nNote: that restriction is enforced on the reader and the writer, but not on\n"
                "the orchestrator, whose model OpenCode chooses. With a cloud orchestrator the\n"
                "code still leaves the machine. Pick a local orchestrator if it has to be real."
            )

    return Choices(orchestrator, reader, writer, allowed, measured)


def _defaults(measured: Measured) -> Choices:
    def first(role: str) -> str:
        options = role_options(role, measured)
        if not options:
            raise SystemExit(f"no provider available for the {role} role")
        return options[0][0]

    return Choices(first("orchestrator"), first("reader"), first("writer"), None, measured)


# --- writing it out ----------------------------------------------------------


def _split(reference: str) -> tuple[Provider, str]:
    provider_id, model = reference.split("/", 1)
    return CATALOGUE[provider_id], model


def build(choices: Choices) -> dict:
    """Turn the choices into the files to write, without writing them.

    Separated so it can be tested, and so 'config --dry-run' can show exactly
    what would change.
    """
    orchestrator_provider, orchestrator_model = _split(choices.orchestrator)
    reader_provider, reader_model = _split(choices.reader)
    writer_provider, writer_model = _split(choices.writer)

    reader_key = f"{reader_provider.id}-reader"
    writer_key = f"{writer_provider.id}-writer"

    profiles = {reader_key: providers.worker_profile(reader_provider, reader_model)}
    # One profile when both roles land on the same model, rather than two
    # identical ones that would then have to be kept in step by hand.
    if (writer_provider.id, writer_model) == (reader_provider.id, reader_model):
        writer_key = reader_key
    else:
        profiles[writer_key] = providers.worker_profile(writer_provider, writer_model)

    # What actually needs exempting is narrower than it looks. The reader and
    # writer profiles are direct HTTP calls made by the tools; they never create
    # an OpenCode session, so the plugin never sees them and exempting them means
    # nothing. The one worker that does run as a session is the explorer
    # subagent, which uses the reader's model - so that is what goes in.
    #
    # Keyed on the model rather than the provider, because provider-level
    # exemption silently breaks when one vendor fills two roles: exempt it and
    # the orchestrator stops being shunted, with no telemetry to show it.
    exempt = {"ollama", "lmstudio", "llamacpp", "local", choices.reader}
    orchestrator_id = choices.orchestrator.split("/")[0]
    # Never exempt the boss, however the roles were assigned. This is the failure
    # that leaves the system inert and looking merely unused.
    exempt -= {choices.orchestrator, orchestrator_id}
    if reader_provider.remote and reader_provider.id == orchestrator_id:
        exempt.discard(reader_provider.id)

    shared = {
        "_roles": {
            "orchestrator": choices.orchestrator,
            "reader": choices.reader,
            "writer": choices.writer,
        },
        "_note": (
            "Shared policy, safe to commit. Machine-specific settings live in "
            "shunt.local.json, which is gitignored."
        ),
        "profile": reader_key,
        "writerProfile": writer_key,
        "bulkExempt": sorted(exempt),
        # Creating a file a worker invents and nobody reads stays at tests only.
        # Changing an existing one comes back as a diff, so it may reach source.
        "writePaths": choices.measured.test_globs,
        "editPaths": sorted(set(choices.measured.test_globs + choices.measured.source_globs)),
        "economics": {
            "assumedConversationTokens": choices.measured.conversation_tokens,
            "remainingTurns": choices.measured.remaining_turns,
        },
    }
    # The floor belongs to whoever is being protected, so it is priced from the
    # orchestrator's own model, not its vendor's cheapest. Gemini Pro against
    # Gemini Flash differ by four times, which moves the threshold from 13 KB to
    # 28 KB: with a cheap boss there is simply less worth saving.
    orchestrator_prices = providers.prices_for(orchestrator_provider, orchestrator_model)
    if orchestrator_prices["cache_write"]:
        shared["economics"]["cacheWritePerMillion"] = orchestrator_prices["cache_write"]
    if orchestrator_prices["cache_read"]:
        shared["economics"]["cacheReadPerMillion"] = orchestrator_prices["cache_read"]

    # What delegating costs on the cheap side. Left out of the arithmetic
    # originally, which made the worker look free and tilted every marginal
    # call towards delegating. Zero is written explicitly for a local model,
    # because there it is a measurement rather than a missing value.
    reader_prices = providers.prices_for(reader_provider, reader_model)
    shared["economics"]["workerInPerMillion"] = (
        0.0 if not reader_provider.remote else (reader_prices["price_in"] or 0.0)
    )
    shared["economics"]["workerOutPerMillion"] = (
        0.0 if not reader_provider.remote else (reader_prices["price_out"] or 0.0)
    )

    if choices.allowed:
        shared["allowedProviders"] = choices.allowed

    local = {
        "_note": "This machine only. Never commit: it names local endpoints and project ids.",
        "profile": reader_key,
        "profiles": profiles,
    }
    # Substitute the environment now, so the runtime never has to expand anything.
    for profile in local["profiles"].values():
        for key, value in list(profile.items()):
            if isinstance(value, str) and value.startswith("${"):
                variable = value[2:-1]
                resolved = os.environ.get(variable)
                if resolved:
                    profile[key] = resolved
                else:
                    profile.pop(key)

    provider_blocks = {}
    for provider, models in (
        (orchestrator_provider, [orchestrator_model]),
        (reader_provider, [reader_model]),
        (writer_provider, [writer_model]),
    ):
        block = providers.opencode_provider_block(provider, models)
        if not block:
            continue
        if provider.id in provider_blocks and "models" in block:
            provider_blocks[provider.id]["models"].update(block["models"])
        else:
            provider_blocks[provider.id] = block

    for block in provider_blocks.values():
        for key, value in list(block.get("options", {}).items()):
            if isinstance(value, str) and value.startswith("${"):
                block["options"][key] = os.environ.get(value[2:-1], "")

    return {
        "shared": shared,
        "local": local,
        "providers": provider_blocks,
        "orchestrator_model": choices.orchestrator,
        "reader_model": choices.reader,
        "reader_label": f"{reader_provider.label} ({reader_model})",
        "writer_label": f"{writer_provider.label} ({writer_model})",
        "reader_is_local": not reader_provider.remote,
    }


# Policy the user is explicitly invited to edit - the README tells people to
# widen these - as opposed to the rest, which is derived from the models chosen
# and must be rewritten when they change.
USER_POLICY = ("editPaths", "writePaths", "allowedProviders")


def merge_over(path: pathlib.Path, produced: dict) -> tuple[dict, list[str]]:
    """Lay the wizard's answers over whatever is already there.

    This used to write the generated file outright, so reconfiguring a model
    silently discarded every policy set by hand. It also contradicted a promise
    the tool makes elsewhere: `shunt update` treats `shunt.json` as user-owned
    and will not touch it even with `--force`. The file was safe from the
    command that sounds dangerous and rewritten by the one that sounds
    harmless.

    The split is between what the wizard deduces and what you decide. Prices,
    profiles and economics follow from the models you just picked, so they are
    rewritten - keeping a cache price from a model you no longer run is the
    failure this whole system is most prone to. Path allowlists are yours.
    """
    try:
        existing = json.loads(path.read_text())
    except Exception:
        return produced, []
    if not isinstance(existing, dict):
        return produced, []

    merged = {**existing, **produced}
    kept = sorted(set(existing) - set(produced))

    for key in USER_POLICY:
        if key in existing and existing[key] != produced.get(key):
            merged[key] = existing[key]
            if key in produced:
                kept.append(key)

    return merged, sorted(set(kept))


def apply(repo: pathlib.Path, plan: dict) -> list[str]:
    from .installer import merge_opencode_json

    destination = repo / ".opencode"
    destination.mkdir(parents=True, exist_ok=True)
    notes = []

    shared_path = destination / "shunt.json"
    shared, kept = merge_over(shared_path, plan["shared"])
    shared_path.write_text(json.dumps(shared, indent=2) + "\n")
    notes.append("wrote .opencode/shunt.json (shared policy, commit this)")
    if kept:
        notes.append(f"  kept your own settings: {', '.join(kept)}")

    local_path = destination / paths.LOCAL_CONFIG
    local, kept_local = merge_over(local_path, plan["local"])
    local_path.write_text(json.dumps(local, indent=2) + "\n")
    notes.append(f"wrote .opencode/{paths.LOCAL_CONFIG} (this machine, gitignored)")
    if kept_local:
        notes.append(f"  kept your own settings: {', '.join(kept_local)}")

    if note := merge_opencode_json(repo, plan["providers"]):
        notes.append(note)

    if note := _rewrite_agent(destination, plan):
        notes.append(note)
    if note := _rewrite_explorer(destination, plan):
        notes.append(note)
    return notes


def _rewrite_explorer(destination: pathlib.Path, plan: dict) -> str | None:
    """Point the explorer subagent at the reader's model.

    This one is not cosmetic. The explorer is the only worker that runs as a real
    OpenCode session, so it is the only one the read shunt can see, and it has to
    be a model the exempt list covers. Leave it on a model nobody configured and
    it either fails to authenticate or gets blocked from the reading that is its
    entire job.
    """
    agent = destination / "agents" / "explorer.md"
    if not agent.is_file():
        return None
    lines = agent.read_text().split("\n")
    for index, line in enumerate(lines[:15]):
        if line.startswith("model:"):
            if line.split(":", 1)[1].strip() == plan["reader_model"]:
                return None
            lines[index] = f"model: {plan['reader_model']}"
            agent.write_text("\n".join(lines))
            return f"pointed the explorer subagent at {plan['reader_model']}"
    return None


def _rewrite_agent(destination: pathlib.Path, plan: dict) -> str | None:
    """Point the orchestrator at the chosen model and stop it describing the wrong setup.

    The shipped prompt named a local Qwen on a GPU. Telling a model something
    untrue about its own environment degrades its behaviour, and most installs
    will not have that hardware, so the sentence is rewritten to match what was
    actually configured.
    """
    agent = destination / "agents" / "orchestrator.md"
    if not agent.is_file():
        return None

    text = agent.read_text()
    lines = text.split("\n")
    for index, line in enumerate(lines[:15]):
        if line.startswith("model:"):
            lines[index] = f"model: {plan['orchestrator_model']}"
            break
    text = "\n".join(lines)

    worker = plan["reader_label"]
    where = "on this machine" if plan["reader_is_local"] else "on a cheap cloud model"
    text = text.replace(
        "a local Qwen3-Coder model on the machine's GPU is not",
        f"{worker}, running {where}, is not",
    )
    text = text.replace(
        "Reasons, designs and reviews, and delegates bulk reading to local GPU models.",
        f"Reasons, designs and reviews, and delegates bulk reading to {worker}.",
    )
    agent.write_text(text)
    return f"pointed the orchestrator at {plan['orchestrator_model']}, worker {worker}"


def run(repo: pathlib.Path, assume_yes: bool = False, dry_run: bool = False) -> int:
    measured = Measured()
    measure_habits(measured)
    measure_repo(repo, measured)
    detect_available(measured)

    choices = ask(repo, measured, assume_yes=assume_yes)
    plan = build(choices)

    if dry_run:
        print("\n--- .opencode/shunt.json ---")
        print(json.dumps(plan["shared"], indent=2))
        print(f"\n--- .opencode/{paths.LOCAL_CONFIG} ---")
        print(json.dumps(plan["local"], indent=2))
        if plan["providers"]:
            print("\n--- opencode.json, provider entries ---")
            print(json.dumps(plan["providers"], indent=2))
        print("\nNothing written (--dry-run).")
        return 0

    print()
    for note in apply(repo, plan):
        print(note)

    print(
        f"\nOrchestrator: {plan['orchestrator_model']}"
        f"\nReader:       {plan['reader_label']}"
        f"\nWriter:       {plan['writer_label']}"
        f"\n\nRun 'shunt doctor' to confirm it works, then 'opencode --agent orchestrator'."
    )
    return 0
