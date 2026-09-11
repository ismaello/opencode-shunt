"""Tests for the health check, focused on its credibility.

`doctor` is the only defence against this system's characteristic failure, which
is working badly rather than not working. That gives it an unusual requirement:
it has to be believed. A check that cries wolf gets ignored, and once it is
ignored the silent failures it exists to catch go back to being silent.

Which is what happened. Worker failures were counted over the whole life of the
install, so a misconfiguration fixed hours earlier still produced a warning, and
the warning was noise.
"""

from __future__ import annotations

import json

import pytest

from opencode_shunt.doctor import (
    FAIL,
    WARN,
    Report,
    check_activity,
    check_config,
    check_credentials,
    check_roles,
)


@pytest.fixture
def telemetry(tmp_path, monkeypatch):
    """Point the telemetry file somewhere disposable, owned by this repository.

    Every real telemetry line carries a sessionID - checked against 263 of them
    - and doctor now filters on it, so a fixture that omits one would be testing
    a shape that never occurs.
    """
    log = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr("opencode_shunt.paths.telemetry_file", lambda: log)

    def write(rows: list[dict], owned: bool = True) -> None:
        log.write_text("\n".join(json.dumps(row) for row in rows))
        sessions = {row.get("sessionID") for row in rows} if owned else set()
        monkeypatch.setattr("opencode_shunt.paths.sessions_in", lambda repo: sessions)

    return write


def delegation(ts: str) -> dict:
    return {
        "ts": ts,
        "tool": "bulk_read",
        "sessionID": "ses_here",
        "content_chars": 10_000,
        "returned_chars": 1_000,
    }


def failure(ts: str) -> dict:
    return {
        "ts": ts,
        "event": "worker-failed",
        "sessionID": "ses_here",
        "error": "ollama 404: model not found",
    }


def run(tmp_path) -> Report:
    report = Report()
    check_activity(tmp_path, report)
    return report


def test_a_failure_that_has_not_recurred_is_history_not_a_warning(telemetry, tmp_path):
    telemetry([delegation("2026-09-10T10:00:00Z"), failure("2026-09-10T11:00:00Z"),
               delegation("2026-09-10T12:00:00Z")])
    report = run(tmp_path)
    assert report.warnings == 0
    assert any("history rather than a fault" in f.detail for f in report.findings)


def test_a_failure_with_nothing_working_since_is_a_warning(telemetry, tmp_path):
    telemetry([delegation("2026-09-10T10:00:00Z"), failure("2026-09-10T12:00:00Z")])
    report = run(tmp_path)
    assert report.warnings == 1
    warning = next(f for f in report.findings if f.level == WARN)
    assert "404" in warning.detail
    # Say what it costs, or the reader has no reason to act on it.
    assert "silence" in warning.detail


def test_the_warning_says_when_so_it_can_be_judged(telemetry, tmp_path):
    telemetry([delegation("2026-09-10T10:00:00Z"), failure("2026-09-10T12:34:00Z")])
    assert any("2026-09-10 12:34" in f.title for f in run(tmp_path).findings)


def test_no_telemetry_is_reported_as_ambiguous_rather_than_healthy(telemetry, tmp_path, monkeypatch):
    monkeypatch.setattr("opencode_shunt.paths.telemetry_file", lambda: tmp_path / "absent.jsonl")
    report = run(tmp_path)
    # A broken install and an unused one look identical here, and saying so is
    # the only honest answer.
    assert any("look the same" in f.detail for f in report.findings)


def test_a_corrupt_line_does_not_stop_the_check(telemetry, tmp_path):
    telemetry([delegation("2026-09-10T10:00:00Z")])
    log = tmp_path / "telemetry.jsonl"
    log.write_text(log.read_text() + "\n{truncated\n")
    assert run(tmp_path).failures == 0


def test_a_hand_edited_orchestrator_that_disagrees_with_the_config_fails(tmp_path):
    """Found by making the mistake. Changing _roles without changing the agent
    file left the old model running and the economics priced for the new one,
    and doctor said everything checked out."""
    agents = tmp_path / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "orchestrator.md").write_text("---\nmodel: anthropic/claude-opus-4-8\n---\n")

    report = Report()
    check_roles(
        tmp_path,
        {"_roles": {"orchestrator": "openai/gpt-6-astra"}, "bulkExempt": []},
        report,
    )
    assert report.failures == 1
    assert any("is not the one that runs" in f.title for f in report.findings)


def test_agreement_between_the_two_is_not_reported_as_a_problem(tmp_path):
    agents = tmp_path / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "orchestrator.md").write_text("---\nmodel: openai/gpt-6-astra\n---\n")

    report = Report()
    check_roles(
        tmp_path,
        {"_roles": {"orchestrator": "openai/gpt-6-astra"}, "bulkExempt": []},
        report,
    )
    assert report.failures == 0


def test_a_fresh_repository_is_not_credited_with_other_repositories_work(telemetry, tmp_path):
    """Found by installing the wheel clean: doctor announced 77 delegations and
    248k tokens saved in a repository where nothing had ever run, because
    telemetry is one file per machine and nothing filtered it."""
    telemetry([delegation("2026-09-10T10:00:00Z")], owned=False)
    report = run(tmp_path)
    assert any("nothing has run in this repository yet" in f.title for f in report.findings)
    assert not any("delegations" in f.title for f in report.findings)


def test_reads_the_shunt_could_not_judge_are_surfaced(telemetry, tmp_path):
    """Measured on a real Gemini Pro session: one read, no session in the map,
    nothing logged, and the resulting empty report looked exactly like a broken
    install. The read is still allowed; it is no longer invisible."""
    telemetry(
        [{"ts": "2026-09-11T10:00:00Z", "tool": "read", "sessionID": "s", "verdict": "session-unknown"}] * 3
        + [{"ts": "2026-09-11T10:01:00Z", "tool": "read", "sessionID": "s", "verdict": "allow-small"}]
    )
    assert any("bypassed the shunt unjudged" in f.title for f in run(tmp_path).findings)


def test_the_occasional_startup_race_is_not_worth_a_warning(telemetry, tmp_path):
    telemetry(
        [{"ts": "2026-09-11T10:00:00Z", "tool": "read", "sessionID": "s", "verdict": "session-unknown"}]
        + [
            {"ts": "2026-09-11T10:01:00Z", "tool": "read", "sessionID": "s", "verdict": "allow-small"}
        ]
        * 20
    )
    assert not any("bypassed" in f.title for f in run(tmp_path).findings)


def test_work_done_here_is_still_reported(telemetry, tmp_path):
    telemetry([delegation("2026-09-10T10:00:00Z")])
    assert any("1 delegations" in f.title for f in run(tmp_path).findings)


# --- reading the config the way the runtime reads it -------------------------
#
# These come from installing over a real pre-package install. The runtime merges
# into each profile so that shunt.json can hold the model and shunt.local.json
# only the project; doctor merged at the level above and replaced the profile
# wholesale, which is a different configuration from the one actually running.


@pytest.fixture
def config(tmp_path):
    """Write the two config files and read them back through doctor."""
    destination = tmp_path / ".opencode"
    destination.mkdir()

    def write(shared: dict | None = None, local: dict | None = None) -> tuple[Report, dict, dict]:
        if shared is not None:
            (destination / "shunt.json").write_text(json.dumps(shared))
        if local is not None:
            (destination / "shunt.local.json").write_text(json.dumps(local))
        report = Report()
        merged, profiles = check_config(tmp_path, report)
        return report, merged, profiles

    return write


VERTEX = {"kind": "vertex", "model": "gemini-2.5-flash", "contextTokens": 1_000_000}


def test_a_profile_split_across_both_files_is_read_as_the_runtime_reads_it(config):
    """shunt.json carries the model, shunt.local.json adds only the project."""
    _, _, profiles = config(
        shared={"profiles": {"flash": VERTEX}},
        local={"profile": "flash", "profiles": {"flash": {"project": "some-project"}}},
    )
    assert profiles["flash"] == {**VERTEX, "project": "some-project"}


def test_the_local_file_wins_on_a_key_both_set(config):
    _, _, profiles = config(
        shared={"profiles": {"flash": {**VERTEX, "project": "shared"}}},
        local={"profile": "flash", "profiles": {"flash": {"project": "machine"}}},
    )
    assert profiles["flash"]["project"] == "machine"


def test_a_split_profile_still_gets_its_credentials_checked(config, monkeypatch):
    """The real cost of the shallow merge: kind went missing, so nothing was checked.

    Every branch of the credential check is selected by kind, so a profile
    without one matched nothing and was passed over without a word.
    """
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    _, merged, profiles = config(
        shared={"profiles": {"flash": VERTEX}},
        local={"profile": "flash", "profiles": {"flash": {}}},
    )
    report = Report()
    check_credentials(merged, profiles, report)
    # Specifically the Vertex branch, since reaching it is the proof that kind
    # survived the merge. "Something was reported" would pass either way now
    # that an unknown kind is a failure of its own.
    assert any("Google Cloud project" in f.title for f in report.findings), [
        f.title for f in report.findings
    ]


def test_an_unrecognised_kind_fails_loudly_rather_than_being_skipped(config):
    _, merged, profiles = config(
        shared={"profiles": {"odd": {"model": "x"}}},
        local={"profile": "odd", "profiles": {}},
    )
    report = Report()
    check_credentials(merged, profiles, report)
    assert report.failures == 1
    assert "nothing about it was checked" in next(f for f in report.findings if f.level == FAIL).title


def test_a_config_from_before_the_split_is_flagged(config):
    """An update leaves the user's config alone, correctly, and it goes stale."""
    report, _, _ = config(
        shared={"profiles": {"flash": VERTEX}},
        local={"profile": "flash", "profiles": {"flash": {"project": "p"}}},
    )
    warnings = [f for f in report.findings if f.level == WARN]
    assert any("predates the config split" in f.title for f in warnings)
    assert any("shunt config" in f.detail for f in warnings)


def test_a_current_config_is_not_nagged(config):
    report, _, _ = config(
        shared={"profile": "flash", "writerProfile": "flash"},
        local={"profile": "flash", "profiles": {"flash": VERTEX}},
    )
    assert not any("predates" in f.title for f in report.findings)


def test_an_unconfigured_install_gets_one_instruction_not_a_wall_of_faults(config):
    report, _, _ = config(shared={"writePaths": ["tests/**"]})
    assert report.failures == 1
    assert "not configured yet" in report.findings[-1].title


def test_malformed_json_is_the_loudest_failure(tmp_path):
    """OpenCode starts, answers normally, and nothing is shunted or recorded."""
    destination = tmp_path / ".opencode"
    destination.mkdir()
    (destination / "shunt.json").write_text('{"profile": "flash",')
    report = Report()
    check_config(tmp_path, report)
    failure = next(f for f in report.findings if f.level == FAIL)
    assert "not valid JSON" in failure.title
    assert "quietest failure" in failure.detail
