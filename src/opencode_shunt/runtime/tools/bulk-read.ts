import { tool } from "@opencode-ai/plugin"
import fs from "node:fs/promises"
import path from "node:path"
import os from "node:os"
import {
  batchBudgetChars,
  callWorker,
  loadConfig,
  resolveProfile,
  type WorkerProfile,
} from "../lib/worker"
import { formatPathCheck, verifyPaths } from "../lib/verify-paths"
import { readCoverage } from "../lib/coverage"
import { assess, loadEconomics } from "../lib/economics"
import { markCovered, prune } from "../lib/covered"

const MAX_FILES = 20
const MAX_BATCHES = 6

/**
 * Floor below which delegating costs more than it saves, now computed from
 * prices in lib/economics.ts rather than fixed at 400 lines.
 *
 * The fixed floor came from evidence: eight of twenty-eight real calls fell
 * below it and plainly wasted money. The arithmetic independently lands within
 * a kilobyte of the same place, which is reassuring, but it also adapts. A long
 * conversation makes the extra round trips dearer and raises the floor; a worker
 * that compresses badly raises it too. A fixed number cannot know either.
 *
 * SHUNT_MIN_DELEGATE_BYTES still overrides it outright, for benchmarking.
 */
const MIN_BYTES_OVERRIDE = process.env.SHUNT_MIN_DELEGATE_BYTES
  ? Number(process.env.SHUNT_MIN_DELEGATE_BYTES)
  : undefined

const SECRET_PATTERNS = [
  /(^|\/)\.env($|\.)/,
  /(^|\/)credentials/i,
  /(^|\/)secrets?($|[._-])/i,
  /(^|\/)service-account/i,
  /\.(pem|key|p12|pfx|jks|keystore)$/i,
  /(^|\/)id_(rsa|dsa|ecdsa|ed25519)$/,
]

const BINARY_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz", ".tiff",
  ".pdf", ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
  ".db", ".sqlite", ".sqlite3", ".parquet", ".avro", ".orc", ".feather",
  ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class", ".pyc",
  ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".wav",
])

/**
 * Answer size past which the delegation has failed at its purpose, as a
 * fraction of the source it was given. A quarter is generous: measured median
 * compression is 0.13, and anything approaching the size of the code is cheaper
 * to read directly.
 */
const MAX_ANSWER_FRACTION = Number(process.env.SHUNT_MAX_ANSWER_FRACTION ?? 0.25)

const COMPRESS_PROMPT = `You shorten a code analysis without losing what it found.

Keep: every file path, every line number, every finding, every warning, the COVERAGE section.
Cut: prose, restatement, explanation of what the code obviously does, quoted code beyond one line per finding, and anything the caller did not ask about.

Output the shortened analysis and nothing else. No preamble, no note about having shortened it.`

const SYSTEM_PROMPT = `You are a code analysis engine. You read source files and answer one specific question about them.

The files are shown with line numbers in the form "NNN| code". Always cite line numbers copied from that gutter, never estimated.

Rules:
- Answer only the question asked. Ignore unrelated observations.
- Stay within the character budget given with the question. It is not advice: an answer approaching the size of the code it describes is worthless, because the caller could have read the code for less. If the question is broad, answer the part that matters and say what you left out.
- Never reproduce whole files. Quote at most 3 lines per finding.
- Copy file paths exactly as they appear in the "=== FILE: ... ===" headers. Never reconstruct a path from the filename.
- Prefer compact structured output over prose.

Begin with a COVERAGE section accounting for every file you were given, one line each:

COVERAGE:
- <exact path>: analysed - <what it contributes, half a sentence>
- <exact path>: not relevant - <why it has no bearing on the question>

Every path appearing in a "=== FILE: ... ===" header must appear exactly once in COVERAGE. "not relevant" is a legitimate and useful answer; leaving a file out is not, because the caller cannot then tell whether you considered it.

For every finding, report:
- file path
- symbol (function/class/model/variable)
- exact line range from the gutter
- evidence: one sentence on why it answers the question
- confidence: high | medium | low

End with an "UNCERTAIN" section listing anything you could not determine from the given files, or "UNCERTAIN: none".`

type FileChunk = {
  displayPath: string
  content: string
  startLine: number
  endLine: number
  chars: number
}

/** Resolve a user-supplied path and refuse anything that escapes the worktree. */
async function resolveInsideWorktree(worktree: string, input: string) {
  const absolute = path.resolve(worktree, input)
  // realpath resolves symlinks, which is what makes the containment check meaningful.
  let real: string
  try {
    real = await fs.realpath(absolute)
  } catch {
    throw new Error(`not found: ${input}`)
  }
  const realRoot = await fs.realpath(worktree)
  if (real !== realRoot && !real.startsWith(realRoot + path.sep)) {
    throw new Error(`outside worktree: ${input}`)
  }
  return real
}

function numberLines(text: string, startLine: number) {
  return text
    .split("\n")
    .map((line, i) => `${String(startLine + i).padStart(5)}| ${line}`)
    .join("\n")
}

/** Split one file into as many line-range chunks as the context budget requires. */
function chunkFile(displayPath: string, text: string, budget: number): FileChunk[] {
  const lines = text.split("\n")
  const chunks: FileChunk[] = []
  let current: string[] = []
  let chars = 0
  let start = 1

  const flush = (endLine: number) => {
    if (!current.length) return
    chunks.push({
      displayPath,
      content: numberLines(current.join("\n"), start),
      startLine: start,
      endLine,
      chars,
    })
    current = []
    chars = 0
    start = endLine + 1
  }

  for (let i = 0; i < lines.length; i++) {
    const cost = lines[i].length + 9 // line content plus the "NNNNN| " gutter
    if (chars + cost > budget && current.length) flush(i)
    current.push(lines[i])
    chars += cost
  }
  flush(lines.length)
  return chunks
}

function renderBatch(batch: FileChunk[]) {
  return batch
    .map(
      (chunk) =>
        `=== FILE: ${chunk.displayPath} (lines ${chunk.startLine}-${chunk.endLine}) ===\n${chunk.content}`,
    )
    .join("\n\n")
}

function packIntoBatches(chunks: FileChunk[], budget: number) {
  const batches: FileChunk[][] = []
  let current: FileChunk[] = []
  let currentChars = 0
  for (const chunk of chunks) {
    if (currentChars + chunk.chars > budget && current.length) {
      batches.push(current)
      current = []
      currentChars = 0
    }
    current.push(chunk)
    currentChars += chunk.chars
  }
  if (current.length) batches.push(current)
  return batches
}

async function logTelemetry(entry: Record<string, unknown>) {
  try {
    const dir = path.join(os.homedir(), ".local", "share", "opencode-shunt")
    await fs.mkdir(dir, { recursive: true })
    await fs.appendFile(
      path.join(dir, "telemetry.jsonl"),
      JSON.stringify({ ts: new Date().toISOString(), ...entry }) + "\n",
    )
  } catch {
    // Telemetry must never break a tool call.
  }
}

export default tool({
  description: `Answer a question about large files WITHOUT loading them into your context.

Reads the files from disk, sends them to a cheap worker model, and returns only a condensed answer with exact line references. The file contents never enter this conversation.

Use this whenever you know which files matter and they are large. Follow up by reading only the specific line ranges it reports. Use grep or glob first if you only need to locate a symbol, and @explorer if you do not yet know which files are relevant.`,
  args: {
    question: tool.schema
      .string()
      .describe(
        "One specific question to answer about these files. Be precise; vague questions waste the call.",
      ),
    paths: tool.schema
      .array(tool.schema.string())
      .describe(`Repository-relative file paths. Maximum ${MAX_FILES}.`),
  },
  async execute(args, context) {
    const startedAt = Date.now()
    const worktree = context.worktree

    if (!args.paths?.length) return "bulk_read error: no paths given."
    if (args.paths.length > MAX_FILES) {
      return `bulk_read error: ${args.paths.length} paths given, maximum is ${MAX_FILES}. Split the request.`
    }

    const config = await loadConfig(worktree)
    let active: { key: string; profile: WorkerProfile }
    try {
      active = resolveProfile(config)
    } catch (error: any) {
      return `bulk_read error: ${error.message}`
    }

    const budget = batchBudgetChars(active.profile)
    const chunks: FileChunk[] = []
    const skipped: string[] = []
    const sizes: { displayPath: string; lines: number }[] = []
    let totalBytes = 0
    let totalLines = 0

    for (const input of args.paths) {
      const basename = path.basename(input)
      if (SECRET_PATTERNS.some((re) => re.test(input) || re.test(basename))) {
        skipped.push(`${input} (looks like a secret)`)
        continue
      }
      if (BINARY_EXTENSIONS.has(path.extname(input).toLowerCase())) {
        skipped.push(`${input} (binary extension)`)
        continue
      }

      let absolute: string
      try {
        absolute = await resolveInsideWorktree(worktree, input)
      } catch (error: any) {
        skipped.push(`${input} (${error.message})`)
        continue
      }

      const stat = await fs.stat(absolute)
      if (!stat.isFile()) {
        skipped.push(`${input} (not a regular file)`)
        continue
      }

      const buffer = await fs.readFile(absolute)
      if (buffer.subarray(0, 8000).includes(0)) {
        skipped.push(`${input} (binary content)`)
        continue
      }

      const text = buffer.toString("utf8")
      const displayPath = path.relative(worktree, absolute)
      const lineCount = text.split("\n").length
      totalBytes += buffer.byteLength
      totalLines += lineCount
      sizes.push({ displayPath, lines: lineCount })
      chunks.push(...chunkFile(displayPath, text, budget))
    }

    if (!chunks.length) {
      return `bulk_read: nothing to read.\nSkipped:\n- ${skipped.join("\n- ")}`
    }

    const economics = loadEconomics(config.economics)
    const verdict = assess(totalBytes, economics)
    const floor = MIN_BYTES_OVERRIDE ?? verdict.breakEvenChars

    if (totalBytes < floor) {
      await logTelemetry({
        tool: "bulk_read",
        event: "refused-too-small",
        agent: context.agent,
        sessionID: context.sessionID,
        question: args.question.slice(0, 200),
        files: sizes.length,
        lines: totalLines,
        bytes: totalBytes,
        break_even_chars: floor,
        net_usd: Number(verdict.net.toFixed(4)),
      })
      const listing = sizes.map((s) => `  - ${s.displayPath} (${s.lines} lines)`).join("\n")
      // Without this the caller sees a small total and never learns that most of
      // what it asked for was silently dropped for being unreadable.
      const missing = skipped.length
        ? `\n\nNote that ${skipped.length} of the paths you gave ${skipped.length === 1 ? "was" : "were"} ` +
          `not read at all, so the total above covers less than you asked for:\n- ${skipped.join("\n- ")}`
        : ""
      return (
        `bulk_read declined: these files total ${totalLines} lines / ${(totalBytes / 1024).toFixed(1)} KB, ` +
        `below the ${(floor / 1024).toFixed(1)} KB at which delegating starts to pay for itself.\n\n` +
        `${listing}${missing}\n\n` +
        `Read them yourself; the read shunt will not block you at this size. ` +
        `The arithmetic: ${verdict.explain}, so delegating this would cost about ` +
        `$${Math.abs(verdict.net).toFixed(3)} more than reading it.`
      )
    }

    let batches = packIntoBatches(chunks, budget)
    let usedProfile = active
    let overflowNote = ""

    // Chunking loses the relationships between files, which is usually the point
    // of the question. If a bigger worker is configured, prefer one whole call.
    if (batches.length > 1 && config.overflowProfile && config.overflowProfile !== active.key) {
      try {
        const overflow = resolveProfile(config, config.overflowProfile)
        const wholeBudget = batchBudgetChars(overflow.profile)
        const totalChars = chunks.reduce((sum, chunk) => sum + chunk.chars, 0)
        if (totalChars <= wholeBudget) {
          usedProfile = overflow
          batches = [chunks]
          overflowNote = `Content did not fit ${active.key}; answered in one call by ${overflow.key} (${overflow.profile.model}).`
        }
      } catch (error: any) {
        overflowNote = `Overflow profile unavailable (${error.message}); chunked instead.`
      }
    }

    const truncated = batches.length > MAX_BATCHES
    const running = truncated ? batches.slice(0, MAX_BATCHES) : batches

    const answers: string[] = []
    const failedPaths = new Set<string>()
    let promptTokens = 0
    let outputTokens = 0
    let failures = 0

    for (const [index, batch] of running.entries()) {
      context.metadata({
        title: `bulk_read ${index + 1}/${running.length} via ${usedProfile.key}`,
        metadata: { files: batch.map((chunk) => chunk.displayPath) },
      })
      try {
        const result = await callWorker(
          usedProfile.profile,
          SYSTEM_PROMPT,
          `QUESTION: ${args.question}\n\nCHARACTER BUDGET: ${Math.round((batch.reduce((n, c) => n + c.chars, 0) * MAX_ANSWER_FRACTION))}\n\n${renderBatch(batch)}`,
          context.abort,
        )
        promptTokens += result.promptTokens
        outputTokens += result.outputTokens
        answers.push(
          running.length > 1
            ? `### Part ${index + 1}/${running.length} (${batch.map((c) => c.displayPath).join(", ")})\n${result.text}`
            : result.text,
        )
      } catch (error: any) {
        // Fail short, never block the session.
        failures++
        for (const chunk of batch) failedPaths.add(chunk.displayPath)
        answers.push(
          `### Part ${index + 1}/${running.length} FAILED\nWorker error: ${error.message}\nFall back to reading these files yourself: ${batch.map((c) => c.displayPath).join(", ")}`,
        )
      }
    }

    let combined = answers.join("\n\n")
    const expected = [...new Set(chunks.map((chunk) => chunk.displayPath))]
    const coverage = readCoverage(combined, expected)
    let analysed = coverage.analysed
    let notRelevant = coverage.notRelevant
    let missing = coverage.missing

    // A second, focused pass over just the files the worker passed over in
    // silence. Retrying only what was missed keeps cross-file reasoning intact
    // for the usual case, and costs an extra call only when something went wrong.
    const retryable = missing.filter((path) => !failedPaths.has(path))
    const retryChunks = chunks.filter((chunk) => retryable.includes(chunk.displayPath))
    const retryChars = retryChunks.reduce((total, chunk) => total + chunk.chars, 0)
    let retried: string[] = []

    if (retryChunks.length && retryChars <= budget) {
      retried = retryable
      context.metadata({
        title: `bulk_read second pass: ${retried.length} file(s) the worker skipped`,
        metadata: { files: retried },
      })
      try {
        const result = await callWorker(
          usedProfile.profile,
          SYSTEM_PROMPT,
          `QUESTION: ${args.question}\n\nCHARACTER BUDGET: ${Math.round(retryChars * MAX_ANSWER_FRACTION)}\n\n${renderBatch(retryChunks)}`,
          context.abort,
        )
        promptTokens += result.promptTokens
        outputTokens += result.outputTokens
        combined += `\n\n### Second pass over files omitted from the first answer (${retried.join(", ")})\n${result.text}`

        // Parsed against the retried subset, because the appended text carries a
        // COVERAGE section of its own that only accounts for those files.
        const second = readCoverage(result.text, retried)
        analysed = [...analysed, ...second.analysed]
        notRelevant = [...notRelevant, ...second.notRelevant]
        missing = [...second.missing, ...missing.filter((path) => failedPaths.has(path))]
      } catch (error: any) {
        failures++
        combined += `\n\n### Second pass FAILED\nWorker error: ${error.message}`
      }
    }

    // A summary longer than the code is not a summary, and this tool exists on
    // the promise that it is. Observed: a broad question over 17.7 KB of source
    // came back as 27.5 KB of prose, so the delegation cost a worker call and a
    // round trip to put *more* in the expensive context than reading the files
    // would have. The prompt asks for brevity; nothing enforced it.
    const bloatCeiling = Math.round(totalBytes * MAX_ANSWER_FRACTION)
    let compressed = 0
    if (combined.length > bloatCeiling) {
      const before = combined.length
      try {
        const result = await callWorker(
          usedProfile.profile,
          COMPRESS_PROMPT,
          `Reduce this to at most ${bloatCeiling} characters.\n\n${combined}`,
          context.abort,
        )
        promptTokens += result.promptTokens
        outputTokens += result.outputTokens
        // Only if it actually helped. A compression pass that grows the text is
        // the same failure again, and the original at least answers the question.
        if (result.text.length < before) {
          combined = result.text
          compressed = before - result.text.length
        }
      } catch {
        // Returning a long answer beats returning none.
      }
    }

    const { checks, text: corrected } = await verifyPaths(combined, worktree)
    const pathCheck = formatPathCheck(checks)

    const notes: string[] = []
    if (overflowNote) notes.push(overflowNote)
    if (skipped.length) notes.push(`Skipped: ${skipped.join("; ")}`)
    if (notRelevant.length) {
      // Stated rather than warned about: a file being irrelevant is an answer, and
      // knowing it was considered saves the caller from reading it to find out.
      notes.push(
        `Considered and found nothing bearing on the question: ${notRelevant.join(", ")}.`,
      )
    }
    if (retried.length) {
      notes.push(
        `Note: ${retried.join(", ")} ${retried.length === 1 ? "was" : "were"} omitted from the first answer and analysed in a second pass, appended above.`,
      )
    }
    if (missing.length) {
      notes.push(
        `WARNING: ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} unaccounted for. ` +
          `${missing.length === 1 ? "It was" : "They were"} read from disk but the worker neither analysed ` +
          `${missing.length === 1 ? "it" : "them"} nor declared ${missing.length === 1 ? "it" : "them"} irrelevant, ` +
          `even after a second attempt. Treat this answer as covering only the other files.`,
      )
    }
    if (!coverage.declared) {
      notes.push(
        `Note: the worker did not produce the requested COVERAGE section, so which files it truly examined was inferred from mentions in its answer.`,
      )
    }
    if (running.length > 1) {
      // Silent partial answers are the failure mode this warning exists to prevent.
      notes.push(
        `WARNING: this answer was assembled from ${running.length} separate passes, so relationships spanning parts may be missed. Configure an overflowProfile in .opencode/shunt.json to avoid this.`,
      )
    }
    if (truncated) {
      notes.push(
        `WARNING: only the first ${MAX_BATCHES} of ${batches.length} chunks were analysed. Narrow the path list.`,
      )
    }

    const footer = [
      "",
      "---",
      `bulk_read: ${totalLines} lines / ${(totalBytes / 1024).toFixed(0)} KB analysed by ${usedProfile.profile.model} (profile ${usedProfile.key}) in ${((Date.now() - startedAt) / 1000).toFixed(1)}s. These lines did not enter your context.`,
      pathCheck,
      ...notes,
    ]
      .filter(Boolean)
      .join("\n")

    const output = corrected + "\n" + footer

    // So the read hook can tell a first read from a re-read of what it just
    // summarised. Only what the worker accounted for: analysed, or examined and
    // declared irrelevant.
    //
    // This used to pass every file it managed to read from disk, which put the
    // tool at odds with itself - it printed "WARNING: x is unaccounted for" and
    // in the same breath recorded x as covered, giving the read hook grounds to
    // block the orchestrator from ever looking at it. The one file known to be
    // missing evidence was the one hardest to get.
    await markCovered(context.sessionID, worktree, [...analysed, ...notRelevant])
    void prune()

    await logTelemetry({
      tool: "bulk_read",
      agent: context.agent,
      sessionID: context.sessionID,
      question: args.question.slice(0, 200),
      // The pair that measures the operation: what the caller would have ingested
      // by reading these files, against what it ingested instead. Both in the same
      // units, so no tokeniser assumptions are needed to compare them.
      content_chars: totalBytes,
      returned_chars: output.length,
      // Chars a second pass removed after the worker overran its budget. Zero
      // is the normal case; a rising figure means the questions being asked are
      // too broad for the tool to be paying for itself.
      compressed_chars: compressed,
      profile: usedProfile.key,
      worker_model: usedProfile.profile.model,
      overflowed: usedProfile.key !== active.key,
      files: args.paths.length,
      skipped: skipped.length,
      bytes: totalBytes,
      lines: totalLines,
      batches: running.length,
      failures,
      local_prompt_tokens: promptTokens,
      local_output_tokens: outputTokens,
      paths_verified: checks.filter((c) => c.status === "VERIFIED").length,
      paths_corrected: checks.filter((c) => c.status === "CORRECTED").length,
      paths_ambiguous: checks.filter((c) => c.status === "AMBIGUOUS").length,
      paths_not_found: checks.filter((c) => c.status === "NOT_FOUND").length,
      coverage_declared: coverage.declared,
      coverage_analysed: analysed.length,
      coverage_not_relevant: notRelevant.length,
      coverage_retried: retried.length,
      // Files still unaccounted for after the second pass. The old files_uncited
      // counted irrelevant files too, which made it useless as a quality signal.
      files_uncited: missing.length,
      duration_ms: Date.now() - startedAt,
    })

    return {
      title: `bulk_read: ${args.paths.length} file(s), ${totalLines} lines`,
      output,
      metadata: {
        lines: totalLines,
        bytes: totalBytes,
        profile: usedProfile.key,
        localPromptTokens: promptTokens,
        durationMs: Date.now() - startedAt,
      },
    }
  },
})
