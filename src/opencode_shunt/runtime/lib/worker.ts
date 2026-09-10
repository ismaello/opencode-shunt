/**
 * Worker profiles: who does the bulk reading, and with what limits.
 *
 * The architecture has three roles (orchestrator, worker, exact search). This
 * module owns the worker role so that swapping a local GPU for a cloud model is
 * a config change, not a code change.
 */

import fs from "node:fs/promises"
import path from "node:path"
import { execFile } from "node:child_process"
import { promisify } from "node:util"

const execFileAsync = promisify(execFile)

export type WorkerProfile = {
  /**
   * "ollama" uses /api/chat. "openai-compatible" and "vertex" both use
   * /chat/completions; they differ only in how the request is authenticated.
   */
  kind: "ollama" | "openai-compatible" | "vertex"
  /** Required except for "vertex", where it is derived from project and location. */
  baseURL?: string
  model: string
  /** Name of the env var holding the API key. Omit for unauthenticated endpoints. */
  apiKeyEnv?: string
  /** Vertex only. */
  project?: string
  location?: string
  contextTokens: number
  costPerMillionInput?: number
  /**
   * Hard ceiling on one request, regardless of how much context the model has.
   * A million-token window means a stray binary diff would otherwise be sent in
   * full and billed in full.
   */
  maxCallChars?: number
  /**
   * How long an Ollama profile keeps its weights resident after a call. Set per
   * request so no change to the system service is needed. The default 5m expires
   * between normal questions and pays a ~5s reload on the next one.
   */
  keepAlive?: string
}

export type ShuntConfig = {
  profile: string
  profiles: Record<string, WorkerProfile>
  /** Profile used when content does not fit the active one. Omit to chunk instead. */
  overflowProfile?: string
  /** Providers allowed to see this repository. Absent means no restriction. */
  allowedProviders?: string[]
  /**
   * Profile for generating files rather than summarising them. Writing code is a
   * harder job than extraction, so it is worth pointing at a stronger model than
   * the one doing bulk reads. Falls back to the active profile.
   */
  writerProfile?: string
  /** Globs delegate_write may create. Absent means its built-in test-only default. */
  writePaths?: string[]
  /**
   * Globs delegate_edit may modify. Falls back to writePaths, then to the
   * test-only default, because changing code that already works and that people
   * rely on deserves at least the caution of creating a new test file.
   */
  editPaths?: string[]
  /**
   * Overrides for the delegation break-even. See lib/economics.ts; the two worth
   * setting per repository are how long conversations run and how many turns
   * read content survives, since both are habits rather than constants.
   */
  economics?: Partial<import("./economics").Economics>
}

/**
 * Last-resort fallback, used only when no configuration exists at all.
 *
 * Ollama at its default address, because it is the one worker that needs no
 * credentials and no account, so it is the only guess that can possibly work on
 * a machine we know nothing about. If it is not running, every tool fails with a
 * connection error naming this address, which is a far better outcome than
 * silently sending someone's code to a cloud they never chose.
 *
 * Run `shunt config` and none of this applies.
 */
const DEFAULT_CONFIG: ShuntConfig = {
  profile: "ollama-default",
  profiles: {
    "ollama-default": {
      kind: "ollama",
      baseURL: "http://127.0.0.1:11434",
      model: "qwen3-coder:30b",
      contextTokens: 32768,
      costPerMillionInput: 0,
    },
  },
}

const RESERVED_OUTPUT_TOKENS = 2048
const RESERVED_PROMPT_TOKENS = 1000
/** Line-numbered source measured at ~3.2 chars per token on qwen3-coder. */
const CHARS_PER_TOKEN = 3.2
/**
 * About 125k tokens. Million-token models make chunking unnecessary but also make
 * it easy to send, and pay for, far more than any question needs.
 */
const DEFAULT_MAX_CALL_CHARS = 400_000

export const TIMEOUT_MS = Number(process.env.SHUNT_TIMEOUT_MS ?? 300_000)

async function readConfigFile(file: string): Promise<Partial<ShuntConfig>> {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"))
  } catch (error: any) {
    // A missing file is normal. Malformed JSON is not, and silently falling back
    // to defaults would send work to a worker the user never chose.
    if (error.code === "ENOENT") return {}
    throw new Error(`${path.basename(file)} could not be read: ${error.message}`)
  }
}

/**
 * Configuration in layers, lowest priority first: built-in defaults, the shared
 * shunt.json, the machine's shunt.local.json, then environment variables.
 *
 * The split exists because the two kinds of setting have different lifetimes and
 * different audiences. Which paths may be generated, and which providers may see
 * the code, are decisions about the repository and belong in version control.
 * Which profile is active, the GCP project id and the address of an Ollama box
 * are facts about one machine, and committing them either leaks them or breaks
 * the next person to clone.
 */
export async function loadConfig(worktree: string): Promise<ShuntConfig> {
  const dir = path.join(worktree, ".opencode")
  const shared = await readConfigFile(path.join(dir, "shunt.json"))
  const local = await readConfigFile(path.join(dir, "shunt.local.json"))

  // Profiles merge one level deep, so a local file can set just "project" or
  // "baseURL" without restating the whole profile.
  const profiles: Record<string, WorkerProfile> = { ...DEFAULT_CONFIG.profiles }
  for (const layer of [shared.profiles, local.profiles]) {
    for (const [key, profile] of Object.entries(layer ?? {})) {
      profiles[key] = { ...profiles[key], ...profile }
    }
  }

  const merged: ShuntConfig = {
    ...DEFAULT_CONFIG,
    ...shared,
    ...local,
    profiles,
  }
  // Env overrides exist for benchmarking one profile against another.
  if (process.env.SHUNT_PROFILE) merged.profile = process.env.SHUNT_PROFILE
  if (process.env.SHUNT_MODEL && merged.profiles[merged.profile]) {
    merged.profiles[merged.profile] = {
      ...merged.profiles[merged.profile],
      model: process.env.SHUNT_MODEL,
    }
  }
  if (process.env.SHUNT_OLLAMA_URL && merged.profiles[merged.profile]) {
    merged.profiles[merged.profile] = {
      ...merged.profiles[merged.profile],
      baseURL: process.env.SHUNT_OLLAMA_URL,
    }
  }
  return merged
}

export function resolveProfile(config: ShuntConfig, name?: string) {
  const key = name ?? config.profile
  const profile = config.profiles[key]
  if (!profile) {
    throw new Error(
      `unknown worker profile "${key}". Available: ${Object.keys(config.profiles).join(", ")}`,
    )
  }
  if (config.allowedProviders && !config.allowedProviders.includes(providerOf(profile))) {
    throw new Error(
      `profile "${key}" uses provider "${providerOf(profile)}", which this repository does not allow`,
    )
  }
  return { key, profile }
}

/** Coarse provider identity, used only for the allowedProviders check. */
function providerOf(profile: WorkerProfile): string {
  if (profile.kind === "vertex") return "google"
  const host = (() => {
    try {
      return new URL(profile.baseURL ?? "").hostname
    } catch {
      return profile.baseURL ?? ""
    }
  })()
  if (host === "127.0.0.1" || host === "localhost") return "ollama"
  if (host.endsWith("googleapis.com")) return "google"
  if (host.endsWith("opencode.ai")) return "opencode"
  if (profile.kind === "ollama") return "ollama"
  return host
}

/** Vertex publishes an OpenAI-compatible surface under a per-project path. */
function vertexBaseURL(profile: WorkerProfile) {
  const project = profile.project ?? process.env.GOOGLE_CLOUD_PROJECT ?? process.env.GOOGLE_VERTEX_PROJECT
  const location = profile.location ?? process.env.VERTEX_LOCATION ?? "global"
  if (!project) {
    throw new Error("vertex profile needs a project, or GOOGLE_CLOUD_PROJECT in the environment")
  }
  const host =
    location === "global" ? "aiplatform.googleapis.com" : `${location}-aiplatform.googleapis.com`
  return `https://${host}/v1/projects/${project}/locations/${location}/endpoints/openapi`
}

let cachedToken: { value: string; expires: number } | null = null

/**
 * Bearer token for Vertex, taken from Application Default Credentials.
 *
 * ADC tokens are valid for an hour; refreshing at fifty minutes keeps a long
 * call from expiring halfway through. gcloud is not always on PATH inside the
 * editor, so the usual install locations are tried too.
 */
async function vertexToken(): Promise<string> {
  if (cachedToken && Date.now() < cachedToken.expires) return cachedToken.value

  const candidates = [
    process.env.GCLOUD_PATH,
    "gcloud",
    `${process.env.HOME}/google-cloud-sdk/bin/gcloud`,
    "/usr/lib/google-cloud-sdk/bin/gcloud",
    "/snap/bin/gcloud",
  ].filter(Boolean) as string[]

  let lastError = ""
  for (const binary of candidates) {
    try {
      const { stdout } = await execFileAsync(
        binary,
        ["auth", "application-default", "print-access-token"],
        { timeout: 30_000 },
      )
      const value = stdout.trim()
      if (!value) continue
      cachedToken = { value, expires: Date.now() + 50 * 60_000 }
      return value
    } catch (error: any) {
      lastError = String(error?.stderr || error?.message || error).slice(0, 200)
    }
  }
  throw new Error(
    `could not get a Vertex token from Application Default Credentials (${lastError}). ` +
      `Run: gcloud auth application-default login`,
  )
}

/** How much line-numbered text fits in one call to this profile. */
export function batchBudgetChars(profile: WorkerProfile): number {
  const fromContext = Math.floor(
    (profile.contextTokens - RESERVED_OUTPUT_TOKENS - RESERVED_PROMPT_TOKENS) * CHARS_PER_TOKEN,
  )
  return Math.min(fromContext, profile.maxCallChars ?? DEFAULT_MAX_CALL_CHARS)
}

export type WorkerResult = {
  text: string
  promptTokens: number
  outputTokens: number
  ms: number
  model: string
}

export async function callWorker(
  profile: WorkerProfile,
  system: string,
  user: string,
  signal: AbortSignal,
): Promise<WorkerResult> {
  const started = Date.now()
  const timeout = AbortSignal.any([signal, AbortSignal.timeout(TIMEOUT_MS)])

  if (profile.kind === "ollama") {
    const response = await fetch(`${profile.baseURL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: timeout,
      body: JSON.stringify({
        model: profile.model,
        stream: false,
        keep_alive: profile.keepAlive ?? "30m",
        // Keep this equal to the Modelfile: changing num_ctx forces a ~5s reload.
        options: { num_ctx: profile.contextTokens },
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
        ],
      }),
    })
    if (!response.ok) {
      throw new Error(`ollama ${response.status}: ${(await response.text()).slice(0, 200)}`)
    }
    const data: any = await response.json()
    return {
      text: String(data?.message?.content ?? "").trim(),
      promptTokens: Number(data?.prompt_eval_count ?? 0),
      outputTokens: Number(data?.eval_count ?? 0),
      ms: Date.now() - started,
      model: profile.model,
    }
  }

  // Vertex differs from a plain OpenAI-compatible endpoint only in where the URL
  // comes from, how the call is authenticated, and the "google/" model prefix.
  const isVertex = profile.kind === "vertex"
  const baseURL = isVertex ? vertexBaseURL(profile) : profile.baseURL
  if (!baseURL) throw new Error(`profile "${profile.model}" has no baseURL`)

  let bearer: string | undefined
  if (isVertex) {
    bearer = await vertexToken()
  } else if (profile.apiKeyEnv) {
    bearer = process.env[profile.apiKeyEnv]
    if (!bearer) throw new Error(`profile needs ${profile.apiKeyEnv} in the environment`)
  }

  const response = await fetch(`${baseURL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
    },
    signal: timeout,
    body: JSON.stringify({
      model: isVertex ? `google/${profile.model}` : profile.model,
      messages: [
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    }),
  })
  if (!response.ok) {
    throw new Error(`worker ${response.status}: ${(await response.text()).slice(0, 200)}`)
  }
  const data: any = await response.json()
  return {
    text: String(data?.choices?.[0]?.message?.content ?? "").trim(),
    promptTokens: Number(data?.usage?.prompt_tokens ?? 0),
    outputTokens: Number(data?.usage?.completion_tokens ?? 0),
    ms: Date.now() - started,
    model: profile.model,
  }
}
