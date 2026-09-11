#!/usr/bin/env node
//
// `npx opencode-shunt ...` -> the Python CLI, with the Python part hidden.
//
// Why a wrapper rather than a Node rewrite: the product is the TypeScript in
// runtime/, and the Python only installs and measures it. Rewriting ~3k lines
// of installer, wizard, doctor and cost analysis in Node would buy nothing
// except a shorter install command.
//
// Why a wrapper rather than telling people to use pipx: OpenCode is a Node
// program, so everyone who can run OpenCode already has npx. Python is very
// likely present but not guaranteed, which is the one weakness of this
// approach and the reason every failure below prints a remedy rather than a
// stack trace.
//
// On first run this builds a private virtualenv under the user's cache
// directory and installs the bundled sdist into it. Later runs exec straight
// into it, so the cost is paid once.

"use strict";

const { spawnSync, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const VERSION = require("../package.json").version;
const MIN_PYTHON = [3, 10];

function cacheRoot() {
  if (process.platform === "win32") {
    return process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  }
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Caches");
  }
  return process.env.XDG_CACHE_HOME || path.join(os.homedir(), ".cache");
}

function die(message, remedy) {
  console.error(`opencode-shunt: ${message}`);
  if (remedy) console.error(`\n${remedy}`);
  process.exit(1);
}

// Newest first, so a machine with several Pythons uses the best one rather
// than whichever `python3` happens to point at.
function findPython() {
  const candidates = [
    "python3.13",
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
    "python",
  ];
  const tooOld = [];
  for (const candidate of candidates) {
    const probe = spawnSync(
      candidate,
      ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
      { encoding: "utf8" },
    );
    if (probe.status !== 0 || !probe.stdout) continue;
    const [major, minor] = probe.stdout.trim().split(".").map(Number);
    if (major > MIN_PYTHON[0] || (major === MIN_PYTHON[0] && minor >= MIN_PYTHON[1])) {
      return candidate;
    }
    tooOld.push(`${candidate} (${major}.${minor})`);
  }
  die(
    tooOld.length
      ? `found Python, but all of it is older than ${MIN_PYTHON.join(".")}: ${tooOld.join(", ")}`
      : "no Python interpreter found",
    `This installs a Python command-line tool. Install Python ${MIN_PYTHON.join(".")} or newer:\n` +
      "  macOS:   brew install python\n" +
      "  Debian:  sudo apt install python3 python3-venv\n" +
      "  Windows: https://python.org/downloads\n\n" +
      "Or skip this wrapper entirely and use the Python tooling directly:\n" +
      "  uv tool install opencode-shunt      # then: shunt init\n" +
      "  pipx install opencode-shunt",
  );
}

function binDir(venv) {
  return path.join(venv, process.platform === "win32" ? "Scripts" : "bin");
}

function install(venv, python) {
  const sdist = fs
    .readdirSync(path.join(__dirname, "..", "dist"))
    .find((name) => name.endsWith(".tar.gz") || name.endsWith(".whl"));
  if (!sdist) {
    die(
      "this package was published without the Python distribution inside it",
      "That is a packaging bug, not something you can fix. Please report it, and\n" +
        "meanwhile use: pipx install opencode-shunt",
    );
  }

  console.error(`opencode-shunt ${VERSION}: first run, preparing (a few seconds)...`);
  // Removed rather than reused: a half-built venv from an interrupted run is
  // worse than no venv, and silently unwrapping into one produces errors that
  // look like bugs in the tool.
  fs.rmSync(venv, { recursive: true, force: true });

  const venvResult = spawnSync(python, ["-m", "venv", venv], { stdio: "inherit" });
  if (venvResult.status !== 0) {
    die(
      "could not create a virtualenv",
      "On Debian and Ubuntu the venv module ships separately:\n" +
        "  sudo apt install python3-venv",
    );
  }

  const pip = path.join(binDir(venv), process.platform === "win32" ? "pip.exe" : "pip");
  const pipResult = spawnSync(
    pip,
    ["install", "--quiet", "--disable-pip-version-check", path.join(__dirname, "..", "dist", sdist)],
    { stdio: "inherit" },
  );
  if (pipResult.status !== 0) {
    fs.rmSync(venv, { recursive: true, force: true });
    die("could not install the Python package", "Try: pipx install opencode-shunt");
  }
}

function main() {
  // Keyed by version, so upgrading the npm package rebuilds rather than
  // running the previous release out of a stale cache.
  const venv = path.join(cacheRoot(), "opencode-shunt", `venv-${VERSION}`);
  const shunt = path.join(binDir(venv), process.platform === "win32" ? "shunt.exe" : "shunt");

  if (!fs.existsSync(shunt)) {
    install(venv, findPython());
  }

  // stdio inherited so the wizard's prompts and doctor's colours behave as if
  // it had been invoked directly, and the exit code passes through because
  // `shunt doctor` returning non-zero is how scripts gate on it.
  const result = spawnSync(shunt, process.argv.slice(2), { stdio: "inherit" });
  if (result.error) die(`could not run ${shunt}: ${result.error.message}`);
  process.exit(result.status ?? 1);
}

main();
