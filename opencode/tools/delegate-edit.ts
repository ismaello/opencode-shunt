/**
 * Apply a repetitive mechanical edit with a cheap model, so the expensive one
 * never emits the changes.
 *
 * This targets the third of the bill the read shunt cannot touch. Measured over
 * sixty real Opus sessions, output is 34.1% of what we spend, at 25 $/M against
 * 6.25 $/M for content entering the context.
 *
 * It is deliberately not for single small edits. OpenCode's edit tool works by
 * search and replace, so changing one line already costs only that line, and
 * delegating it would save nothing while adding a round trip. What costs real
 * money is the same mechanical change repeated across many sites: annotating
 * forty functions means emitting forty pairs of old and new text. Same lesson as
 * reading, then - volume, not size.
 *
 * The design decision that makes this safe enough to exist: the worker returns
 * the whole file and the diff is computed here. It cannot misreport its own
 * edit, and a wholesale rewrite dressed up as a tidy-up shows up immediately as
 * a diff touching most of the file, which is refused.
 */

import { tool } from "@opencode-ai/plugin"
import fs from "node:fs/promises"
import path from "node:path"
import { callWorker, loadConfig, resolveProfile, type WorkerProfile } from "../lib/worker"
import {
  DEFAULT_WRITE_PATHS,
  backup,
  checkSyntax,
  isAllowed,
  leaksSecret,
  logTelemetry,
  looksAbbreviated,
  looksSecret,
  resolveInside,
  stripFences,
  unifiedDiff,
} from "../lib/guards"
import { OUTPUT_DIR } from "../lib/output-shunt"

/** Below this there is nothing repetitive to delegate; just edit it. */
const MIN_BYTES = 1_500
/** Beyond this the worker's context and its patience both suffer. */
const MAX_BYTES = 200_000
/**
 * A returned file this much shorter than the original means the model abbreviated
 * instead of reproducing it, whatever it claims. The usual form is a comment
 * saying the rest is unchanged.
 */
const MIN_RETURNED_FRACTION = 0.75
/** A diff touching more of the file than this is a rewrite, not an edit. */
const MAX_CHANGE_FRACTION = 0.6
/**
 * Diff lines put in front of the caller; the rest goes to read_output.
 *
 * This is the tool's central trade-off. The diff is what makes a delegated edit
 * trustworthy, but it lands in the expensive context and eats the saving it was
 * meant to produce. Thirty lines is about six hunks, which for the repetitive
 * changes this tool is for is enough to see whether the pattern was applied
 * correctly; the remaining sites are the same thing again.
 */
const DIFF_PREVIEW_LINES = 30

const SYSTEM_PROMPT = `You apply one mechanical edit to a source file and return the result.

Rules:
- Return the COMPLETE file: every line, from the first to the last, with your change applied.
- Never abbreviate. Never write "rest of file unchanged", never elide a section, never use an ellipsis to stand in for code. The file is written to disk exactly as you return it, so anything you leave out is deleted.
- Change only what the instruction asks for. Leave formatting, comments, blank lines, import order and unrelated code exactly as they are.
- Apply the change at EVERY site it applies to, not just the first.
- Invent nothing: no new imports, helpers or dependencies unless the instruction names them.
- Output only the file content. No markdown fences, no commentary before or after.
- If the instruction is ambiguous, cannot be applied, or would require judgement about behaviour, output one line starting with "CANNOT: " and what is missing, and nothing else.`

export default tool({
  description: `Apply a repetitive mechanical edit to a file using a cheap worker model, without emitting the changes yourself.

Output tokens are the most expensive thing you produce, so this is for changes that would otherwise have you emit many search-and-replace pairs: adding type annotations throughout a module, adding docstrings to every public function, renaming a symbol at every site, migrating a formatting style. You get back a real diff, computed here rather than reported by the model, plus a syntax check.

Do NOT use this for a single edit, where it saves nothing, nor for anything requiring judgement about behaviour: bug fixes, logic changes, security or data correctness. Those are yours. Restricted by an allowlist.`,
  args: {
    path: tool.schema.string().describe("Repository-relative path of the file to edit."),
    instruction: tool.schema
      .string()
      .describe(
        "The mechanical change, stated precisely enough to apply without judgement. Name the pattern to look for and what it becomes.",
      ),
  },
  async execute(args, context) {
    const startedAt = Date.now()
    const worktree = context.worktree

    if (!args.path?.trim()) return "delegate_edit error: no path given."
    if (!args.instruction?.trim()) return "delegate_edit error: no instruction given."

    const config = await loadConfig(worktree)
    // Editing existing code is riskier than creating a test, so it defaults to
    // the same test-only allowlist. Widening it is a deliberate per-repo choice.
    const allowed = config.editPaths ?? config.writePaths ?? DEFAULT_WRITE_PATHS

    let target: { absolute: string; displayPath: string }
    try {
      target = await resolveInside(worktree, args.path)
    } catch (error: any) {
      return `delegate_edit error: ${args.path} ${error.message}.`
    }

    if (looksSecret(target.displayPath)) {
      return `delegate_edit refused: ${target.displayPath} looks like a secret or credential file.`
    }

    if (!isAllowed(target.displayPath, allowed)) {
      await logTelemetry({
        tool: "delegate_edit",
        event: "refused-not-allowed",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
      })
      return (
        `delegate_edit refused: ${target.displayPath} is not on the edit allowlist.\n\n` +
        `A worker may only touch files whose correctness a test run can judge, because ` +
        `nobody reads what it produces. Make this change yourself, or add the pattern to ` +
        `"editPaths" in .opencode/shunt.json if this file genuinely qualifies.\n\n` +
        `Currently allowed: ${allowed.join(", ")}`
      )
    }

    let before: string
    try {
      before = await fs.readFile(target.absolute, "utf8")
    } catch (error: any) {
      return `delegate_edit error: cannot read ${target.displayPath} (${error.code === "ENOENT" ? "does not exist; use delegate_write to create it" : error.message}).`
    }

    if (before.length < MIN_BYTES) {
      await logTelemetry({
        tool: "delegate_edit",
        event: "refused-too-small",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
        bytes: before.length,
      })
      return (
        `delegate_edit declined: ${target.displayPath} is only ${(before.length / 1024).toFixed(1)} KB ` +
        `(${before.split("\n").length} lines), too small for a delegated edit to pay for itself.\n\n` +
        `Edit it yourself. Search and replace already costs you only the lines you change, so ` +
        `delegating this would add a round trip and save nothing.`
      )
    }
    if (before.length > MAX_BYTES) {
      return (
        `delegate_edit refused: ${target.displayPath} is ${(before.length / 1024).toFixed(0)} KB, ` +
        `over the ${MAX_BYTES / 1024} KB limit for returning a whole file reliably. ` +
        `Split the change, or edit the relevant section yourself.`
      )
    }

    let active: { key: string; profile: WorkerProfile }
    try {
      active = resolveProfile(config, config.writerProfile)
    } catch (error: any) {
      return `delegate_edit error: ${error.message}`
    }

    let result
    try {
      result = await callWorker(
        active.profile,
        SYSTEM_PROMPT,
        `FILE: ${target.displayPath}\nINSTRUCTION: ${args.instruction}\n\nCONTENT:\n${before}`,
        AbortSignal.timeout(300_000),
      )
    } catch (error: any) {
      await logTelemetry({
        tool: "delegate_edit",
        event: "worker-failed",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
        error: String(error?.message ?? error).slice(0, 300),
      })
      return `delegate_edit failed: the worker errored (${String(error?.message ?? error).slice(0, 200)}). ${target.displayPath} is untouched.`
    }

    const after = stripFences(result.text)
    const reject = async (event: string, message: string) => {
      await logTelemetry({
        tool: "delegate_edit",
        event,
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
        worker_model: active.profile.model,
        before_bytes: before.length,
        after_bytes: after.length,
      })
      return `${message}\n\n${target.displayPath} is untouched.`
    }

    if (after.startsWith("CANNOT:")) {
      return await reject(
        "worker-declined",
        `delegate_edit: the worker declined.\n\n${after.trim()}\n\nMake the change yourself, or restate the instruction mechanically.`,
      )
    }

    // The failure that would silently delete code, so it is checked before anything
    // reaches the disk. Models abbreviate long files however firmly told not to.
    if (looksAbbreviated(after)) {
      return await reject(
        "refused-abbreviated",
        `delegate_edit refused: the worker abbreviated the file instead of returning it whole, ` +
          `which would have deleted whatever it left out.`,
      )
    }
    if (after.length < before.length * MIN_RETURNED_FRACTION) {
      return await reject(
        "refused-truncated",
        `delegate_edit refused: the worker returned ${(after.length / 1024).toFixed(1)} KB against ` +
          `${(before.length / 1024).toFixed(1)} KB of original, so it dropped content rather than editing it.`,
      )
    }
    if (leaksSecret(after)) {
      return await reject(
        "refused-secret-in-output",
        `delegate_edit refused: the result contains something shaped like a real credential.`,
      )
    }
    if (after === before) {
      return await reject(
        "no-change",
        `delegate_edit: the worker returned the file unchanged, so the instruction matched nothing. ` +
          `Check the pattern you described exists here.`,
      )
    }

    const { diff, added, removed, editCost, available } = await unifiedDiff(before, after, target.displayPath)
    const totalLines = before.split("\n").length
    const changeFraction = (added + removed) / Math.max(totalLines, 1)

    if (changeFraction > MAX_CHANGE_FRACTION) {
      return await reject(
        "refused-rewrote",
        `delegate_edit refused: the diff touches ${added + removed} lines of a ${totalLines}-line file ` +
          `(${(changeFraction * 100).toFixed(0)}%), which is a rewrite rather than the edit asked for. ` +
          `This is what the check exists to catch: reformatting or restructuring smuggled in alongside ` +
          `a small change.`,
      )
    }

    const backedUp = await backup(target.displayPath, before)
    try {
      await fs.writeFile(target.absolute, after, "utf8")
    } catch (error: any) {
      return `delegate_edit failed: could not write ${target.displayPath} (${error.message}). It is unchanged.`
    }

    const syntax = await checkSyntax(worktree, target.absolute)

    // A failed parse is recoverable and the caller must be told plainly, but
    // leaving broken code on disk is worse than losing the work.
    if (syntax.status === "failed" && backedUp) {
      await fs.writeFile(target.absolute, before, "utf8")
      await logTelemetry({
        tool: "delegate_edit",
        event: "reverted-syntax-error",
        agent: context.agent,
        sessionID: context.sessionID,
        target: target.displayPath,
        worker_model: active.profile.model,
        detail: syntax.detail.slice(0, 200),
      })
      return (
        `delegate_edit failed: the edited file did not parse, so it was reverted.\n\n` +
        `${syntax.detail}\n\n` +
        `${target.displayPath} is back to its original contents. The rejected version is at ` +
        `${backedUp.replace(/__/, "__rejected__")} if you want to look. Make the change yourself.`
      )
    }

    // Full diff kept aside so the preview can stay small; that is the whole point.
    const id = `edit-${Date.now().toString(36)}-${path.basename(target.displayPath).replace(/[^A-Za-z0-9_-]/g, "")}`.slice(-80)
    let stored = false
    if (available && diff) {
      try {
        await fs.mkdir(OUTPUT_DIR, { recursive: true })
        await fs.writeFile(path.join(OUTPUT_DIR, `${id}.txt`), diff)
        stored = true
      } catch {
        // A missing artifact only costs the caller the full view.
      }
    }

    const diffLines = diff ? diff.split("\n") : []
    const preview = diffLines.slice(0, DIFF_PREVIEW_LINES).join("\n")
    const elided = Math.max(diffLines.length - DIFF_PREVIEW_LINES, 0)
    // The preview dominates the receipt; the rest is a fixed preamble and caveat.
    const receiptCost = preview.length + 600

    const receipt = [
      `delegate_edit: edited ${target.displayPath}, +${added}/-${removed} lines of ${totalLines},`,
      `by ${active.profile.model} (profile ${active.key}) in ${((Date.now() - startedAt) / 1000).toFixed(1)}s.`,
      `Making this change yourself would have cost about ${Math.round(editCost / 3.5).toLocaleString()} output tokens ` +
        `of search-and-replace; this receipt is ${Math.round(receiptCost / 3.5)}.`,
      "",
      syntax.status === "ok"
        ? `SYNTAX: ok (${syntax.detail})`
        : `SYNTAX: not checked (${syntax.detail}). Nothing has verified this edit yet.`,
      backedUp ? `Previous version kept at ${backedUp}` : `WARNING: no backup could be written.`,
      "",
      available ? "DIFF:" : "DIFF: git was unavailable, so only line counts are known.",
      preview,
      elided
        ? `\n[${elided} more diff lines]${stored ? ` Read the whole diff with read_output("${id}").` : ""}`
        : "",
      "",
      `Review the diff and run the tests. A file that parses is not a file that is correct, ` +
        `and nothing here proves the change means what you intended.`,
    ]
      .filter(Boolean)
      .join("\n")

    await logTelemetry({
      tool: "delegate_edit",
      agent: context.agent,
      sessionID: context.sessionID,
      target: target.displayPath,
      instruction: args.instruction.slice(0, 200),
      profile: active.key,
      worker_model: active.profile.model,
      // The pair that measures this tool. The orchestrator would have emitted the
      // changed lines twice over, as old text and new, to drive search-and-replace;
      // instead it received a receipt. Output bills at five times input.
      //
      // Stamped because the baseline changed: the first version charged a flat 40
      // chars per changed line, which ignored that search-and-replace pays for
      // surrounding context at every site, and understated the saving by an order
      // of magnitude. Rows without the stamp are not comparable and are excluded
      // rather than rewritten.
      metric_version: 2,
      written_chars: editCost,
      returned_chars: receipt.length,
      lines_added: added,
      lines_removed: removed,
      file_lines: totalLines,
      change_fraction: Number(changeFraction.toFixed(3)),
      syntax: syntax.status,
      diff_id: stored ? id : null,
      local_prompt_tokens: result.promptTokens,
      local_output_tokens: result.outputTokens,
      duration_ms: Date.now() - startedAt,
    })

    return {
      title: `delegate_edit: ${target.displayPath} (+${added}/-${removed}, syntax ${syntax.status})`,
      output: receipt,
      metadata: {
        path: target.displayPath,
        added,
        removed,
        syntax: syntax.status,
        durationMs: Date.now() - startedAt,
      },
    }
  },
})
