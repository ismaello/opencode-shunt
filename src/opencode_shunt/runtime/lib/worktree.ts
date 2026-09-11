/**
 * The repository root, insisted upon rather than trusted.
 *
 * OpenCode hands the plugin a worktree. On a real session it handed over "/",
 * and path.resolve("/", "src/app.py") is "/src/app.py", which exists nowhere.
 * Every relative path the model read missed, the stat threw, and the hook
 * returned without a word.
 *
 * The damage was wider than the missed reads. loadExempt and loadConfig read
 * from the same root, so the exemption list came back empty and the delegation
 * economics fell back to defaults belonging to no repository at all. The shunt
 * was loaded, passed every health check, and did nothing.
 *
 * Which models it hits is not random: measured over 193 recorded reads, Claude
 * and GPT pass absolute paths and Gemini passes relative ones. So the same
 * installation works or silently does not depending on who is orchestrating.
 */

import fs from "node:fs/promises"
import path from "node:path"

export type Worktree = { root: string; given: string | null; recovered: boolean }

async function holdsInstall(directory: string): Promise<boolean> {
  try {
    return (await fs.stat(path.join(directory, ".opencode"))).isDirectory()
  } catch {
    return false
  }
}

/** A root is believed only if it holds the .opencode we were installed into. */
export async function resolveWorktree(given: string | undefined | null, cwd = process.cwd()): Promise<Worktree> {
  // "/" is excluded even though it is a real directory: it is the value that
  // arrives when the worktree is missing, and a stray /.opencode would then
  // make a broken session look fine.
  const candidates = [given, cwd].filter((c): c is string => Boolean(c) && c !== "/")

  for (const candidate of candidates) {
    if (await holdsInstall(candidate)) {
      return { root: candidate, given: given ?? null, recovered: candidate !== given }
    }
  }

  // Nothing verifiable. Prefer what we were told over a guess, but report that
  // it was never confirmed, because the alternative is failing silently again.
  return { root: given || cwd, given: given ?? null, recovered: false }
}
