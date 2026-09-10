/**
 * Write a file with a cheap model, so the expensive one never emits its body.
 *
 * The read shunt cuts what the orchestrator ingests. This cuts what it emits,
 * which is the half we had not touched and the more expensive half: Opus output
 * runs 25 $/M against 5 $/M input. A 300-line test file is roughly 4,000 output
 * tokens; the receipt this returns instead is under 200.
 *
 * This is the most dangerous tool in the set, because its failure mode is wrong
 * code on disk that nobody read. Hence the guards below: an allowlist that keeps
 * it away from business logic, no silent overwrites, a backup of anything it
 * replaces, a secret scan, and a syntax check whose result is reported honestly
 * including when no check was possible.
 */

import { tool } from "@opencode-ai/plugin"
import fs from "node:fs/promises"
import path from "node:path"
import {
  batchBudgetChars,
  callWorker,
  loadConfig,
  resolveProfile,
  type WorkerProfile,
} from "../lib/worker"
import {
  DEFAULT_WRITE_PATHS,
  backup,
  checkSyntax,
  extractSymbols,
  isAllowed,
  leaksSecret,
  logTelemetry,
  looksSecret,
  resolveInside,
  stripFences,
} from "../lib/guards"

const MAX_REFERENCES = 8
/** A generated test file has no business exceeding this. */
const MAX_OUTPUT_CHARS = 60_000

const SYSTEM_PROMPT = `You are a code generator. You produce the complete contents of exactly one file.

Rules:
- Output ONLY the file content. No markdown fences, no commentary, no explanation before or after it.
- Match the reference files exactly in convention: import style, naming, fixture usage, assertion style, type annotations, formatting.
- Use only symbols that appear in the reference files. Never invent an import, helper, fixture or factory that is not shown to you.
- Never emit a credential, API key, token, private key, real hostname or connection string. Where a value is needed, use an obviously fake placeholder.
- Cover the cases the instruction asks for and no others. Do not pad with speculative tests.
- If the instruction cannot be carried out from the reference material given, output one line starting with "CANNOT: " followed by what is missing, and nothing else.`

export default tool({
  description: `Generate a file with a cheap worker model instead of writing it yourself.

The body never passes through this conversation, which saves output tokens: the most expensive thing you produce. You get back a receipt with the path, the line count, the symbols defined and a syntax check.

Use this for tests, fixtures, factories and scaffolding derived from code that already exists. Pass the module under test and an existing test file as reference_paths so the worker copies the conventions of the repository.

Do NOT use this for business logic, or for edits to a file that already exists and matters. Restricted by an allowlist to test and fixture paths.`,
  args: {
    path: tool.schema
      .string()
      .describe("Repository-relative path of the file to create. Must match the write allowlist."),
    instruction: tool.schema
      .string()
      .describe(
        "Precisely what the file must contain: which behaviours to cover, which cases, which fixtures to use. Vague instructions produce useless files.",
      ),
    reference_paths: tool.schema
      .array(tool.schema.string())
      .describe(
        `Existing files the worker must imitate and draw symbols from: the code under test, plus a comparable existing test. Maximum ${MAX_REFERENCES}. Omitting these produces invented imports.`,
      ),
    overwrite: tool.schema
      .boolean()
      .optional()
      .describe("Required to replace an existing file. The previous contents are backed up."),
  },
  async execute(args, context) {
    const startedAt = Date.now()
    const worktree = context.worktree

    if (!args.path?.trim()) return "delegate_write error: no path given."
    if (!args.instruction?.trim()) return "delegate_write error: no instruction given."

    const config = await loadConfig(worktree)
    const allowed = config.writePaths ?? DEFAULT_WRITE_PATHS

    let target: { absolute: string; displayPath: string }
    try {
      target = await resolveInside(worktree, args.path)
    } catch (error: any) {
      return `delegate_write error: ${args.path} ${error.message}.`
    }

    if (looksSecret(target.displayPath)) {
      return `delegate_write refused: ${target.displayPath} looks like a secret or credential file.`
    }

    if (!isAllowed(target.displayPath, allowed)) {
      await logTelemetry({
        tool: "delegate_write",
        event: "refused-not-allowed",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
      })
      return (
        `delegate_write refused: ${target.displayPath} is not on the write allowlist.\n\n` +
        `This tool may only create tests, fixtures and scaffolding, because generated code that ` +
        `nobody reads is only safe where a test run can judge it. Write this file yourself, or add ` +
        `the pattern to "writePaths" in .opencode/shunt.json if it genuinely is test scaffolding.\n\n` +
        `Currently allowed: ${allowed.join(", ")}`
      )
    }

    let previous: string | null = null
    try {
      previous = await fs.readFile(target.absolute, "utf8")
    } catch {
      // Does not exist, which is the expected case.
    }
    if (previous !== null && !args.overwrite) {
      return (
        `delegate_write refused: ${target.displayPath} already exists (${previous.split("\n").length} lines).\n\n` +
        `Pass overwrite: true to replace it, having first checked that nothing there is worth keeping. ` +
        `To change part of a file, read the relevant lines and edit them yourself instead.`
      )
    }

    let active: { key: string; profile: WorkerProfile }
    try {
      active = resolveProfile(config, config.writerProfile)
    } catch (error: any) {
      return `delegate_write error: ${error.message}`
    }

    // Reference material. Without it the worker invents imports, so its absence is worth saying.
    const references: string[] = []
    const skipped: string[] = []
    const paths = (args.reference_paths ?? []).slice(0, MAX_REFERENCES)
    const budget = batchBudgetChars(active.profile)
    let referenceChars = 0

    for (const input of paths) {
      if (looksSecret(input)) {
        skipped.push(`${input} (looks like a secret)`)
        continue
      }
      try {
        const absolute = path.resolve(worktree, input)
        const real = await fs.realpath(absolute)
        const realRoot = await fs.realpath(worktree)
        if (real !== realRoot && !real.startsWith(realRoot + path.sep)) {
          skipped.push(`${input} (outside worktree)`)
          continue
        }
        const text = await fs.readFile(real, "utf8")
        if (referenceChars + text.length > budget) {
          skipped.push(`${input} (would exceed the worker's context)`)
          continue
        }
        referenceChars += text.length
        references.push(`=== REFERENCE: ${path.relative(worktree, real)} ===\n${text}`)
      } catch (error: any) {
        skipped.push(`${input} (${error.code === "ENOENT" ? "not found" : error.message})`)
      }
    }

    let result
    try {
      result = await callWorker(
        active.profile,
        SYSTEM_PROMPT,
        [
          `TARGET FILE: ${target.displayPath}`,
          `INSTRUCTION: ${args.instruction}`,
          "",
          references.length ? references.join("\n\n") : "(no reference files were readable)",
        ].join("\n"),
        AbortSignal.timeout(300_000),
      )
    } catch (error: any) {
      await logTelemetry({
        tool: "delegate_write",
        event: "worker-failed",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
        profile: active.key,
        error: String(error?.message ?? error).slice(0, 300),
      })
      return `delegate_write failed: the worker errored (${String(error?.message ?? error).slice(0, 200)}). Nothing was written.`
    }

    const body = stripFences(result.text)

    if (body.startsWith("CANNOT:")) {
      await logTelemetry({
        tool: "delegate_write",
        event: "worker-declined",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
        profile: active.key,
        worker_model: active.profile.model,
      })
      return (
        `delegate_write: the worker declined and nothing was written.\n\n${body.trim()}\n\n` +
        `Give it the missing reference files, or write this one yourself.`
      )
    }

    if (body.trim().length < 20) {
      return `delegate_write failed: the worker returned almost nothing (${body.trim().length} chars). Nothing was written.`
    }
    if (body.length > MAX_OUTPUT_CHARS) {
      return (
        `delegate_write refused: the worker produced ${(body.length / 1024).toFixed(0)} KB, over the ` +
        `${MAX_OUTPUT_CHARS / 1024} KB cap. Nothing was written. Split the instruction into smaller files.`
      )
    }

    if (leaksSecret(body)) {
      await logTelemetry({
        tool: "delegate_write",
        event: "refused-secret-in-output",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
        worker_model: active.profile.model,
      })
      return `delegate_write refused: the generated content contains something shaped like a real credential. Nothing was written.`
    }

    let backedUp: string | null = null
    if (previous !== null) backedUp = await backup(target.displayPath, previous)

    try {
      await fs.mkdir(path.dirname(target.absolute), { recursive: true })
      await fs.writeFile(target.absolute, body, "utf8")
    } catch (error: any) {
      return `delegate_write failed: could not write ${target.displayPath} (${error.message}).`
    }

    const syntax = await checkSyntax(worktree, target.absolute)
    const symbols = extractSymbols(body)
    const lines = body.split("\n").length

    const receipt = [
      `delegate_write: wrote ${target.displayPath}, ${lines} lines / ${(body.length / 1024).toFixed(1)} KB,`,
      `by ${active.profile.model} (profile ${active.key}) in ${((Date.now() - startedAt) / 1000).toFixed(1)}s.`,
      `Its body did not pass through your context, saving roughly ${Math.round(body.length / 3.5)} output tokens.`,
      "",
      syntax.status === "ok"
        ? `SYNTAX: ok (${syntax.detail})`
        : syntax.status === "failed"
          ? `SYNTAX: FAILED. ${syntax.detail}\nThe file is on disk but does not parse. Read the reported lines and fix them, or delete it.`
          : `SYNTAX: not checked (${syntax.detail}). Nothing has verified this file yet.`,
      symbols.length
        ? `DEFINES: ${symbols.slice(0, 30).join(", ")}${symbols.length > 30 ? ` (+${symbols.length - 30} more)` : ""}`
        : `DEFINES: no functions or classes detected, which is suspicious for a generated test file.`,
      backedUp ? `Replaced the previous version; it is kept at ${backedUp}` : "",
      skipped.length ? `Reference files not used: ${skipped.join("; ")}` : "",
      !references.length
        ? `WARNING: no reference files were readable, so imports and fixtures in this file are guesses. Verify them.`
        : "",
      "",
      `Run the tests to find out whether it is correct. Nothing here proves the file does what you asked, only that it parses.`,
    ]
      .filter(Boolean)
      .join("\n")

    await logTelemetry({
      tool: "delegate_write",
      agent: context.agent,
      sessionID: context.sessionID,
      target: target.displayPath,
      instruction: args.instruction.slice(0, 200),
      profile: active.key,
      worker_model: active.profile.model,
      // The pair that measures this tool: output the orchestrator would have had
      // to emit, against the receipt it got instead. Output tokens cost five
      // times input on Opus, so this side of the ledger is the expensive one.
      written_chars: body.length,
      returned_chars: receipt.length,
      lines,
      references: references.length,
      references_skipped: skipped.length,
      overwrote: previous !== null,
      syntax: syntax.status,
      symbols: symbols.length,
      local_prompt_tokens: result.promptTokens,
      local_output_tokens: result.outputTokens,
      duration_ms: Date.now() - startedAt,
    })

    return {
      title: `delegate_write: ${target.displayPath} (${lines} lines, syntax ${syntax.status})`,
      output: receipt,
      metadata: {
        path: target.displayPath,
        lines,
        syntax: syntax.status,
        profile: active.key,
        durationMs: Date.now() - startedAt,
      },
    }
  },
})
