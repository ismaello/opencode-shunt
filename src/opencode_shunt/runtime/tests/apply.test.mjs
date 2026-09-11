/**
 * Tests for the only function allowed to change a file in the repository.
 *
 * These run against a real temporary directory rather than a mock, because
 * every failure they cover is about what is actually on disk at the moment
 * something goes wrong. A mock would agree with whatever the code does.
 *
 * The previous order - write, then parse, then undo from a backup - could
 * leave code that does not compile in the repository whenever the backup had
 * quietly failed, and could revert an edit the user made while the worker was
 * still answering.
 *
 * Run: node --experimental-strip-types --no-warnings tests/apply.test.mjs
 */

import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { applyVerified } from "../lib/guards.ts"

let failed = 0
const ok = (name, condition, detail = "") => {
  if (!condition) failed++
  console.log(`${condition ? "ok  " : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`)
}

const repo = await fs.mkdtemp(path.join(os.tmpdir(), "shunt-apply-"))
const target = (name) => ({ absolute: path.join(repo, name), displayPath: name })

const GOOD = "def f():\n    return 1\n"
const BROKEN = "def f(:\n    return 1\n"

console.log("-- the ordinary case --")
{
  await fs.writeFile(path.join(repo, "a.py"), GOOD)
  const result = await applyVerified(repo, target("a.py"), GOOD, "def f():\n    return 2\n")
  ok("a valid edit is applied", result.ok)
  ok("and the new contents are on disk", (await fs.readFile(path.join(repo, "a.py"), "utf8")).includes("return 2"))
}

console.log("-- broken output never reaches the repository --")
{
  await fs.writeFile(path.join(repo, "b.py"), GOOD)
  const result = await applyVerified(repo, target("b.py"), GOOD, BROKEN)
  ok("a file that does not parse is refused", !result.ok && result.kind === "syntax")
  ok(
    "and the original is untouched, with no backup involved",
    (await fs.readFile(path.join(repo, "b.py"), "utf8")) === GOOD,
  )
}

console.log("-- a file that changed underneath --")
{
  await fs.writeFile(path.join(repo, "c.py"), GOOD)
  const staleBefore = "def f():\n    return 0\n"
  const result = await applyVerified(repo, target("c.py"), staleBefore, "def f():\n    return 3\n")
  ok("an edit computed from stale contents is refused", !result.ok && result.kind === "changed-underneath")
  ok("and the file keeps what is actually there", (await fs.readFile(path.join(repo, "c.py"), "utf8")) === GOOD)
}

console.log("-- creating a new file --")
{
  const result = await applyVerified(repo, target("nested/new.py"), null, GOOD)
  ok("a new file is created, parent directories included", result.ok)
  ok("with the right contents", (await fs.readFile(path.join(repo, "nested/new.py"), "utf8")) === GOOD)

  const second = await applyVerified(repo, target("nested/new.py"), null, GOOD)
  ok("creating over something that now exists is refused", !second.ok && second.kind === "changed-underneath")
}

{
  const result = await applyVerified(repo, target("never.py"), null, BROKEN)
  ok("a new file that does not parse is refused", !result.ok && result.kind === "syntax")
  const exists = await fs.access(path.join(repo, "never.py")).then(() => true, () => false)
  ok("and it is never created at all", !exists)
}

console.log("-- no debris left behind --")
{
  const entries = await fs.readdir(repo)
  ok(`no scratch files remain`, !entries.some((e) => e.startsWith(".shunt-")), entries.join(", "))
}

await fs.rm(repo, { recursive: true, force: true })

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed")
process.exit(failed ? 1 : 0)
