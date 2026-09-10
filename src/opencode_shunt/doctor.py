"""Turn silent failures into loud ones.

This is the most useful command in the package, for a reason worth writing down.
Every way this system breaks, it breaks quietly and in the expensive direction:

  - A malformed shunt.json means the plugin never loads. Verified: OpenCode
    starts, answers normally, and says nothing.
  - A profile name that does not exist means bulk_read fails. Verified: the
    orchestrator quietly reads the files itself, gives a perfectly good answer,
    and bills full price.
  - An orchestrator whose provider sits in the exempt list means nothing is ever
    shunted. And because the exemption check returns before any telemetry is
    written, that setup produces no records at all - indistinguishable from a
    system nobody used.

So the failure signature of a broken install is an empty report, which is also
the signature of a working install that has had no traffic. Nothing in the
system can tell you which you have. That is what this command is for.

It only reads: it never changes a repository.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass

from . import paths
from .providers import CATALOGUE

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"

MARKS = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL ", INFO: "      "}


@dataclass
class Finding:
    level: str
    title: str
    detail: str = ""

    def render(self) -> str:
        head = f"[{MARKS[self.level]}] {self.title}"
        if not self.detail:
            return head
        body = "\n".join(f"           {line}" for line in self.detail.split("\n"))
        return f"{head}\n{body}"


class Report:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def add(self, level: str, title: str, detail: str = "") -> None:
        self.findings.append(Finding(level, title, detail))

    ok = lambda self, t, d="": self.add(OK, t, d)  # noqa: E731
    warn = lambda self, t, d="": self.add(WARN, t, d)  # noqa: E731
    fail = lambda self, t, d="": self.add(FAIL, t, d)  # noqa: E731
    info = lambda self, t, d="": self.add(INFO, t, d)  # noqa: E731

    @property
    def failures(self) -> int:
        return sum(1 for f in self.findings if f.level == FAIL)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.level == WARN)


def _load_json(path: pathlib.Path) -> tuple[dict | None, str | None]:
    """Parse a config file, returning the error rather than raising it.

    Malformed JSON is the failure that stops the plugin loading, so the checker
    has to survive it in order to report it.
    """
    if not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text()), None
    except json.JSONDecodeError as error:
        return None, f"line {error.lineno}: {error.msg}"


def check_opencode(report: Report) -> None:
    binary = shutil.which("opencode")
    if not binary:
        report.fail(
            "opencode is not on PATH",
            "Nothing here works without it. Install it first: https://opencode.ai",
        )
        return
    try:
        version = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        report.ok(f"opencode {version or 'installed'}", binary)
    except (subprocess.SubprocessError, OSError) as error:
        report.warn("opencode is installed but did not respond", str(error)[:200])


def check_install(repo: pathlib.Path, report: Report) -> dict | None:
    """Is the runtime present, and does it match what this version ships?"""
    destination = repo / ".opencode"
    if not destination.is_dir():
        report.fail(
            "no .opencode directory in this repository",
            f"Run: shunt init {repo}",
        )
        return None

    manifest, error = _load_json(destination / paths.MANIFEST_NAME)
    if error:
        report.warn(f"{paths.MANIFEST_NAME} is unreadable", error)
    elif manifest is None:
        report.warn(
            "installed by hand, with no manifest",
            "Nothing records which version is here, so an old file cannot be told from an\n"
            "edited one. Run 'shunt update' once to establish a record.",
        )
    else:
        installed = manifest.get("version", "unknown")
        shipping = paths.version()
        if installed == shipping:
            report.ok(f"runtime {installed} installed", str(destination))
        else:
            report.warn(
                f"runtime is {installed}, this package ships {shipping}",
                "Run: shunt update",
            )

    missing = [
        name
        for name in ("plugins/shunt.ts", "tools/bulk-read.ts", "lib/worker.ts", "shunt.json")
        if not (destination / name).is_file()
    ]
    if missing:
        report.fail(
            "core runtime files are missing",
            "\n".join(missing) + "\nRun: shunt update --force",
        )

    # OpenCode installs plugin dependencies itself on first load. When that has
    # not happened the plugin cannot import its SDK and silently does not load.
    if not (destination / "node_modules" / "@opencode-ai" / "plugin").is_dir():
        report.warn(
            "plugin dependencies are not installed yet",
            "OpenCode fetches these the first time it loads the plugin. If they are still\n"
            "absent after running opencode once in this repository, the plugin is not loading.",
        )

    return manifest


def check_config(repo: pathlib.Path, report: Report) -> tuple[dict, dict]:
    """The shared policy and the machine's overrides, merged as the runtime does."""
    destination = repo / ".opencode"
    shared, shared_error = _load_json(destination / "shunt.json")
    local, local_error = _load_json(destination / "shunt.local.json")

    for name, error in (("shunt.json", shared_error), ("shunt.local.json", local_error)):
        if error:
            report.fail(
                f"{name} is not valid JSON, so the plugin will not load",
                f"{error}\n"
                "This is the quietest failure in the system: OpenCode starts, answers\n"
                "normally, and nothing is shunted or recorded.",
            )

    if shared is None and not shared_error:
        report.warn("no shunt.json", "The runtime falls back to built-in defaults.")

    merged = {**(shared or {}), **(local or {})}
    # Profiles merge one level deep, matching loadConfig in runtime/lib/worker.ts.
    profiles = {**(shared or {}).get("profiles", {}), **(local or {}).get("profiles", {})}
    merged["profiles"] = profiles

    # A fresh install has policy but no profiles, which is a state to explain
    # rather than a list of faults to report. Diagnosing it as four separate
    # failures would bury the one thing the user needs to do.
    if local is None and not local_error and not profiles:
        report.fail(
            "not configured yet",
            "The runtime is installed but nobody has said which model orchestrates, which\n"
            "reads and which writes.\nRun: shunt config",
        )
        return merged, {}

    if not profiles:
        report.fail("no worker profiles defined", "There is nothing to delegate to. Run: shunt config")
        return merged, profiles

    active = merged.get("profile")
    if active not in profiles:
        report.fail(
            f'active reader profile "{active}" does not exist',
            f"Available: {', '.join(sorted(profiles)) or 'none'}\n"
            "Verified consequence: bulk_read raises, the orchestrator quietly reads the\n"
            "files itself, the answer looks perfect and you pay full price.",
        )
    else:
        report.ok(f'reader profile "{active}"', _describe(profiles[active]))

    writer = merged.get("writerProfile")
    if writer and writer not in profiles:
        report.fail(f'writer profile "{writer}" does not exist', "delegate_write and delegate_edit will fail.")
    elif writer and writer != active:
        report.ok(f'writer profile "{writer}"', _describe(profiles[writer]))
    elif not writer:
        report.info("no separate writer profile", "Generation falls back to the reader profile.")

    return merged, profiles


def _describe(profile: dict) -> str:
    bits = [profile.get("model", "?"), f"kind={profile.get('kind', '?')}"]
    if profile.get("baseURL"):
        bits.append(profile["baseURL"])
    if profile.get("contextTokens"):
        bits.append(f"{profile['contextTokens'] // 1000}k context")
    return "  ".join(bits)


def check_credentials(merged: dict, profiles: dict, report: Report) -> None:
    """Check what is actually in use, and only that.

    Checking every defined profile turns one real problem into a wall of
    failures about models nobody selected, which trains people to ignore the
    output. An unused profile with a missing key is not a fault.
    """
    in_use = {
        merged.get("profile"),
        merged.get("writerProfile"),
        merged.get("overflowProfile"),
    } & set(profiles)
    idle = sorted(set(profiles) - in_use)
    if idle:
        report.info(f"{len(idle)} profile(s) defined but not selected: {', '.join(idle)}")

    for name, profile in sorted((name, profiles[name]) for name in in_use):
        kind = profile.get("kind")
        if kind == "vertex":
            project = os.environ.get("GOOGLE_CLOUD_PROJECT") or profile.get("project", "")
            if not project or str(project).startswith(("CHANGE", "${")):
                report.fail(
                    f'profile "{name}" has no Google Cloud project',
                    "Set GOOGLE_CLOUD_PROJECT, or put a project id in the profile.",
                )
            elif not _vertex_token():
                report.fail(
                    f'profile "{name}" cannot get a Vertex token',
                    "Run: gcloud auth application-default login",
                )
            else:
                report.ok(f'profile "{name}" is authenticated', f"project {project}")
        elif variable := profile.get("apiKeyEnv"):
            if os.environ.get(variable):
                report.ok(f'profile "{name}" has {variable} set')
            else:
                report.fail(
                    f'profile "{name}" needs {variable}, which is not set',
                    _where_from(variable),
                )
        elif kind == "ollama":
            url = profile.get("baseURL", "http://127.0.0.1:11434")
            if _ollama_up(url):
                report.ok(f'profile "{name}" reached Ollama', url)
            else:
                report.fail(
                    f'profile "{name}" cannot reach Ollama at {url}',
                    "Start it with: ollama serve",
                )


def _where_from(variable: str) -> str:
    for provider in CATALOGUE.values():
        if variable in provider.requires:
            return f"Get one from {provider.requires[variable]}"
    return ""


def _vertex_token() -> bool:
    for candidate in (
        os.environ.get("GCLOUD_PATH"),
        "gcloud",
        f"{pathlib.Path.home()}/google-cloud-sdk/bin/gcloud",
    ):
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "auth", "application-default", "print-access-token"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except (subprocess.SubprocessError, OSError):
            continue
    return False


def _ollama_up(url: str) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def check_roles(repo: pathlib.Path, merged: dict, report: Report) -> None:
    """The check that catches a system quietly doing nothing.

    The shunt exempts cheap sessions from read blocking, keyed on provider name.
    Exempt the orchestrator's provider and the whole thing switches off, without
    a word, because the exemption test returns before telemetry is written. That
    is fine as long as the boss and the workers come from different vendors, and
    it is wrong the moment one vendor serves both roles.
    """
    orchestrator = _orchestrator_model(repo)
    if not orchestrator:
        report.warn("cannot tell which model orchestrates", "No model in agents/orchestrator.md.")
        return

    provider = orchestrator.split("/")[0]
    exempt = set(merged.get("bulkExempt") or [])
    exempt |= set(
        filter(None, (os.environ.get("SHUNT_BULK_EXEMPT") or "ollama,lmstudio,llamacpp,local").split(","))
    )

    if provider in exempt or orchestrator in exempt:
        report.fail(
            f"the orchestrator ({orchestrator}) is exempt from the shunt",
            "Nothing will ever be blocked or delegated, and nothing will be recorded, so\n"
            "this looks exactly like an unused install. Remove it from bulkExempt.",
        )
    else:
        report.ok(f"orchestrator {orchestrator} is subject to the shunt")

    workers = {
        profile.get("model", "")
        for key, profile in merged.get("profiles", {}).items()
        if key in (merged.get("profile"), merged.get("writerProfile"))
    }
    for model in filter(None, workers):
        # A worker that gets blocked from reading cannot do its job, and the
        # design depends on this exemption more than on any other rule.
        if not any(entry and (entry in model or model.endswith(entry)) for entry in exempt) and not _covered(
            model, exempt
        ):
            report.warn(
                f'worker model "{model}" may not be exempt from read blocking',
                "Workers must be free to read. Check bulkExempt in shunt.json covers the\n"
                "provider serving this model, or its subagent will be blocked from its own job.",
            )


def _covered(model: str, exempt: set[str]) -> bool:
    return any(entry.split("/")[-1] in model or model in entry for entry in exempt if entry)


def _orchestrator_model(repo: pathlib.Path) -> str | None:
    agent = repo / ".opencode" / "agents" / "orchestrator.md"
    if not agent.is_file():
        return None
    for line in agent.read_text().split("\n")[:15]:
        if line.startswith("model:"):
            return line.split(":", 1)[1].strip()
    return None


def check_policy(merged: dict, report: Report) -> None:
    """allowedProviders constrains workers only, which is narrower than it sounds."""
    allowed = merged.get("allowedProviders")
    if not allowed:
        return

    report.info(f"cloud policy: {', '.join(allowed)}")
    remote_only = [p.id for p in CATALOGUE.values() if not p.remote]
    if all(entry not in remote_only and entry != "ollama" for entry in allowed):
        return

    if set(allowed) <= {"ollama", "local", "lmstudio", "llamacpp"}:
        report.warn(
            "this repository is marked as not allowed to leave the machine, but that "
            "only binds the workers",
            "allowedProviders is enforced in resolveProfile, which the reader and writer\n"
            "go through. The orchestrator's model comes from OpenCode's own config and is\n"
            "not checked, so a cloud orchestrator still sees the code. Choose a local\n"
            "orchestrator too if the restriction has to be real.",
        )


def check_activity(repo: pathlib.Path, report: Report) -> None:
    """Has it actually done anything? An empty report is the ambiguous case."""
    telemetry = paths.telemetry_file()
    if not telemetry.is_file():
        report.info(
            "no telemetry yet",
            "Nothing has run. Note that a broken install and an unused one look the same\n"
            "here, which is why the checks above matter more than this one.",
        )
        return

    rows = []
    for line in telemetry.read_text(errors="replace").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    delegations = [r for r in rows if r.get("tool") in ("bulk_read", "delegate_write", "delegate_edit")]
    blocks = [r for r in rows if r.get("verdict") in ("block", "observe-would-block")]
    if not delegations and not blocks:
        report.info("telemetry exists but records no delegations or blocks")
        return

    saved_in = sum(
        r.get("content_chars", 0) - r.get("returned_chars", 0)
        for r in rows
        if r.get("tool") == "bulk_read" and r.get("content_chars")
    )
    saved_in += sum(
        r.get("raw_bytes", 0) - r.get("summary_bytes", 0)
        for r in rows
        if r.get("event") == "output-shunt" and r.get("raw_bytes")
    )
    saved_out = sum(
        r.get("written_chars", 0) - r.get("returned_chars", 0)
        for r in rows
        if r.get("tool") in ("delegate_write", "delegate_edit") and r.get("written_chars")
    )
    report.ok(
        f"{len(delegations)} delegations, {len(blocks)} expensive reads intercepted",
        f"roughly {saved_in / 3.5 / 1000:,.0f}k input and {saved_out / 3.5 / 1000:,.0f}k output "
        f"tokens kept out of the orchestrator\nSee 'shunt stats' for the detail.",
    )

    failures = [r for r in rows if str(r.get("event", "")).endswith(("failed", "error"))]
    if failures:
        report.warn(
            f"{len(failures)} worker call(s) failed",
            "Latest: " + str(failures[-1].get("error", ""))[:160],
        )


def check_database(report: Report) -> None:
    """OpenCode's own accounting, which is where real savings get measured."""
    database = paths.opencode_db()
    if not database.is_file():
        report.info("no opencode database found", "'shunt report' needs it to measure real sessions.")
        return
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            sessions = connection.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        report.ok(f"opencode database readable, {sessions} sessions", "'shunt report' can measure real usage.")
    except sqlite3.Error as error:
        report.warn("opencode database is not readable", str(error)[:160])


def run(repo: pathlib.Path) -> int:
    report = Report()
    print(f"repository: {repo}\npackage:    opencode-shunt {paths.version()}\n")

    check_opencode(report)
    manifest = check_install(repo, report)
    if manifest is not None or (repo / ".opencode").is_dir():
        merged, profiles = check_config(repo, report)
        if profiles:
            check_credentials(merged, profiles, report)
            check_roles(repo, merged, report)
            check_policy(merged, report)
    check_activity(repo, report)
    check_database(report)

    for finding in report.findings:
        print(finding.render())

    print()
    if report.failures:
        print(f"{report.failures} problem(s) will stop this working. Fix those first.")
        return 1
    if report.warnings:
        print(f"{report.warnings} warning(s): it will run, but not as intended.")
        return 0
    print("Everything checks out.")
    return 0
