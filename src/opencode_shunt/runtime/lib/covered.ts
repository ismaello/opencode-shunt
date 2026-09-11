/**
 * Remembers which files a worker already summarised in a session.
 *
 * This exists because of a measured failure. The orchestrator delegated a
 * six-file review to the worker exactly as intended, got back a summary at 73%
 * compression, and then read all six files in full anyway. Every file was small
 * enough that the read shunt could not object to any of them individually, so
 * the delegation added a round trip and a summary to a context that ended up
 * holding the whole codebase regardless: it cost more than not delegating.
 *
 * The prompt already asks for targeted follow-up reads. This makes the request
 * enforceable, by letting the read hook tell the difference between "read a
 * file" and "re-read a file I was just given a summary of".
 *
 * State lives on disk rather than in memory because the tool and the plugin are
 * separate modules and their sharing a process is an assumption, not a promise.
 * A missing or unreadable file means no record, which allows the read: failing
 * open matters more here than perfect bookkeeping.
 */

import fs from "node:fs/promises"
import path from "node:path"
import os from "node:os"

const STATE_DIR = path.join(os.homedir(), ".local", "share", "opencode-shunt", "covered")

/** Session ids come from OpenCode, but they end up as filenames. */
function stateFile(sessionID: string): string {
  return path.join(STATE_DIR, `${sessionID.replace(/[^A-Za-z0-9_-]/g, "")}.json`.slice(-120))
}

/** Repository-relative and normalised, so the tool and the hook agree on a key. */
function key(worktree: string, filePath: string): string {
  const absolute = path.resolve(worktree, filePath)
  return path.relative(worktree, absolute)
}

/** Record that these files were summarised for this session. */
export async function markCovered(
  sessionID: string,
  worktree: string,
  filePaths: string[],
): Promise<void> {
  if (!sessionID || !filePaths.length) return
  try {
    await fs.mkdir(STATE_DIR, { recursive: true })
    const file = stateFile(sessionID)
    const existing = await readCovered(sessionID)
    for (const filePath of filePaths) existing.add(key(worktree, filePath))
    await fs.writeFile(file, JSON.stringify({ at: Date.now(), files: [...existing] }), "utf8")
  } catch {
    // Never break a successful delegation over bookkeeping.
  }
}

async function readCovered(sessionID: string): Promise<Set<string>> {
  try {
    const parsed = JSON.parse(await fs.readFile(stateFile(sessionID), "utf8"))
    return new Set(Array.isArray(parsed?.files) ? parsed.files : [])
  } catch {
    return new Set()
  }
}

/** Was this file already summarised for this session? */
export async function wasCovered(
  sessionID: string,
  worktree: string,
  filePath: string,
): Promise<boolean> {
  if (!sessionID) return false
  return (await readCovered(sessionID)).has(key(worktree, filePath))
}

/**
 * Drop session records older than a day. Called opportunistically rather than
 * scheduled; the cost of an occasional stale file is nothing, and a cleanup
 * that can fail silently is preferable to a daemon.
 */
export async function prune(maxAgeMs = 24 * 60 * 60 * 1000): Promise<void> {
  try {
    const now = Date.now()
    for (const name of await fs.readdir(STATE_DIR)) {
      const file = path.join(STATE_DIR, name)
      const stat = await fs.stat(file)
      if (now - stat.mtimeMs > maxAgeMs) await fs.rm(file, { force: true })
    }
  } catch {
    // Nothing to prune, or nowhere to prune it from.
  }
}
