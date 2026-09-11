/**
 * Tests for resolving the repository root.
 *
 * This exists because of a failure that produced no error, no warning and no
 * telemetry: OpenCode passed "/" as the worktree, every relative path the model
 * read resolved to /src/..., the stat missed, and the hook returned silently.
 * The install looked healthy and did nothing.
 *
 * The asymmetry that hid it is worth keeping in mind while reading these:
 * Claude and GPT pass absolute file paths, which resolve correctly from any
 * root at all, so the bug was invisible until a Gemini session read something.
 *
 * Run: node --experimental-strip-types --no-warnings tests/worktree.test.mjs
 */

import assert from "node:assert/strict"
import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"

import { resolveWorktree } from "../lib/worktree.ts"

let failures = 0
const tests = []
const test = (name, fn) => tests.push([name, fn])

async function scratch(withInstall = true) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "worktree-"))
  if (withInstall) await fs.mkdir(path.join(dir, ".opencode"))
  return dir
}

test("a root holding the install is used as given", async () => {
  const dir = await scratch()
  const result = await resolveWorktree(dir)
  assert.equal(result.root, dir)
  assert.equal(result.recovered, false)
})

test('"/" is rejected even though it is a real directory', async () => {
  const cwd = await scratch()
  const result = await resolveWorktree("/", cwd)
  assert.equal(result.root, cwd, "should fall back to the cwd that holds .opencode")
  assert.equal(result.recovered, true, "the caller has to be able to report this")
  assert.equal(result.given, "/", "what we were handed is kept, for the record")
})

test("an empty worktree falls back rather than resolving against nothing", async () => {
  const cwd = await scratch()
  const result = await resolveWorktree("", cwd)
  assert.equal(result.root, cwd)
  assert.equal(result.recovered, true)
})

test("undefined is handled, since the field may simply be absent", async () => {
  const cwd = await scratch()
  const result = await resolveWorktree(undefined, cwd)
  assert.equal(result.root, cwd)
  assert.equal(result.given, null)
})

test("a directory without .opencode is not believed", async () => {
  const bare = await scratch(false)
  const cwd = await scratch()
  const result = await resolveWorktree(bare, cwd)
  assert.equal(result.root, cwd)
  assert.equal(result.recovered, true)
})

test("when nothing can be verified, what we were told wins over a guess", async () => {
  const bare = await scratch(false)
  const alsoBare = await scratch(false)
  const result = await resolveWorktree(bare, alsoBare)
  assert.equal(result.root, bare)
  // Not flagged as recovered: nothing was recovered, and claiming otherwise
  // would put a misleading line in the telemetry.
  assert.equal(result.recovered, false)
})

test("the resolved root makes a relative read resolve to a real file", async () => {
  const dir = await scratch()
  await fs.mkdir(path.join(dir, "src"))
  await fs.writeFile(path.join(dir, "src", "app.py"), "x = 1\n")

  const { root } = await resolveWorktree("/", dir)
  // This is the exact expression the read hook uses, and the exact one that
  // produced "/src/app.py" before.
  const resolved = path.resolve(root, "src/app.py")
  assert.equal((await fs.stat(resolved)).isFile(), true)
})

for (const [name, fn] of tests) {
  try {
    await fn()
    console.log(`ok   ${name}`)
  } catch (error) {
    failures++
    console.log(`FAIL ${name}\n     ${error.message}`)
  }
}
process.exit(failures ? 1 : 0)
