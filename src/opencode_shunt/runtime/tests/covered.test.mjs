/**
 * Tests for the record of what a worker already summarised.
 *
 * The failure this guards is quiet and expensive: the orchestrator delegates a
 * read, gets a summary, and then reads the files in full anyway, paying for
 * both. Nothing errors, nothing looks wrong, the bill is simply higher than not
 * delegating at all.
 *
 * Run: node --experimental-strip-types --no-warnings tests/covered.test.mjs
 */

import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { markCovered, prune, wasCovered } from "../lib/covered.ts"

let failed = 0
const ok = (name, condition, detail = "") => {
  if (!condition) failed++
  console.log(`${condition ? "ok  " : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`)
}

const worktree = await fs.mkdtemp(path.join(os.tmpdir(), "covered-"))
const session = `test-${Date.now()}`

ok("nothing is covered before a delegation", !(await wasCovered(session, worktree, "app/a.py")))

await markCovered(session, worktree, ["app/a.py", "app/b.py"])
ok("a summarised file is remembered", await wasCovered(session, worktree, "app/a.py"))
ok("so is the second one", await wasCovered(session, worktree, "app/b.py"))
ok("an untouched file is not", !(await wasCovered(session, worktree, "app/c.py")))

// The tool records what it resolved; the hook asks about whatever the model
// typed. They have to agree, or the check silently never fires.
ok("./ prefix resolves to the same file", await wasCovered(session, worktree, "./app/a.py"))
ok("an absolute path does too", await wasCovered(session, worktree, path.join(worktree, "app/a.py")))

await markCovered(session, worktree, ["app/c.py"])
ok("a later call adds rather than replaces", await wasCovered(session, worktree, "app/a.py"))
ok("and records the new file", await wasCovered(session, worktree, "app/c.py"))

const other = `other-${Date.now()}`
ok("sessions do not leak into each other", !(await wasCovered(other, worktree, "app/a.py")))

// Failing open matters more than bookkeeping: a missing record must allow the
// read, never block it.
ok("no session id means no record", !(await wasCovered("", worktree, "app/a.py")))
await markCovered("", worktree, ["app/a.py"])
ok("and writing without one is a no-op", !(await wasCovered("", worktree, "app/a.py")))

await prune(0)
ok("pruning clears expired sessions", !(await wasCovered(session, worktree, "app/a.py")))

await fs.rm(worktree, { recursive: true, force: true })

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed")
process.exit(failed ? 1 : 0)
