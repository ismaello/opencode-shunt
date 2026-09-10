/**
 * Output shunt: keep huge tool results out of the frontier context.
 *
 * The read shunt stops the orchestrator ingesting large files. Tool *output* is
 * the other half of the same problem: one `pytest` run or one `git diff` can
 * cost more context than the files it touched.
 *
 * The division of labour here is deliberate. Anything the orchestrator needs
 * exactly - error messages, failing test names, stack traces, hunk headers - is
 * extracted with regexes and passed through verbatim. The worker model only ever
 * summarises the surrounding noise. A summariser that paraphrases an assertion
 * message is worse than no summariser at all.
 */

import fs from "node:fs/promises"
import path from "node:path"
import os from "node:os"
import { batchBudgetChars, callWorker, type WorkerProfile } from "./worker"

export const OUTPUT_DIR = path.join(os.homedir(), ".local", "share", "opencode-shunt", "outputs")

export type OutputKind = "pytest" | "git-diff" | "generic"

/** Cap on the verbatim block, so preserving detail cannot itself flood context. */
const VERBATIM_BUDGET = 8_000

const PROMPTS: Record<OutputKind, string> = {
  pytest: `You are summarising the output of a test run for an engineer who cannot see it.

Report, in this order:
1. The counts: how many passed, failed, errored, skipped.
2. The distinct FAILURE CAUSES, grouped. For each group: the exception type, what triggers it, how many tests it accounts for, and at most two example test names.
3. Any collection error, import error or fixture error, which matters more than any assertion failure.
4. Whether one underlying defect explains several groups.

Never enumerate the failing tests one by one: the complete list of test names and the stack traces are preserved separately and reproducing them wastes the summary. If a group has more than two members, give the count and two examples, nothing more.

Do not suggest fixes. Be terse: at most 15 lines.`,

  "git-diff": `You are summarising a diff for an engineer who cannot see it.

Report, in this order:
1. How many files changed, and the overall shape of the change in one sentence.
2. Per file: the path, and what changed in it functionally (not line counts).
3. Anything that looks structural or risky: signature changes, deleted branches, changed defaults, migrations, dependency bumps.

Do not reproduce the diff. Be terse.`,

  generic: `You are summarising command output for an engineer who cannot see it.

Report what the command did, whether it succeeded, and any errors or warnings that appear. Lead with anything that indicates failure. Group repeated messages instead of listing every occurrence.

Do not quote long passages; error lines are preserved separately. Be terse: at most 12 lines.`,
}

export function detectKind(command: string, text: string): OutputKind {
  if (/\bpytest\b|\bpy\.test\b|python -m pytest/.test(command)) return "pytest"
  if (/^=+ (test session starts|FAILURES|ERRORS|short test summary)/m.test(text)) return "pytest"
  if (/\bgit\s+(diff|show|format-patch)\b/.test(command)) return "git-diff"
  if (/^diff --git /m.test(text)) return "git-diff"
  return "generic"
}

/**
 * Lines that must reach the orchestrator unaltered.
 *
 * Deduplicated in order, then capped. When the cap truncates, the caller says so
 * out loud rather than letting the orchestrator assume it saw everything.
 */
export function extractVerbatim(kind: OutputKind, text: string) {
  const lines = text.split("\n")
  const patterns: RegExp[] =
    kind === "pytest"
      ? [
          /^(FAILED|ERROR)\s/, // short test summary entries
          /^E\s{2,}/, // pytest's assertion and exception detail
          /^[\w./-]+:\d+:\s+\w*(Error|Exception)/, // "path.py:12: TypeError"
          /^=+.*\b\d+\s+(failed|passed|error|errors|skipped)/, // the tally line
          /^(ERROR|FAILED)\b.*(collecting|collection|import)/i,
        ]
      : kind === "git-diff"
        ? [/^diff --git /, /^@@ /, /^(new file|deleted file|rename from|rename to|similarity index)/]
        : [
            /\b(error|exception|traceback|fatal|panic|failed|failure)\b/i,
            /^\s*(at|File)\s+["']?[\w./-]+["']?[,:]\s*line\s+\d+/i,
          ]

  const seen = new Set<string>()
  const kept: string[] = []
  let chars = 0
  let dropped = 0

  for (const line of lines) {
    if (!patterns.some((re) => re.test(line))) continue
    const key = line.trim()
    if (!key || seen.has(key)) continue
    seen.add(key)
    if (chars + line.length + 1 > VERBATIM_BUDGET) {
      dropped++
      continue
    }
    kept.push(line)
    chars += line.length + 1
  }

  return { lines: kept, dropped }
}

/** Beyond this a line is minified, base64 or binary, never prose worth reading. */
const MAX_USEFUL_LINE = 1_000

/**
 * Remove content that costs context and carries no meaning for a summariser.
 *
 * A diff of a repository containing a PDF is megabytes of binary in a handful of
 * lines. Feeding that to the worker fills its context with noise and produces a
 * generic answer, which is how this guard came to exist. Only what the model sees
 * is filtered; the stored artifact stays byte for byte.
 */
function stripNoise(text: string) {
  let removed = 0
  const cleaned = text
    // Git's own binary payload marker, plus the encoded block that follows it.
    .replace(/^GIT binary patch$[\s\S]*?(?=^diff --git |\Z)/gm, () => {
      removed++
      return "[binary patch omitted]\n"
    })
    .split("\n")
    .map((line) => {
      if (line.length <= MAX_USEFUL_LINE) return line
      removed++
      return `${line.slice(0, 120)}... [line of ${line.length} characters truncated]`
    })
    .join("\n")
  return { cleaned, removed }
}

/**
 * What to send the worker when the raw output does not fit its context.
 *
 * Head and tail carry the command and its conclusion; the middle of a long run
 * is usually repetition. The elision is marked so the model does not treat the
 * two halves as contiguous.
 */
function fitToBudget(text: string, budget: number) {
  if (text.length <= budget) return { body: text, elided: false }
  const half = Math.floor((budget - 200) / 2)
  const omitted = text.length - half * 2
  return {
    body:
      text.slice(0, half) +
      `\n\n... [${omitted} characters omitted from the middle of this output] ...\n\n` +
      text.slice(-half),
    elided: true,
  }
}

/**
 * OpenCode truncates large bash output before any plugin sees it, keeping head
 * and tail and writing the complete text elsewhere. It says so in the output:
 *
 *   ...output truncated...
 *   Full output saved to: /home/user/.local/share/opencode/tool-output/tool_xxx
 *
 * Summarising the truncated copy would be worse than doing nothing, because the
 * result reads like a summary of everything. So recover the original first.
 */
const TRUNCATION_NOTICE = /^Full output saved to:\s*(\S+)\s*$/m

export async function resolveCompleteOutput(received: string) {
  const match = received.match(TRUNCATION_NOTICE)
  if (!match) return { raw: received, recovered: false, sourcePath: undefined }
  try {
    const raw = await fs.readFile(match[1], "utf8")
    return { raw, recovered: true, sourcePath: match[1] }
  } catch {
    // Better to summarise the truncated copy than to fail, but say so.
    return { raw: received, recovered: false, sourcePath: match[1] }
  }
}

/**
 * Make an output reachable by read_output. When OpenCode already holds the
 * complete text, record a pointer instead of duplicating it.
 */
export async function registerArtifact(
  id: string,
  opts: { text: string; sourcePath?: string; recovered: boolean },
) {
  await fs.mkdir(OUTPUT_DIR, { recursive: true })
  if (opts.recovered && opts.sourcePath) {
    await fs.writeFile(
      path.join(OUTPUT_DIR, `${id}.json`),
      JSON.stringify({ path: opts.sourcePath, lines: opts.text.split("\n").length }),
      "utf8",
    )
    return
  }
  await fs.writeFile(path.join(OUTPUT_DIR, `${id}.txt`), opts.text, "utf8")
}

export type ShuntedOutput = {
  text: string
  kind: OutputKind
  workerModel: string
  promptTokens: number
  outputTokens: number
  verbatimKept: number
  verbatimDropped: number
  elided: boolean
  noiseRemoved: number
  recovered: boolean
  rawLines: number
  rawBytes: number
  ms: number
}

export async function summariseOutput(opts: {
  id: string
  command: string
  /** The output as the tool returned it, possibly already truncated by OpenCode. */
  received: string
  profile: WorkerProfile
  profileKey: string
  signal: AbortSignal
}): Promise<ShuntedOutput> {
  const started = Date.now()
  const { raw, recovered, sourcePath } = await resolveCompleteOutput(opts.received)
  await registerArtifact(opts.id, { text: raw, sourcePath, recovered })

  const kind = detectKind(opts.command, raw)
  const verbatim = extractVerbatim(kind, raw)
  const { cleaned, removed } = stripNoise(raw)
  const { body, elided } = fitToBudget(cleaned, batchBudgetChars(opts.profile))

  const result = await callWorker(
    opts.profile,
    PROMPTS[kind],
    `COMMAND: ${opts.command}\n\nOUTPUT:\n${body}`,
    opts.signal,
  )

  const lineCount = raw.split("\n").length
  const sections = [
    `OUTPUT SHUNT: ${lineCount} lines / ${(raw.length / 1024).toFixed(0)} KB of ${kind} output summarised by ${result.model} (profile ${opts.profileKey}) in ${((Date.now() - started) / 1000).toFixed(1)}s. The raw output did not enter your context.`,
    "",
    "## Summary",
    result.text,
  ]

  if (verbatim.lines.length) {
    sections.push(
      "",
      "## Preserved verbatim (not summarised, safe to trust character for character)",
      "```",
      ...verbatim.lines,
      "```",
    )
    if (verbatim.dropped) {
      sections.push(
        `WARNING: ${verbatim.dropped} further matching lines exceeded the verbatim budget and are not shown. Use read_output to see them.`,
      )
    }
  }

  if (elided) {
    sections.push(
      "",
      "WARNING: the output was too large for the worker's context, so the middle was omitted from the summary. The verbatim block above was extracted from the complete output and is unaffected.",
    )
  }

  if (removed) {
    sections.push(
      "",
      `Note: ${removed} binary or excessively long passages were withheld from the summariser. They are intact in the stored output.`,
    )
  }

  if (sourcePath && !recovered) {
    sections.push(
      "",
      `WARNING: this output was truncated before the shunt saw it and the complete copy at ${sourcePath} could not be read. The summary and the verbatim block above describe only part of the output.`,
    )
  }

  sections.push(
    "",
    `Full output: read_output(id: "${opts.id}", offset: 1, limit: 200). ${lineCount} lines available.`,
  )

  return {
    text: sections.join("\n"),
    kind,
    workerModel: result.model,
    promptTokens: result.promptTokens,
    outputTokens: result.outputTokens,
    verbatimKept: verbatim.lines.length,
    verbatimDropped: verbatim.dropped,
    elided,
    noiseRemoved: removed,
    recovered,
    rawLines: lineCount,
    rawBytes: raw.length,
    ms: Date.now() - started,
  }
}
