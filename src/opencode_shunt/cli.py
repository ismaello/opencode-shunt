"""Command line entry point.

Commands are shaped like git's: they run inside a repository and default to the
current directory. There is deliberately no registry of which repositories have
been installed into, which means nothing will tell you that another eight repos
are on an old version. That is the cost of not maintaining a registry, and it
seemed the better trade for a tool people install once per project.
"""

from __future__ import annotations

import argparse
import sys

from . import paths

EPILOG = """\
typical first run:

  shunt init                 put the runtime into ./.opencode
  shunt config               decide who orchestrates, reads and writes
  shunt doctor               confirm it actually works

afterwards:

  shunt stats                what each delegation saved, per operation
  shunt report               what it saved across real sessions, from opencode's own books
  shunt doctor               run this whenever something feels wrong

Every way this system breaks, it breaks quietly and in the expensive direction,
so 'doctor' is not a formality. A broken install looks exactly like an unused one.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shunt",
        description="Keep bulk work off your expensive model, in any repository.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"opencode-shunt {paths.version()}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text, description=help_text)
        child.add_argument("repo", nargs="?", default=".", help="repository (default: current directory)")
        return child

    initialise = add("init", "Install the runtime into a repository.")
    initialise.add_argument("--force", action="store_true", help="replace files edited here, keeping .bak copies")
    initialise.add_argument("--check", action="store_true", help="report what would change, write nothing")

    update = add("update", "Update an installed runtime to this package's version.")
    update.add_argument("--force", action="store_true", help="replace files edited here, keeping .bak copies")
    update.add_argument("--check", action="store_true", help="report what would change, write nothing")

    config = add("config", "Choose who orchestrates, who reads and who writes.")
    config.add_argument("--yes", action="store_true", help="accept the measured defaults, ask nothing")
    config.add_argument("--dry-run", action="store_true", help="print the configuration, write nothing")

    add("doctor", "Check the install, the credentials and the role assignment.")

    stats = add("stats", "Per-operation savings, from the shunt's own telemetry.")
    stats.add_argument("--since", help="ISO date, e.g. 2026-09-01")

    report = add("report", "Session-level savings, joined against opencode's accounting.")
    report.add_argument("--since", help="ISO date, e.g. 2026-09-01")
    report.add_argument("--detail", action="store_true", help="one line per session")

    bench = add("bench", "A/B the shunt on real questions. Slow and it costs money.")
    bench.add_argument("--repeat", type=int, default=1, help="runs per arm; medians are reported")
    bench.add_argument("--report", action="store_true", help="show previous results without running")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    repo = paths.resolve_repo(args.repo)

    if args.command in ("init", "update"):
        from .installer import install

        if args.command == "update" and not (repo / ".opencode").is_dir():
            print(f"nothing installed in {repo}. Run: shunt init")
            return 1
        code = install(repo, check=args.check, force=args.force)
        if code == 0 and args.command == "init" and not args.check:
            local = repo / ".opencode" / paths.LOCAL_CONFIG
            if not local.is_file():
                print("\nNo configuration yet. Run: shunt config")
        return code

    if args.command == "config":
        from .configure import run

        return run(repo, assume_yes=args.yes, dry_run=args.dry_run)

    if args.command == "doctor":
        from .doctor import run

        return run(repo)

    if args.command == "stats":
        from .stats import main as stats_main

        return stats_main(["--since", args.since] if args.since else [])

    if args.command == "report":
        from .report import main as report_main

        forwarded = []
        if args.since:
            forwarded += ["--since", args.since]
        if args.detail:
            forwarded.append("--detail")
        return report_main(forwarded)

    if args.command == "bench":
        from .bench import main as bench_main

        forwarded = [str(repo)]
        if args.repeat != 1:
            forwarded += ["--repeat", str(args.repeat)]
        if args.report:
            forwarded.append("--report")
        return bench_main(forwarded)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
