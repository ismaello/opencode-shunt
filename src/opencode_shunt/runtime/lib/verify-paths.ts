/**
 * Deterministic path verification for worker answers.
 *
 * The observed failure mode of the local model is a plausible but wrong path:
 * the right filename in a directory it assumed. A prompt instruction does not
 * prevent that; checking against the filesystem does.
 */

import fs from "node:fs/promises"
import path from "node:path"

export type PathStatus = "VERIFIED" | "CORRECTED" | "AMBIGUOUS" | "NOT_FOUND"

export type PathCheck = {
  candidate: string
  status: PathStatus
  /** Set for CORRECTED: the real path the candidate was rewritten to. */
  resolved?: string
  /** Set for AMBIGUOUS: the tied candidates, capped for display. */
  alternatives?: string[]
  /** Set for AMBIGUOUS: how many tied candidates there really are. */
  alternativeCount?: number
}

const CODE_EXTENSIONS =
  "py|ts|tsx|js|jsx|mjs|cjs|sql|go|rs|java|kt|rb|php|cs|swift|scala|sh|bash|zsh|" +
  "md|json|ya?ml|toml|ini|cfg|conf|tf|tfvars|proto|graphql|css|scss|html|vue|svelte"

/** Paths as they appear in prose: optional directories plus a code extension. */
const PATH_PATTERN = new RegExp(
  String.raw`(?:[\w.@-]+\/)*[\w.@-]+\.(?:${CODE_EXTENSIONS})\b`,
  "g",
)

const SKIP_DIRECTORIES = new Set([
  ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
  ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".cache",
  "target", "vendor", ".tox", "site-packages", ".opencode",
])

const MAX_INDEXED_FILES = 50_000

/** Map every basename in the worktree to the relative paths that carry it. */
async function indexByBasename(worktree: string) {
  const index = new Map<string, string[]>()
  let count = 0

  async function walk(directory: string) {
    if (count >= MAX_INDEXED_FILES) return
    let entries
    try {
      entries = await fs.readdir(directory, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      if (count >= MAX_INDEXED_FILES) return
      if (entry.name.startsWith(".") && entry.isDirectory() && !SKIP_DIRECTORIES.has(entry.name)) {
        continue
      }
      const absolute = path.join(directory, entry.name)
      if (entry.isDirectory()) {
        if (SKIP_DIRECTORIES.has(entry.name)) continue
        await walk(absolute)
      } else if (entry.isFile()) {
        count++
        const relative = path.relative(worktree, absolute)
        const bucket = index.get(entry.name)
        if (bucket) bucket.push(relative)
        else index.set(entry.name, [relative])
      }
    }
  }

  await walk(worktree)
  return index
}

async function exists(worktree: string, candidate: string) {
  try {
    const stat = await fs.stat(path.resolve(worktree, candidate))
    return stat.isFile()
  } catch {
    return false
  }
}

/**
 * How many trailing path segments two paths share.
 *
 * A model that gets the directory wrong usually still gets the last segment or
 * two right, so the closest suffix match is almost always the intended file.
 * Ranking by this turns most would-be ambiguities into confident corrections
 * without ever guessing between equally plausible candidates.
 */
function suffixScore(candidate: string, actual: string): number {
  const a = candidate.split("/").filter(Boolean).reverse()
  const b = actual.split("/").filter(Boolean).reverse()
  let score = 0
  while (score < a.length && score < b.length && a[score] === b[score]) score++
  return score
}

export async function verifyPaths(text: string, worktree: string) {
  const candidates = [...new Set(text.match(PATH_PATTERN) ?? [])]
  if (!candidates.length) return { checks: [] as PathCheck[], text }

  const checks: PathCheck[] = []
  let index: Map<string, string[]> | null = null

  for (const candidate of candidates) {
    if (await exists(worktree, candidate)) {
      checks.push({ candidate, status: "VERIFIED" })
      continue
    }
    // Build the index lazily: most answers cite only paths that already resolve.
    index ??= await indexByBasename(worktree)
    const matches = index.get(path.basename(candidate)) ?? []
    if (!matches.length) {
      checks.push({ candidate, status: "NOT_FOUND" })
      continue
    }
    const best = Math.max(...matches.map((m) => suffixScore(candidate, m)))
    const winners = matches.filter((m) => suffixScore(candidate, m) === best)
    if (winners.length === 1) {
      checks.push({ candidate, status: "CORRECTED", resolved: winners[0] })
    } else {
      checks.push({
        candidate,
        status: "AMBIGUOUS",
        alternatives: winners.slice(0, 5),
        alternativeCount: winners.length,
      })
    }
  }

  // Rewrite unambiguous corrections in place so the orchestrator reads the right
  // file, and report every one of them below so nothing is corrected silently.
  //
  // This must be a single pass driven by the same pattern used to extract the
  // candidates. Replacing candidates one by one corrupts longer paths that end
  // with a shorter candidate: rewriting "chat/x.py" to "src/pkg/chat/x.py" would
  // also hit the tail of an already-correct "src/pkg/chat/x.py" elsewhere in the
  // text and produce "src/pkg/src/pkg/chat/x.py".
  const corrections = new Map(
    checks
      .filter((check) => check.status === "CORRECTED" && check.resolved)
      .map((check) => [check.candidate, check.resolved!]),
  )
  const rewritten = corrections.size
    ? text.replace(PATH_PATTERN, (match) => corrections.get(match) ?? match)
    : text

  return { checks, text: rewritten }
}

/** Human-readable footer. Returns an empty string when everything checked out. */
export function formatPathCheck(checks: PathCheck[]): string {
  const problems = checks.filter((c) => c.status !== "VERIFIED")
  if (!checks.length) return ""
  if (!problems.length) return `PATH CHECK: ${checks.length} verified.`

  const lines = [`PATH CHECK: ${checks.length - problems.length} verified, ${problems.length} flagged.`]
  for (const check of problems) {
    if (check.status === "CORRECTED") {
      lines.push(`- CORRECTED  ${check.candidate} -> ${check.resolved} (rewritten above)`)
    } else if (check.status === "AMBIGUOUS") {
      const shown = check.alternatives ?? []
      const total = check.alternativeCount ?? shown.length
      const more = total > shown.length ? `, and ${total - shown.length} more` : ""
      lines.push(
        `- AMBIGUOUS  ${check.candidate} matches ${total} files equally well: ` +
          `${shown.join(", ")}${more}. Not rewritten; confirm before using.`,
      )
    } else {
      lines.push(`- NOT_FOUND  ${check.candidate} does not exist in this repository. Do not trust it.`)
    }
  }
  return lines.join("\n")
}
