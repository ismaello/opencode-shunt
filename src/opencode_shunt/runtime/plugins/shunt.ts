import type { Plugin } from "@opencode-ai/plugin"
import fs from "node:fs/promises"
import path from "node:path"
import os from "node:os"
import { loadConfig, resolveProfile } from "../lib/worker"
import { summariseOutput } from "../lib/output-shunt"

// "enforce" blocks expensive reads, "observe" only records them.
const MODE = (process.env.SHUNT_MODE ?? "enforce") as "enforce" | "observe"

// Sessions cheap enough to read freely, which is the entire point of having them.
// Entries are either a provider ("ollama") or a specific model
// ("google/gemini-2.5-flash-lite"), because a worker is not necessarily local:
// the same role can be filled by a GPU here or a cheap cloud model.
const DEFAULT_EXEMPT = "ollama,lmstudio,llamacpp,local"

// Agents that must see the world exactly as it is regardless of their model.
// The benchmark control arm is only a control if nothing shunts it.
const EXEMPT_AGENTS = new Set(
  (process.env.SHUNT_EXEMPT_AGENTS ?? "benchmark-baseline").split(",").map((s) => s.trim()),
)

function buildExemptMatcher(worktreeExempt: string[] = []) {
  const entries = new Set([
    ...(process.env.SHUNT_BULK_EXEMPT ?? DEFAULT_EXEMPT).split(",").map((s) => s.trim()),
    ...worktreeExempt,
  ])
  entries.delete("")
  return (providerID: string, modelID: string, agent: string) =>
    EXEMPT_AGENTS.has(agent) ||
    entries.has(providerID) ||
    entries.has(`${providerID}/${modelID}`)
}

/** Read the bulkExempt list from .opencode/shunt.json, if there is one. */
async function loadExempt(worktree: string): Promise<string[]> {
  try {
    const raw = await fs.readFile(path.join(worktree, ".opencode", "shunt.json"), "utf8")
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed?.bulkExempt) ? parsed.bulkExempt : []
  } catch {
    return []
  }
}

const MAX_LINES = Number(process.env.SHUNT_MAX_LINES ?? 400)
const MAX_BYTES = Number(process.env.SHUNT_MAX_BYTES ?? 40_960)

// Output thresholds are separate from read thresholds: a 400-line file is worth
// summarising, a 400-line command output is often the answer itself.
const OUTPUT_MAX_LINES = Number(process.env.SHUNT_OUTPUT_MAX_LINES ?? 400)
const OUTPUT_MAX_BYTES = Number(process.env.SHUNT_OUTPUT_MAX_BYTES ?? 30_720)
const OUTPUT_TIMEOUT_MS = Number(process.env.SHUNT_OUTPUT_TIMEOUT_MS ?? 120_000)
// A read with an explicit window this size or smaller is precision work, not bulk.
const MAX_RANGE_LINES = 400

const BULK_COMMANDS = /^(cat|bat|less|more|head|tail|nl|strings)$/

type SessionInfo = { agent: string; providerID: string; modelID: string }

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

async function measure(file: string) {
  const stat = await fs.stat(file)
  if (!stat.isFile()) return null
  // Only count lines when the byte size alone is not already decisive.
  if (stat.size > MAX_BYTES) return { bytes: stat.size, lines: -1 }
  const text = await fs.readFile(file, "utf8")
  return { bytes: stat.size, lines: text.split("\n").length }
}

/** Extract file arguments from a shell command that would dump a whole file. */
function bulkTargets(command: string): string[] {
  const targets: string[] = []
  for (const segment of command.split(/\|\||&&|;|\|/)) {
    const tokens = segment.trim().split(/\s+/).filter(Boolean)
    if (!tokens.length) continue
    const binary = path.basename(tokens[0])
    if (!BULK_COMMANDS.test(binary)) continue

    // head/tail are fine when they ask for a small window.
    if (binary === "head" || binary === "tail") {
      const nFlag = /-n\s*(\d+)|^-(\d+)$/.exec(segment)
      const count = Number(nFlag?.[1] ?? nFlag?.[2] ?? 10)
      if (count <= MAX_RANGE_LINES) continue
    }

    for (const token of tokens.slice(1)) {
      if (token.startsWith("-")) continue
      if (/^\d+$/.test(token)) continue
      targets.push(token)
    }
  }
  return targets
}

export const ShuntPlugin: Plugin = async ({ worktree }) => {
  // tool.execute.before only receives { tool, sessionID, callID }, with no agent
  // or model. chat.params fires before every LLM call and does carry both, so we
  // build the mapping there and look it up when a tool runs. Subagents get their
  // own child sessionID, which is what keeps them out of the shunt.
  const sessions = new Map<string, SessionInfo>()
  const isExempt = buildExemptMatcher(await loadExempt(worktree))

  const guidance = (what: string, detail: string) =>
    new Error(
      `SHUNT: ${what} (${detail}).\n` +
        `This would burn frontier context on bulk reading. Instead:\n` +
        `  - bulk_read(question, paths) to answer a question about these files locally, or\n` +
        `  - @explorer if you do not yet know which files matter, or\n` +
        `  - read with offset/limit if you already know the exact lines you need.`,
    )

  return {
    "chat.params": async (input) => {
      sessions.set(input.sessionID, {
        agent: input.agent,
        providerID: input.model?.providerID ?? "unknown",
        modelID: input.model?.id ?? "unknown",
      })
    },

    "tool.execute.before": async (input, output) => {
      if (input.tool !== "read" && input.tool !== "bash") return

      const session = sessions.get(input.sessionID)
      // Fail open: an unknown session is more likely a startup race than an
      // attempt to dodge the shunt, and a false block is worse than a miss.
      if (!session) return
      if (isExempt(session.providerID, session.modelID, session.agent)) return

      const record = (verdict: string, extra: Record<string, unknown>) =>
        logTelemetry({
          tool: input.tool,
          agent: session.agent,
          provider: session.providerID,
          model: session.modelID,
          sessionID: input.sessionID,
          mode: MODE,
          verdict,
          ...extra,
        })

      if (input.tool === "read") {
        const filePath = output.args?.filePath
        if (typeof filePath !== "string") return

        const limit = Number(output.args?.limit ?? 0)
        if (limit > 0 && limit <= MAX_RANGE_LINES) {
          await record("allow-bounded", { file: filePath, limit })
          return
        }

        let size: Awaited<ReturnType<typeof measure>>
        try {
          size = await measure(path.resolve(worktree, filePath))
        } catch {
          return
        }
        if (!size) return

        const tooBig = size.bytes > MAX_BYTES || size.lines > MAX_LINES
        if (!tooBig) {
          await record("allow-small", { file: filePath, ...size })
          return
        }

        await record(MODE === "enforce" ? "block" : "observe-would-block", {
          file: filePath,
          ...size,
        })
        if (MODE === "enforce") {
          throw guidance(
            `refusing to read ${filePath} in full`,
            size.lines > 0 ? `${size.lines} lines` : `${(size.bytes / 1024).toFixed(0)} KB`,
          )
        }
        return
      }

      // bash: catch the obvious ways to dump a file without the read tool.
      const command = output.args?.command
      if (typeof command !== "string") return

      for (const target of bulkTargets(command)) {
        let size: Awaited<ReturnType<typeof measure>>
        try {
          size = await measure(path.resolve(worktree, target))
        } catch {
          continue
        }
        if (!size) continue
        if (size.bytes <= MAX_BYTES && size.lines <= MAX_LINES) continue

        await record(MODE === "enforce" ? "block" : "observe-would-block", {
          command: command.slice(0, 200),
          file: target,
          ...size,
        })
        if (MODE === "enforce") {
          throw guidance(
            `refusing to dump ${target} via shell`,
            size.lines > 0 ? `${size.lines} lines` : `${(size.bytes / 1024).toFixed(0)} KB`,
          )
        }
      }
    },

    // The read shunt keeps large files out of context; this keeps large tool
    // results out. One test run can cost more than the files it exercised.
    "tool.execute.after": async (input, output) => {
      if (input.tool !== "bash") return
      if (typeof output.output !== "string") return

      const lines = output.output.split("\n").length
      if (output.output.length <= OUTPUT_MAX_BYTES && lines <= OUTPUT_MAX_LINES) return

      const session = sessions.get(input.sessionID)
      if (!session) return
      if (isExempt(session.providerID, session.modelID, session.agent)) return

      const command = typeof input.args?.command === "string" ? input.args.command : "unknown"

      // In observe mode, record what would have happened and change nothing. The
      // summary is not even generated, so measuring costs nothing either.
      if (MODE !== "enforce") {
        await logTelemetry({
          event: "output-would-shunt",
          agent: session.agent,
          providerID: session.providerID,
          sessionID: input.sessionID,
          command: command.slice(0, 200),
          received_bytes: output.output.length,
          received_lines: lines,
        })
        return
      }

      const id = `${input.sessionID}-${input.callID}`.replace(/[^A-Za-z0-9_-]/g, "").slice(-80)

      try {
        const config = await loadConfig(worktree)
        const { key, profile } = resolveProfile(config)
        const summary = await summariseOutput({
          id,
          command,
          received: output.output,
          profile,
          profileKey: key,
          signal: AbortSignal.timeout(OUTPUT_TIMEOUT_MS),
        })

        await logTelemetry({
          event: "output-shunt",
          agent: session.agent,
          providerID: session.providerID,
          sessionID: input.sessionID,
          command: command.slice(0, 200),
          kind: summary.kind,
          profile: key,
          worker_model: summary.workerModel,
          // Distinguish what the hook was handed from the complete output, which
          // OpenCode may already have truncated before we saw it.
          received_bytes: output.output.length,
          received_lines: lines,
          raw_bytes: summary.rawBytes,
          raw_lines: summary.rawLines,
          recovered_full_output: summary.recovered,
          summary_bytes: summary.text.length,
          verbatim_kept: summary.verbatimKept,
          verbatim_dropped: summary.verbatimDropped,
          elided: summary.elided,
          noise_removed: summary.noiseRemoved,
          local_prompt_tokens: summary.promptTokens,
          duration_ms: summary.ms,
        })

        output.output = summary.text
        output.title = `${output.title} (shunted: ${lines} lines -> summary)`
      } catch (error: any) {
        // Fail open. Passing the raw output through costs tokens; replacing it
        // with an error would lose the result of work already done.
        await logTelemetry({
          event: "output-shunt-failed",
          sessionID: input.sessionID,
          command: command.slice(0, 200),
          raw_bytes: output.output.length,
          error: String(error?.message ?? error).slice(0, 300),
        })
      }
    },
  }
}
