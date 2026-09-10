"""Tests for installing, updating and merging.

The four-state classification is the whole reason the manifest exists, and each
state has a different consequence: guess "outdated" when a file was edited and
somebody's work is destroyed; guess "modified" when it is merely old and that
repository never receives a fix. So each is pinned here.

The opencode.json merge is tested because the previous installer refused to do
it and told the user to merge by hand, which is where a half-finished install
comes from.
"""

from __future__ import annotations

import json

from opencode_shunt import installer, paths


def test_the_packaged_runtime_is_present():
    """A wheel built without the TypeScript would install nothing and say it worked."""
    files = installer.shipped_files()
    for required in ("plugins/shunt.ts", "tools/bulk-read.ts", "lib/worker.ts", "lib/economics.ts", "shunt.json"):
        assert required in files, f"{required} is not in the packaged runtime"


def test_machine_specific_files_are_never_shipped():
    files = installer.shipped_files()
    assert paths.LOCAL_CONFIG not in files
    assert not any("node_modules" in name for name in files)


def test_no_shipped_file_names_a_real_project_or_endpoint():
    """A package must not carry its author's cloud project or GPU address."""
    for name, path in installer.shipped_files().items():
        if path.suffix not in (".json", ".md", ".ts"):
            continue
        text = path.read_text(errors="replace")
        assert "production-400914" not in text, f"{name} contains a real project id"


def test_classification(tmp_path):
    source = tmp_path / "shipped.ts"
    source.write_text("new content")
    target = tmp_path / "installed.ts"

    assert installer.classify(source, target, None) == "new"

    target.write_text("new content")
    assert installer.classify(source, target, installer.sha256(source)) == "current"

    target.write_text("old content")
    old_hash = installer.sha256(target)
    # Matches what we recorded installing, so it is old and safe to replace.
    assert installer.classify(source, target, old_hash) == "outdated"
    # Matches neither: somebody edited it here.
    assert installer.classify(source, target, "0" * 64) == "modified"
    # No record at all, so old and edited cannot be told apart.
    assert installer.classify(source, target, None) == "unknown"


def test_install_then_check_reports_up_to_date(tmp_path, capsys):
    assert installer.install(tmp_path, quiet=True) == 0
    capsys.readouterr()
    assert installer.install(tmp_path, check=True) == 0
    assert "up to date" in capsys.readouterr().out


def test_check_reports_a_locally_edited_file(tmp_path, capsys):
    installer.install(tmp_path, quiet=True)
    (tmp_path / ".opencode" / "shunt.json").write_text('{"edited": true}')
    capsys.readouterr()
    assert installer.install(tmp_path, check=True) == 1
    assert "edited here" in capsys.readouterr().out


def test_an_edited_file_survives_an_update(tmp_path):
    installer.install(tmp_path, quiet=True)
    target = tmp_path / ".opencode" / "shunt.json"
    target.write_text('{"mine": true}')
    installer.install(tmp_path, quiet=True)
    assert json.loads(target.read_text()) == {"mine": True}


def test_force_replaces_it_but_keeps_a_copy(tmp_path):
    installer.install(tmp_path, quiet=True)
    target = tmp_path / ".opencode" / "shunt.json"
    target.write_text('{"mine": true}')
    installer.install(tmp_path, force=True, quiet=True)
    assert json.loads(target.read_text()) != {"mine": True}
    assert json.loads(target.with_suffix(".json.bak").read_text()) == {"mine": True}


def test_the_manifest_records_the_version(tmp_path):
    installer.install(tmp_path, quiet=True)
    manifest = json.loads((tmp_path / ".opencode" / paths.MANIFEST_NAME).read_text())
    assert manifest["version"] == paths.version()
    assert manifest["files"]


def test_gitignore_keeps_the_local_config_out_of_version_control(tmp_path):
    installer.install(tmp_path, quiet=True)
    ignored = (tmp_path / ".opencode" / ".gitignore").read_text()
    assert paths.LOCAL_CONFIG in ignored


def test_merge_creates_opencode_json_when_absent(tmp_path):
    installer.merge_opencode_json(tmp_path, {"ollama": {"npm": "x"}})
    config = json.loads((tmp_path / "opencode.json").read_text())
    assert config["provider"]["ollama"] == {"npm": "x"}
    assert config["$schema"]


def test_merge_preserves_everything_already_there(tmp_path):
    target = tmp_path / "opencode.json"
    target.write_text(json.dumps({"model": "mine", "provider": {"custom": {"npm": "keep-me"}}}))
    installer.merge_opencode_json(tmp_path, {"ollama": {"npm": "added"}})
    config = json.loads(target.read_text())
    assert config["model"] == "mine"
    assert config["provider"]["custom"]["npm"] == "keep-me"
    assert config["provider"]["ollama"]["npm"] == "added"


def test_merge_never_overwrites_a_provider_somebody_configured(tmp_path):
    target = tmp_path / "opencode.json"
    target.write_text(json.dumps({"provider": {"ollama": {"npm": "theirs"}}}))
    installer.merge_opencode_json(tmp_path, {"ollama": {"npm": "ours"}})
    assert json.loads(target.read_text())["provider"]["ollama"]["npm"] == "theirs"


def test_merge_refuses_to_touch_malformed_json(tmp_path):
    target = tmp_path / "opencode.json"
    target.write_text("{ not json")
    note = installer.merge_opencode_json(tmp_path, {"ollama": {"npm": "x"}})
    assert "not valid JSON" in note
    assert target.read_text() == "{ not json"
