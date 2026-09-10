import { tool } from "@opencode-ai/plugin"
import fs from "node:fs/promises"
import path from "node:path"
import { OUTPUT_DIR } from "../lib/output-shunt"

const MAX_LIMIT = 500

/**
 * Ids come from the output shunt, never from the user, so anything that is not a
 * plain token is either a mistake or an attempt to escape the artifact directory.
 */
const SAFE_ID = /^[A-Za-z0-9_-]{1,80}$/

async function listAvailable() {
  try {
    const entries = await fs.readdir(OUTPUT_DIR)
    return entries
      .filter((e) => e.endsWith(".txt") || e.endsWith(".json"))
      .map((e) => e.replace(/\.(txt|json)$/, ""))
  } catch {
    return []
  }
}

/**
 * An output is stored either as a copy (.txt) or, when OpenCode already holds
 * the complete text, as a pointer to its artifact (.json). The pointer avoids
 * keeping two copies of a quarter-megabyte of logs.
 */
async function loadArtifact(id: string): Promise<string | { error: string }> {
  try {
    return await fs.readFile(path.join(OUTPUT_DIR, `${id}.txt`), "utf8")
  } catch {
    // Not a stored copy; try the pointer.
  }
  let pointer: { path?: string }
  try {
    pointer = JSON.parse(await fs.readFile(path.join(OUTPUT_DIR, `${id}.json`), "utf8"))
  } catch {
    return { error: "missing" }
  }
  if (!pointer.path) return { error: "missing" }
  try {
    return await fs.readFile(pointer.path, "utf8")
  } catch {
    return {
      error: `the complete output was held at ${pointer.path}, which no longer exists. Re-run the command if you still need it.`,
    }
  }
}

export default tool({
  description: `Read a slice of a large tool output that the output shunt set aside.

When a command produces more output than fits comfortably in context, you receive a summary plus an id. Use this to read the exact part of the raw output you still need. It only opens outputs the shunt registered, never files in the repository; use read for those.

Always narrow down using the summary first. Reading the whole artifact defeats the purpose of it having been summarised.`,
  args: {
    id: tool.schema.string().describe("The id given in the output shunt message."),
    offset: tool.schema.number().describe("First line to return, 1-based."),
    limit: tool.schema
      .number()
      .describe(`How many lines to return. Required, maximum ${MAX_LIMIT}.`),
  },
  async execute(args) {
    if (!SAFE_ID.test(args.id)) {
      return `read_output error: "${args.id}" is not a valid output id.`
    }

    const loaded = await loadArtifact(args.id)
    if (typeof loaded !== "string") {
      if (loaded.error !== "missing") return `read_output error: ${loaded.error}`
      const available = await listAvailable()
      return (
        `read_output error: no stored output with id "${args.id}".\n` +
        (available.length
          ? `Available ids: ${available.slice(-10).join(", ")}`
          : "No outputs have been stored yet.")
      )
    }

    const lines = loaded.split("\n")
    const offset = Math.max(1, Math.floor(args.offset))
    const limit = Math.min(Math.max(1, Math.floor(args.limit)), MAX_LIMIT)
    const slice = lines.slice(offset - 1, offset - 1 + limit)

    if (!slice.length) {
      return `read_output: line ${offset} is past the end of output "${args.id}", which has ${lines.length} lines.`
    }

    const numbered = slice.map((line, i) => `${String(offset + i).padStart(6)}| ${line}`).join("\n")
    const lastShown = offset + slice.length - 1
    const footer =
      lastShown < lines.length
        ? `\n\n[lines ${offset}-${lastShown} of ${lines.length}. Continue at offset ${lastShown + 1}.]`
        : `\n\n[lines ${offset}-${lastShown} of ${lines.length}. End of output.]`

    return {
      title: `read_output ${args.id}: lines ${offset}-${lastShown}`,
      output: numbered + footer,
      metadata: { id: args.id, offset, limit, totalLines: lines.length },
    }
  },
})
